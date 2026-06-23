"""Repository helpers for `engagements`. Caller's session role determines
what rows are visible: `pulse_anon` sees only the row matching the
request's token; `pulse_admin` (BYPASSRLS) sees all rows.
"""
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_my_engagement(session: AsyncSession) -> dict | None:
    """RLS narrows this to the token-bound engagement. Returns None on no match.

    LEFT JOINs the owning organization so the bootstrap payload carries
    the org's logo + branding alongside the engagement. The anon SELECT
    policy on ``organizations`` (added in migration 0007) admits exactly
    the row whose ``id`` matches the ``pulse.org_id`` GUC the request set,
    so the join stays tenant-scoped. ``org_logo_path`` / ``org_branding``
    are ``None`` when the org has no logo / branding configured.
    """
    result = await session.execute(
        text(
            "select c.id::text, c.name, c.org_name, c.engagement_name, "
            "c.brief, c.voice_enabled, c.created_at, c.last_active_at, "
            "o.logo_path as org_logo_path, o.branding as org_branding "
            "from public.engagements c "
            "left join public.organizations o on o.id = c.org_id "
            "limit 1"
        )
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_my_org_logo_path(session: AsyncSession) -> str | None:
    """Return the owning org's current ``logo_path`` for the token's engagement.

    Runs on the ``pulse_anon`` session. RLS narrows ``engagements`` to the
    token-bound row, and the anon SELECT policy on ``organizations``
    (migration 0007) admits only the matching org row, so a missing or
    cross-tenant org yields ``None``.

    Args:
        session: ``pulse_anon`` session with ``pulse.token`` +
            ``pulse.org_id`` set.

    Returns:
        Relative ``logo_path`` under ``settings.upload_dir``, or ``None``
        when the org has no logo (or no resolvable org).
    """
    result = await session.execute(
        text(
            "select o.logo_path "
            "from public.engagements c "
            "join public.organizations o on o.id = c.org_id "
            "limit 1"
        )
    )
    return result.scalar_one_or_none()


async def voice_enabled_for_my_engagement(session: AsyncSession) -> bool:
    """Return whether voice recording is enabled for the token-bound engagement.

    Runs on the ``pulse_anon`` session. This is the security gate the upload
    route consults before accepting a ``kind='voice'`` upload, so it filters
    on ``pulse_request_token()`` EXPLICITLY (like ``touch_last_active``)
    rather than leaning on RLS alone — the flag is then correct even if RLS
    were ever not engaged. Returns ``False`` when no engagement resolves
    (unknown token) — the upload route treats that as "voice not allowed",
    the safe default.

    Args:
        session: ``pulse_anon`` session with ``pulse.token`` set.

    Returns:
        The engagement's ``voice_enabled`` flag, or ``False`` when no row
        resolves.
    """
    result = await session.execute(
        text(
            "select voice_enabled from public.engagements "
            "where token = public.pulse_request_token() limit 1"
        )
    )
    return bool(result.scalar_one_or_none())


async def touch_last_active(session: AsyncSession) -> bool:
    """Updates last_active_at on the token-bound engagement. Returns True if
    a row was updated. RLS + the column-scoped grant restrict this to the
    token's own row and to that one column."""
    result = await session.execute(
        text(
            "update public.engagements set last_active_at = now() "
            "where token = public.pulse_request_token() returning id"
        )
    )
    return result.rowcount > 0


# ── admin-mode helpers (BYPASSRLS — explicit engagement_id filters) ────────


async def list_all_with_counts(session: AsyncSession) -> list[dict]:
    """All engagements + per-engagement aggregates. The two FILTER counts
    answer the operator's first-glance question: how far is this engagement?"""
    result = await session.execute(
        text(
            """
            select
              c.id::text                                         as id,
              c.name, c.org_name, c.engagement_name, c.token,
              c.brief, c.voice_enabled, c.created_at, c.last_active_at,
              c.group_id::text                                   as group_id,
              g.name                                             as group_name,
              coalesce(count(r.*) filter (where r.state = 'answered'), 0)::int as answered_count,
              coalesce(count(r.*) filter (where r.state = 'skipped'),  0)::int as skipped_count,
              (select count(*) from public.cards where engagement_id = c.id)::int  as total_cards
            from public.engagements c
            left join public.responses r on r.engagement_id = c.id
            left join public.engagement_groups g on g.id = c.group_id
            group by c.id, g.name
            order by c.created_at desc
            """
        )
    )
    return [dict(r) for r in result.mappings().all()]


async def get_by_id(session: AsyncSession, engagement_id: str) -> dict | None:
    try:
        result = await session.execute(
            text(
                "select id::text, name, org_name, engagement_name, token, brief, "
                "voice_enabled, group_id::text as group_id, "
                "created_at, last_active_at from public.engagements "
                "where id = cast(:cid as uuid)"
            ),
            {"cid": engagement_id},
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def create_engagement(
    session: AsyncSession,
    *,
    name: str,
    org_name: str | None,
    engagement_name: str | None,
    org_id: str,
) -> dict:
    """Insert a new engagement and return its row.

    Args:
        session: DB session (admin role in PR 1, member role in PR 2).
        name: Customer-facing name.
        org_name: Legacy customer-org text column (free-form).
        engagement_name: Optional engagement label.
        org_id: Owning organization UUID — NOT NULL on the column.

    Returns:
        Dict of the inserted row with the same keys the admin API
        already returns for engagement rows.
    """
    token = secrets.token_hex(8)
    result = await session.execute(
        text(
            "insert into public.engagements "
            "(name, org_name, engagement_name, token, org_id) "
            "values (:n, :o, :e, :t, cast(:org as uuid)) "
            "returning id::text, name, org_name, engagement_name, token, brief, voice_enabled, "
            "group_id::text as group_id, created_at, last_active_at"
        ),
        {
            "n": name,
            "o": org_name,
            "e": engagement_name,
            "t": token,
            "org": org_id,
        },
    )
    return dict(result.mappings().one())


async def update_engagement(
    session: AsyncSession, engagement_id: str, fields: dict
) -> dict | None:
    """Partial update — only the keys in `fields` get written. Caller is
    responsible for restricting which keys it forwards (so the wire body
    can't sneak in a token rotation by sending {'token': '...'}).

    ``group_id`` is special-cased: it's a uuid column, so its bind is
    cast explicitly. Passing ``group_id=None`` ungroups the engagement
    (moves it to the implicit "Ungrouped" bucket)."""
    if not fields:
        return await get_by_id(session, engagement_id)
    # group_id is a uuid column — cast the bind so a NULL or a string id
    # both bind cleanly. Every other field is plain text.
    set_clauses = ", ".join(
        f"{k} = cast(:{k} as uuid)" if k == "group_id" else f"{k} = :{k}"
        for k in fields
    )
    params = {"cid": engagement_id, **fields}
    try:
        result = await session.execute(
            text(
                f"update public.engagements set {set_clauses} "
                f"where id = cast(:cid as uuid) "
                f"returning id::text, name, org_name, engagement_name, token, brief, voice_enabled, "
                f"group_id::text as group_id, created_at, last_active_at"
            ),
            params,
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def list_upload_paths_for_engagement(
    session: AsyncSession, engagement_id: str
) -> list[str]:
    """Fetch storage_path values BEFORE deleting the engagement so the
    route layer knows which on-disk files to remove after the FK cascade
    wipes the uploads rows."""
    try:
        result = await session.execute(
            text(
                "select storage_path from public.uploads "
                "where engagement_id = cast(:cid as uuid)"
            ),
            {"cid": engagement_id},
        )
    except Exception:
        return []
    return [row[0] for row in result.all()]


async def delete_engagement(session: AsyncSession, engagement_id: str) -> bool:
    """Delete an engagement and let FK cascades wipe its cards, responses,
    and uploads. Returns True if a row was deleted. The caller is
    responsible for removing the on-disk upload files — fetch the paths
    via `list_upload_paths_for_engagement` *before* this call."""
    try:
        result = await session.execute(
            text("delete from public.engagements where id = cast(:cid as uuid)"),
            {"cid": engagement_id},
        )
    except Exception:
        return False
    return result.rowcount > 0
