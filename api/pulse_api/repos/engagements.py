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
            "select c.id::text, cl.name as name, c.engagement_name, "
            "c.brief, c.voice_enabled, c.created_at, c.last_active_at, "
            "o.logo_path as org_logo_path, o.branding as org_branding "
            "from public.engagements c "
            "join public.clients cl on cl.id = c.client_id "
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
    answer the operator's first-glance question: how far is this engagement?

    JOINs ``clients`` for the owning client (``client_id`` + ``client_name``)
    and carries the raw ``created_by`` user id. The owner's display name /
    email is enriched in a SECOND pass at the route layer against a
    BYPASSRLS session — ``users`` is not granted to ``pulse_member`` (same
    pattern the activity feed uses for actor display)."""
    result = await session.execute(
        text(
            """
            select
              c.id::text                                         as id,
              cl.id::text                                        as client_id,
              cl.name                                            as client_name,
              c.created_by::text                                 as created_by,
              c.engagement_name, c.token,
              c.brief, c.voice_enabled, c.created_at, c.last_active_at,
              coalesce(count(r.*) filter (where r.state = 'answered'), 0)::int as answered_count,
              coalesce(count(r.*) filter (where r.state = 'skipped'),  0)::int as skipped_count,
              (select count(*) from public.cards where engagement_id = c.id)::int  as total_cards
            from public.engagements c
            join public.clients cl on cl.id = c.client_id
            left join public.responses r on r.engagement_id = c.id
            group by c.id, cl.id, cl.name
            order by c.created_at desc
            """
        )
    )
    return [dict(r) for r in result.mappings().all()]


async def enrich_owner_display(
    admin_session: AsyncSession, rows: list[dict]
) -> list[dict]:
    """Fill ``owner_name`` / ``owner_email`` on engagement summary rows.

    ``list_all_with_counts`` runs on a ``pulse_member`` session which has
    no grant on ``users``, so it only carries the raw ``created_by`` id.
    This second pass resolves the display fields against a BYPASSRLS
    ``admin_session`` (the same two-pass pattern the activity feed uses).
    Mutates the rows in place (also returns them for convenience). Rows
    with a NULL ``created_by`` — or whose user was removed — get
    ``owner_name = owner_email = None``.
    """
    from pulse_api.repos import memberships as memberships_repo

    owner_ids = sorted(
        {str(r["created_by"]) for r in rows if r.get("created_by") is not None}
    )
    owner_map = (
        await memberships_repo.list_user_display_fields(admin_session, owner_ids)
        if owner_ids
        else {}
    )
    for r in rows:
        cb = r.get("created_by")
        u = owner_map.get(str(cb)) if cb is not None else None
        r["owner_name"] = (str(u["name"]) if u and u.get("name") else None)
        r["owner_email"] = (str(u["email"]) if u and u.get("email") else None)
    return rows


async def get_by_id(session: AsyncSession, engagement_id: str) -> dict | None:
    try:
        result = await session.execute(
            text(
                "select c.id::text, c.client_id::text as client_id, "
                "cl.name as name, c.engagement_name, c.token, "
                "c.brief, c.voice_enabled, c.created_by::text as created_by, "
                "c.created_at, c.last_active_at "
                "from public.engagements c "
                "join public.clients cl on cl.id = c.client_id "
                "where c.id = cast(:cid as uuid)"
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
    client_id: str,
    engagement_name: str | None,
    org_id: str,
    created_by: str | None,
) -> dict:
    """Insert a new engagement under ``client_id`` and return its row.

    The client owns the customer-facing name now, so this no longer takes
    a ``name`` — the caller resolves ``client_id`` first (via
    ``clients.get_or_create`` or an existing id). The returned ``name`` is
    the client's name, joined back so the create response keeps the same
    shape the admin API already returns.

    Args:
        session: DB session (``pulse_member`` role).
        client_id: Owning client UUID (already resolved/verified in-org).
        engagement_name: Optional engagement label.
        org_id: Owning organization UUID — NOT NULL on the column.
        created_by: User who created the engagement (the active operator),
            or ``None``.

    Returns:
        Dict of the inserted row, including the joined client ``name`` +
        ``client_id``.
    """
    token = secrets.token_hex(8)
    result = await session.execute(
        text(
            "with ins as ("
            "  insert into public.engagements "
            "  (client_id, engagement_name, token, org_id, created_by) "
            "  values (cast(:cid as uuid), :e, :t, cast(:org as uuid), "
            "          cast(:by as uuid)) "
            "  returning id, client_id, engagement_name, token, brief, "
            "            voice_enabled, created_by, created_at, last_active_at"
            ") "
            "select ins.id::text, ins.client_id::text as client_id, "
            "cl.name as name, cl.name as client_name, "
            "ins.engagement_name, ins.token, "
            "ins.brief, ins.voice_enabled, ins.created_by::text as created_by, "
            "ins.created_at, ins.last_active_at "
            "from ins join public.clients cl on cl.id = ins.client_id"
        ),
        {
            "cid": client_id,
            "e": engagement_name,
            "t": token,
            "org": org_id,
            "by": created_by,
        },
    )
    return dict(result.mappings().one())


async def update_engagement(
    session: AsyncSession, engagement_id: str, fields: dict
) -> dict | None:
    """Partial update — only the keys in `fields` get written. Caller is
    responsible for restricting which keys it forwards (so the wire body
    can't sneak in a token rotation by sending {'token': '...'}).

    All writable fields (``engagement_name``, ``brief``, ``voice_enabled``)
    are plain columns. The customer-facing name lives
    on ``clients`` now and is NOT mutable through this path. The returned
    row joins ``clients`` so ``name`` + ``client_id`` stay present."""
    if not fields:
        return await get_by_id(session, engagement_id)
    set_clauses = ", ".join(f"{k} = :{k}" for k in fields)
    params = {"cid": engagement_id, **fields}
    try:
        await session.execute(
            text(
                f"update public.engagements set {set_clauses} "
                f"where id = cast(:cid as uuid)"
            ),
            params,
        )
    except Exception:
        return None
    return await get_by_id(session, engagement_id)


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
