"""Repository helpers for `responses`. RLS restricts everything to the
token's client, and inserts derive client_id server-side from
`pulse_request_client_id()` so the wire body can't pretend to be another
client."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_for_my_client(session: AsyncSession) -> list[dict]:
    result = await session.execute(
        text(
            "select id::text, card_id::text, client_id::text, state, response_value, "
            "viewed_at, answered_at, created_at, updated_at "
            "from public.responses"
        )
    )
    return [dict(r) for r in result.mappings().all()]


async def _card_belongs_to_caller(session: AsyncSession, card_id: str) -> bool:
    """RLS filters `cards` by client_id automatically — if no row comes
    back, the card either doesn't exist or belongs to another client.
    Treat both the same way (404) so we don't leak existence."""
    try:
        result = await session.execute(
            text("select 1 from public.cards where id = cast(:cid as uuid)"),
            {"cid": card_id},
        )
    except Exception:
        # Malformed UUID, etc. — same answer.
        return False
    return result.scalar() is not None


async def mark_viewed(session: AsyncSession, card_id: str) -> dict | None:
    """Insert a viewed row if none exists; otherwise leave the existing
    row alone. Returns the row's current state, or None if the card
    isn't visible to the token's client (404 at the route layer)."""
    if not await _card_belongs_to_caller(session, card_id):
        return None

    result = await session.execute(
        text(
            "insert into public.responses (card_id, client_id, state, viewed_at) "
            "values (cast(:cid as uuid), public.pulse_request_client_id(), 'viewed', now()) "
            "on conflict (card_id, client_id) do nothing "
            "returning id::text, card_id::text, state, viewed_at"
        ),
        {"cid": card_id},
    )
    row = result.mappings().one_or_none()
    if row is not None:
        return dict(row)
    # Conflict path — read the existing row.
    existing = await session.execute(
        text(
            "select id::text, card_id::text, state, viewed_at "
            "from public.responses where card_id = cast(:cid as uuid)"
        ),
        {"cid": card_id},
    )
    row = existing.mappings().one_or_none()
    return dict(row) if row else None


async def upsert_answer(
    session: AsyncSession,
    *,
    card_id: str,
    state: str,
    response_value: dict | None,
) -> dict | None:
    """Insert or update the response for (card_id, current client). For
    answered/skipped/needs_edit states this sets `answered_at = now()`; for
    'viewed' it sets `viewed_at = now()` and leaves `answered_at` alone."""
    if not await _card_belongs_to_caller(session, card_id):
        return None

    set_answered = state in ("answered", "skipped", "needs_edit")
    result = await session.execute(
        text(
            f"""
            insert into public.responses
              (card_id, client_id, state, response_value, viewed_at, answered_at)
            values
              (cast(:cid as uuid), public.pulse_request_client_id(), :state,
               cast(:rv as jsonb),
               {"now()" if state == "viewed" else "null"},
               {"now()" if set_answered else "null"})
            on conflict (card_id, client_id) do update set
              state = excluded.state,
              response_value = excluded.response_value,
              answered_at = coalesce(excluded.answered_at, public.responses.answered_at),
              viewed_at = coalesce(public.responses.viewed_at, excluded.viewed_at)
            returning id::text, card_id::text, client_id::text, state, response_value,
                      viewed_at, answered_at, created_at, updated_at
            """
        ),
        {"cid": card_id, "state": state, "rv": _json_dump(response_value)},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


def _json_dump(value: dict | None) -> str | None:
    import json
    return json.dumps(value) if value is not None else None


# ── admin-mode helpers (BYPASSRLS — explicit client_id filters) ────────────


async def set_clickup_status_by_card(
    session: AsyncSession, card_id: str, clickup_status: str
) -> bool:
    """Webhook-driven update: cache the latest upstream ClickUp status on
    the response for the given card. Returns True if a response row was
    updated. Admin-only (BYPASSRLS — webhook has no token context)."""
    result = await session.execute(
        text(
            "update public.responses "
            "set clickup_status = :s, clickup_status_updated_at = now() "
            "where card_id = cast(:cid as uuid) returning id"
        ),
        {"s": clickup_status, "cid": card_id},
    )
    return result.rowcount > 0


async def list_for_client(session: AsyncSession, client_id: str) -> list[dict]:
    try:
        result = await session.execute(
            text(
                "select id::text, card_id::text, client_id::text, state, response_value, "
                "viewed_at, answered_at, created_at, updated_at "
                "from public.responses where client_id = cast(:cid as uuid) "
                "order by created_at"
            ),
            {"cid": client_id},
        )
    except Exception:
        return []
    return [dict(r) for r in result.mappings().all()]


async def admin_list_for_client(session: AsyncSession, client_id: str) -> list[dict]:
    """Same as list_for_client but additionally returns the ClickUp
    status fields. The pulse_anon role has no grants on those columns,
    so this variant is only callable from the admin (BYPASSRLS) session."""
    try:
        result = await session.execute(
            text(
                "select id::text, card_id::text, client_id::text, state, response_value, "
                "viewed_at, answered_at, created_at, updated_at, "
                "clickup_status, clickup_status_updated_at "
                "from public.responses where client_id = cast(:cid as uuid) "
                "order by created_at"
            ),
            {"cid": client_id},
        )
    except Exception:
        return []
    return [dict(r) for r in result.mappings().all()]
