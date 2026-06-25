"""Repository helpers for `responses`. RLS restricts everything to the
token's recipient, and inserts derive recipient_id + engagement_id
server-side (`pulse_request_recipient_id()` / `pulse_request_engagement_id()`)
so the wire body can't pretend to be another recipient. The answer is
unique on `(card_id, recipient_id)`, so two recipients on the same
engagement answer the same card independently."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_for_my_engagement(session: AsyncSession) -> list[dict]:
    result = await session.execute(
        text(
            "select id::text, card_id::text, engagement_id::text, recipient_id::text, "
            "state, response_value, viewed_at, answered_at, created_at, updated_at "
            "from public.responses"
        )
    )
    return [dict(r) for r in result.mappings().all()]


async def _card_belongs_to_caller(session: AsyncSession, card_id: str) -> bool:
    """RLS filters `cards` by engagement_id automatically — if no row comes
    back, the card either doesn't exist or belongs to another engagement.
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
    isn't visible to the token's engagement (404 at the route layer)."""
    if not await _card_belongs_to_caller(session, card_id):
        return None

    result = await session.execute(
        text(
            "insert into public.responses "
            "(card_id, engagement_id, recipient_id, state, viewed_at) "
            "values (cast(:cid as uuid), public.pulse_request_engagement_id(), "
            "public.pulse_request_recipient_id(), 'viewed', now()) "
            "on conflict (card_id, recipient_id) do nothing "
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
    """Insert or update the response for (card_id, current engagement). For
    answered/skipped/needs_edit states this sets `answered_at = now()`; for
    'viewed' it sets `viewed_at = now()` and leaves `answered_at` alone."""
    if not await _card_belongs_to_caller(session, card_id):
        return None

    set_answered = state in ("answered", "skipped", "needs_edit")
    result = await session.execute(
        text(
            f"""
            insert into public.responses
              (card_id, engagement_id, recipient_id, state, response_value,
               viewed_at, answered_at)
            values
              (cast(:cid as uuid), public.pulse_request_engagement_id(),
               public.pulse_request_recipient_id(), :state,
               cast(:rv as jsonb),
               {"now()" if state == "viewed" else "null"},
               {"now()" if set_answered else "null"})
            on conflict (card_id, recipient_id) do update set
              state = excluded.state,
              response_value = excluded.response_value,
              answered_at = coalesce(excluded.answered_at, public.responses.answered_at),
              viewed_at = coalesce(public.responses.viewed_at, excluded.viewed_at)
            returning id::text, card_id::text, engagement_id::text, recipient_id::text,
                      state, response_value, viewed_at, answered_at, created_at, updated_at
            """
        ),
        {"cid": card_id, "state": state, "rv": _json_dump(response_value)},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


def _json_dump(value: dict | None) -> str | None:
    import json
    return json.dumps(value) if value is not None else None


# ── admin-mode helpers (BYPASSRLS — explicit engagement_id filters) ────────


async def list_for_engagement(session: AsyncSession, engagement_id: str) -> list[dict]:
    try:
        result = await session.execute(
            text(
                "select id::text, card_id::text, engagement_id::text, recipient_id::text, "
                "state, response_value, viewed_at, answered_at, created_at, updated_at "
                "from public.responses where engagement_id = cast(:cid as uuid) "
                "order by created_at"
            ),
            {"cid": engagement_id},
        )
    except Exception:
        return []
    return [dict(r) for r in result.mappings().all()]


async def delete_all_for_engagement(session: AsyncSession, engagement_id: str) -> int:
    """Admin reset: wipe every response for one engagement so the deck
    restarts clean. Returns the number of rows removed. BYPASSRLS session
    with an explicit engagement_id filter."""
    result = await session.execute(
        text("delete from public.responses where engagement_id = cast(:cid as uuid)"),
        {"cid": engagement_id},
    )
    return result.rowcount or 0
