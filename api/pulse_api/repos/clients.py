"""Repository helpers for `clients`. Caller's session role determines what
rows are visible: `pulse_anon` sees only the row matching the request's
token; `pulse_admin` (BYPASSRLS) sees all rows.
"""
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_my_client(session: AsyncSession) -> dict | None:
    """RLS narrows this to the token-bound client. Returns None on no match.

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
            "c.brief, c.created_at, c.last_active_at, "
            "o.logo_path as org_logo_path, o.branding as org_branding "
            "from public.clients c "
            "left join public.organizations o on o.id = c.org_id "
            "limit 1"
        )
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_my_org_logo_path(session: AsyncSession) -> str | None:
    """Return the owning org's current ``logo_path`` for the token's client.

    Runs on the ``pulse_anon`` session. RLS narrows ``clients`` to the
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
            "from public.clients c "
            "join public.organizations o on o.id = c.org_id "
            "limit 1"
        )
    )
    return result.scalar_one_or_none()


async def touch_last_active(session: AsyncSession) -> bool:
    """Updates last_active_at on the token-bound client. Returns True if a
    row was updated. RLS + the column-scoped grant restrict this to the
    token's own row and to that one column."""
    result = await session.execute(
        text(
            "update public.clients set last_active_at = now() "
            "where token = public.pulse_request_token() returning id"
        )
    )
    return result.rowcount > 0


# ── admin-mode helpers (BYPASSRLS — explicit client_id filters) ────────────


async def list_all_with_counts(session: AsyncSession) -> list[dict]:
    """All engagements + per-client aggregates. The two FILTER counts
    answer the operator's first-glance question: how far is this client?"""
    result = await session.execute(
        text(
            """
            select
              c.id::text                                         as id,
              c.name, c.org_name, c.engagement_name, c.token,
              c.brief, c.created_at, c.last_active_at,
              c.group_id::text                                   as group_id,
              g.name                                             as group_name,
              coalesce(count(r.*) filter (where r.state = 'answered'), 0)::int as answered_count,
              coalesce(count(r.*) filter (where r.state = 'skipped'),  0)::int as skipped_count,
              (select count(*) from public.cards where client_id = c.id)::int  as total_cards
            from public.clients c
            left join public.responses r on r.client_id = c.id
            left join public.engagement_groups g on g.id = c.group_id
            group by c.id, g.name
            order by c.created_at desc
            """
        )
    )
    return [dict(r) for r in result.mappings().all()]


async def get_by_id(session: AsyncSession, client_id: str) -> dict | None:
    try:
        result = await session.execute(
            text(
                "select id::text, name, org_name, engagement_name, token, brief, "
                "group_id::text as group_id, "
                "created_at, last_active_at from public.clients "
                "where id = cast(:cid as uuid)"
            ),
            {"cid": client_id},
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
        already returns for client rows.
    """
    token = secrets.token_hex(8)
    result = await session.execute(
        text(
            "insert into public.clients "
            "(name, org_name, engagement_name, token, org_id) "
            "values (:n, :o, :e, :t, cast(:org as uuid)) "
            "returning id::text, name, org_name, engagement_name, token, brief, "
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
    session: AsyncSession, client_id: str, fields: dict
) -> dict | None:
    """Partial update — only the keys in `fields` get written. Caller is
    responsible for restricting which keys it forwards (so the wire body
    can't sneak in a token rotation by sending {'token': '...'}).

    ``group_id`` is special-cased: it's a uuid column, so its bind is
    cast explicitly. Passing ``group_id=None`` ungroups the engagement
    (moves it to the implicit "Ungrouped" bucket)."""
    if not fields:
        return await get_by_id(session, client_id)
    # group_id is a uuid column — cast the bind so a NULL or a string id
    # both bind cleanly. Every other field is plain text.
    set_clauses = ", ".join(
        f"{k} = cast(:{k} as uuid)" if k == "group_id" else f"{k} = :{k}"
        for k in fields
    )
    params = {"cid": client_id, **fields}
    try:
        result = await session.execute(
            text(
                f"update public.clients set {set_clauses} "
                f"where id = cast(:cid as uuid) "
                f"returning id::text, name, org_name, engagement_name, token, brief, "
                f"group_id::text as group_id, created_at, last_active_at"
            ),
            params,
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def list_upload_paths_for_client(
    session: AsyncSession, client_id: str
) -> list[str]:
    """Fetch storage_path values BEFORE deleting the engagement so the
    route layer knows which on-disk files to remove after the FK cascade
    wipes the uploads rows."""
    try:
        result = await session.execute(
            text(
                "select storage_path from public.uploads "
                "where client_id = cast(:cid as uuid)"
            ),
            {"cid": client_id},
        )
    except Exception:
        return []
    return [row[0] for row in result.all()]


async def delete_engagement(session: AsyncSession, client_id: str) -> bool:
    """Delete an engagement and let FK cascades wipe its cards, responses,
    and uploads. Returns True if a row was deleted. The caller is
    responsible for removing the on-disk upload files — fetch the paths
    via `list_upload_paths_for_client` *before* this call."""
    try:
        result = await session.execute(
            text("delete from public.clients where id = cast(:cid as uuid)"),
            {"cid": client_id},
        )
    except Exception:
        return False
    return result.rowcount > 0


async def rotate_token(session: AsyncSession, client_id: str) -> dict | None:
    """Generate a fresh 16-hex token. The old URL stops working immediately."""
    new_token = secrets.token_hex(8)
    try:
        result = await session.execute(
            text(
                "update public.clients set token = :t "
                "where id = cast(:cid as uuid) "
                "returning id::text, name, org_name, engagement_name, token, brief, "
                "group_id::text as group_id, created_at, last_active_at"
            ),
            {"t": new_token, "cid": client_id},
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None
