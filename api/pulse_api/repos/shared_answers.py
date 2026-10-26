"""Repository helpers for shared answers + their audit trail (migration 0023).

The embed API calls the read/write helpers on the BYPASSRLS admin engine
with an engagement id it resolved from the respondent's token on the
RLS-scoped anon session — so every query here filters on that engagement id
explicitly. Admin reads run on the member session (org-scoped RLS).
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def current_for_engagement(
    session: AsyncSession, engagement_id: str
) -> dict[str, dict[str, Any]]:
    """``{card_id: {value, updated_by_name, updated_at}}`` for one engagement."""
    rows = await session.execute(
        text(
            "select s.card_id::text as card_id, s.value, s.updated_at, "
            "       coalesce(r.name, r.email) as updated_by_name "
            "from public.shared_answers s "
            "left join public.recipients r on r.id = s.updated_by "
            "where s.engagement_id = cast(:e as uuid)"
        ),
        {"e": engagement_id},
    )
    return {r["card_id"]: dict(r) for r in rows.mappings()}


async def get_value(session: AsyncSession, card_id: str) -> dict[str, Any] | None:
    """The card's current shared answer, taking a transaction-scoped lock on
    the card first so two simultaneous saves merge in turn instead of one
    losing the other's grid cells."""
    await session.execute(
        text("select pg_advisory_xact_lock(hashtext(:c))"), {"c": f"shared:{card_id}"}
    )
    row = await session.execute(
        text("select value from public.shared_answers where card_id = cast(:c as uuid)"),
        {"c": card_id},
    )
    return row.scalar_one_or_none()


async def save(
    session: AsyncSession,
    *,
    card_id: str,
    engagement_id: str,
    org_id: str,
    recipient_id: str,
    actor_label: str,
    change: dict[str, Any],
    new_value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Set the card's shared answer to ``new_value`` (None clears it) and log
    ``change`` as one audit event. Returns the stored row, or None if
    cleared. The caller commits."""
    if new_value is None:
        await session.execute(
            text("delete from public.shared_answers where card_id = cast(:c as uuid)"),
            {"c": card_id},
        )
        row = None
    else:
        row = (
            await session.execute(
                text(
                    """
                    insert into public.shared_answers
                      (card_id, engagement_id, org_id, value, updated_by, updated_at)
                    values (cast(:c as uuid), cast(:e as uuid), cast(:o as uuid),
                            cast(:v as jsonb), cast(:r as uuid), clock_timestamp())
                    on conflict (card_id) do update set
                      value = excluded.value,
                      updated_by = excluded.updated_by,
                      updated_at = excluded.updated_at
                    returning value, updated_at
                    """
                ),
                {
                    "c": card_id,
                    "e": engagement_id,
                    "o": org_id,
                    "v": json.dumps(new_value),
                    "r": recipient_id,
                },
            )
        ).mappings().one()
        row = dict(row)
    await session.execute(
        text(
            """
            insert into public.answer_events
              (card_id, engagement_id, org_id, recipient_id, actor_label, change)
            values (cast(:c as uuid), cast(:e as uuid), cast(:o as uuid),
                    cast(:r as uuid), :a, cast(:ch as jsonb))
            """
        ),
        {
            "c": card_id,
            "e": engagement_id,
            "o": org_id,
            "r": recipient_id,
            "a": actor_label,
            "ch": json.dumps(change),
        },
    )
    return row


async def list_events(session: AsyncSession, engagement_id: str) -> list[dict[str, Any]]:
    """Every logged change on one engagement, newest first."""
    rows = await session.execute(
        text(
            "select id::text, card_id::text as card_id, recipient_id::text as recipient_id, "
            "       actor_label, change, created_at "
            "from public.answer_events where engagement_id = cast(:e as uuid) "
            "order by created_at desc, id"
        ),
        {"e": engagement_id},
    )
    return [dict(r) for r in rows.mappings()]


async def delete_for_engagement(session: AsyncSession, engagement_id: str) -> int:
    """Clear shared answers + their history (engagement reset)."""
    await session.execute(
        text("delete from public.answer_events where engagement_id = cast(:e as uuid)"),
        {"e": engagement_id},
    )
    res = await session.execute(
        text("delete from public.shared_answers where engagement_id = cast(:e as uuid)"),
        {"e": engagement_id},
    )
    return res.rowcount
