"""Repository helpers for ``deck_templates`` — org-scoped, reusable decks.

Runs on the ``pulse_member`` session: RLS narrows every read and write to
the active org, so another tenant's template is invisible even if its id is
guessed. Writes pass the membership's ``org_id`` so the INSERT satisfies the
RLS WITH CHECK.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Keys of one template card, in the order they're stored.
CARD_KEYS = (
    "category", "title", "context", "question", "response_type",
    "options", "default_value", "skip_allowed", "attachment_path",
)
# Engagement toggles a template carries.
SETTING_KEYS = (
    "voice_enabled", "reminders_enabled", "reactive_cards_enabled",
    "transcription_enabled",
)

_DETAIL_COLS = (
    "id::text as id, name, description, cards, settings, "
    "jsonb_array_length(cards)::int as card_count, created_at, updated_at"
)


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def list_for_org(session: AsyncSession) -> list[dict[str, Any]]:
    """Template summaries (no card bodies), sorted by name."""
    result = await session.execute(
        text(
            "select id::text as id, name, description, "
            "jsonb_array_length(cards)::int as card_count, created_at, updated_at "
            "from public.deck_templates order by lower(name), id"
        )
    )
    return [dict(r) for r in result.mappings().all()]


async def get(session: AsyncSession, template_id: str) -> dict[str, Any] | None:
    if not _valid_uuid(template_id):
        return None
    result = await session.execute(
        text(
            f"select {_DETAIL_COLS} from public.deck_templates "
            "where id = cast(:id as uuid)"
        ),
        {"id": template_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def name_taken(
    session: AsyncSession, name: str, *, exclude_id: str | None = None
) -> bool:
    result = await session.execute(
        text(
            "select 1 from public.deck_templates "
            "where lower(name) = lower(:n) "
            "and (cast(:ex as uuid) is null or id <> cast(:ex as uuid)) limit 1"
        ),
        {"n": name, "ex": exclude_id},
    )
    return result.first() is not None


async def create(
    session: AsyncSession,
    *,
    org_id: str,
    created_by: str | None,
    name: str,
    description: str | None,
    cards: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "insert into public.deck_templates "
            "(org_id, name, description, cards, settings, created_by) "
            "values (cast(:org as uuid), :n, :d, cast(:c as jsonb), "
            "        cast(:s as jsonb), cast(:by as uuid)) "
            f"returning {_DETAIL_COLS}"
        ),
        {
            "org": org_id,
            "n": name,
            "d": description,
            "c": json.dumps(cards),
            "s": json.dumps(settings),
            "by": created_by,
        },
    )
    return dict(result.mappings().one())


async def update(
    session: AsyncSession, template_id: str, fields: dict[str, Any]
) -> dict[str, Any] | None:
    """Write the given keys (``name``, ``description``, ``cards``,
    ``settings``) and bump ``updated_at``. Returns the new row or None."""
    if not _valid_uuid(template_id):
        return None
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {"id": template_id}
    for key, value in fields.items():
        if key in ("cards", "settings"):
            sets.append(f"{key} = cast(:{key} as jsonb)")
            params[key] = json.dumps(value)
        elif key in ("name", "description"):
            sets.append(f"{key} = :{key}")
            params[key] = value
        else:  # pragma: no cover - guarded by the caller
            raise ValueError(f"unknown template field {key!r}")
    result = await session.execute(
        text(
            f"update public.deck_templates set {', '.join(sets)} "
            f"where id = cast(:id as uuid) returning {_DETAIL_COLS}"
        ),
        params,
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def delete(session: AsyncSession, template_id: str) -> dict[str, Any] | None:
    """Delete one template; returns ``{id, name}`` or None when not found."""
    if not _valid_uuid(template_id):
        return None
    result = await session.execute(
        text(
            "delete from public.deck_templates where id = cast(:id as uuid) "
            "returning id::text as id, name"
        ),
        {"id": template_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def snapshot_engagement(
    session: AsyncSession, engagement_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The reusable part of an engagement: its operator-authored shared
    cards (no AI follow-ups, no recipient-scoped cards, no page-block
    cards) in deck order, plus the four engagement toggles."""
    cards = (
        await session.execute(
            text(
                "select category, title, context, question, response_type, "
                "options, default_value, skip_allowed, attachment_path "
                "from public.cards "
                "where engagement_id = cast(:e as uuid) "
                "  and source = 'operator' and recipient_id is null "
                "  and block_id is null "
                "order by order_index"
            ),
            {"e": engagement_id},
        )
    ).mappings().all()
    settings_row = (
        await session.execute(
            text(
                "select voice_enabled, reminders_enabled, "
                "reactive_cards_enabled, transcription_enabled "
                "from public.engagements where id = cast(:e as uuid)"
            ),
            {"e": engagement_id},
        )
    ).mappings().one()
    return (
        [{k: row[k] for k in CARD_KEYS} for row in cards],
        {k: bool(settings_row[k]) for k in SETTING_KEYS},
    )
