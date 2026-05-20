"""Repository helpers for `clients`. Caller's session role determines what
rows are visible: `pulse_anon` sees only the row matching the request's
token; `pulse_admin` (BYPASSRLS) sees all rows.
"""
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_my_client(session: AsyncSession) -> dict | None:
    """RLS narrows this to the token-bound client. Returns None on no match."""
    result = await session.execute(
        text(
            "select id::text, name, org_name, engagement_name, brief, "
            "created_at, last_active_at "
            "from public.clients limit 1"
        )
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


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
              coalesce(count(r.*) filter (where r.state = 'answered'), 0)::int as answered_count,
              coalesce(count(r.*) filter (where r.state = 'skipped'),  0)::int as skipped_count,
              (select count(*) from public.cards where client_id = c.id)::int  as total_cards
            from public.clients c
            left join public.responses r on r.client_id = c.id
            group by c.id
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
) -> dict:
    token = secrets.token_hex(8)
    result = await session.execute(
        text(
            "insert into public.clients (name, org_name, engagement_name, token) "
            "values (:n, :o, :e, :t) "
            "returning id::text, name, org_name, engagement_name, token, brief, "
            "created_at, last_active_at"
        ),
        {"n": name, "o": org_name, "e": engagement_name, "t": token},
    )
    return dict(result.mappings().one())


async def update_engagement(
    session: AsyncSession, client_id: str, fields: dict
) -> dict | None:
    """Partial update — only the keys in `fields` get written. Caller is
    responsible for restricting which keys it forwards (so the wire body
    can't sneak in a token rotation by sending {'token': '...'})."""
    if not fields:
        return await get_by_id(session, client_id)
    set_clauses = ", ".join(f"{k} = :{k}" for k in fields)
    params = {"cid": client_id, **fields}
    try:
        result = await session.execute(
            text(
                f"update public.clients set {set_clauses} "
                f"where id = cast(:cid as uuid) "
                f"returning id::text, name, org_name, engagement_name, token, brief, "
                f"created_at, last_active_at"
            ),
            params,
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def rotate_token(session: AsyncSession, client_id: str) -> dict | None:
    """Generate a fresh 16-hex token. The old URL stops working immediately."""
    new_token = secrets.token_hex(8)
    try:
        result = await session.execute(
            text(
                "update public.clients set token = :t "
                "where id = cast(:cid as uuid) "
                "returning id::text, name, org_name, engagement_name, token, brief, "
                "created_at, last_active_at"
            ),
            {"t": new_token, "cid": client_id},
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None
