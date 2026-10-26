"""Embed API — what a client document's ``pulse-blocks.js`` calls.

Same credential as the deck (the respondent's ``X-Pulse-Token``, RLS via
``get_anon_session``), addressed by page block id instead of card id. Cross-
origin calls are allowed only from ``settings.embed_allowed_origins`` and
only on this prefix (``pulse_api.embed_cors``), without cookies.

Two modes, per engagement:

* One answer each (default): saves go through ``client_api.save_response``,
  so completion alerts, webhooks and progress behave exactly as for the deck.
* ``shared_answers``: everyone edits the same answers and overwrites each
  other; every save is logged in ``answer_events``. The respondent's token
  is resolved on the anon session first; the shared tables are then read
  and written on the BYPASSRLS admin engine filtered on that engagement id.

Saves carry a *change* (``page_blocks.clean_change``): a question's whole
text, or only the grid cells that changed, merged into the current answer.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import page_blocks, webhooks
from pulse_api.config import settings
from pulse_api.db import admin_engine, get_anon_session
from pulse_api.observability import limiter
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import engagements as engagements_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import shared_answers as shared_repo
from pulse_api.routes import client_api

router = APIRouter(prefix="/api/embed", tags=["embed"])


@asynccontextmanager
async def _admin_session() -> AsyncIterator[AsyncSession]:
    """Short-lived BYPASSRLS session for the shared tables. Its own seam so
    tests can bind it to the rolled-back test connection."""
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


class EmbedSaveRequest(BaseModel):
    block_id: str = Field(min_length=1, max_length=64)
    value: dict[str, Any]


async def _me(session: AsyncSession) -> dict[str, Any]:
    me = await engagements_repo.get_my_engagement(session)
    if me is None:
        raise HTTPException(status_code=404, detail="link not found")
    return me


def _iso(ts: Any) -> str | None:
    return ts.isoformat() if ts is not None else None


@router.get("/blocks")
@limiter.limit(settings.rate_limit_token_validation)
async def get_blocks(
    request: Request,
    session: AsyncSession = Depends(get_anon_session),
) -> dict[str, Any]:
    """The respondent's name, the mode, and per page block its current answer
    (in shared mode: who last changed it and when)."""
    me = await _me(session)
    unopened = await engagements_repo.my_unopened_recipient_id(session)
    if unopened is not None:
        webhooks.schedule_deck_opened(unopened)
    cards = [c for c in await cards_repo.list_for_my_engagement(session) if c["block_id"]]
    shared = bool(me.get("shared_answers"))
    blocks: dict[str, dict[str, Any]] = {}
    if shared:
        async with _admin_session() as admin:
            current = await shared_repo.current_for_engagement(admin, me["id"])
        for c in cards:
            cur = current.get(c["id"])
            blocks[c["block_id"]] = {
                "type": c["response_type"],
                "state": "answered" if cur else "not_started",
                "value": cur["value"] if cur else None,
                "updated_by": cur["updated_by_name"] if cur else None,
                "updated_at": _iso(cur["updated_at"]) if cur else None,
            }
    else:
        responses = {
            r["card_id"]: r for r in await responses_repo.list_for_my_engagement(session)
        }
        for c in cards:
            r = responses.get(c["id"])
            blocks[c["block_id"]] = {
                "type": c["response_type"],
                "state": r["state"] if r else "not_started",
                "value": r["response_value"] if r else None,
            }
    return {"respondent": me.get("recipient_name"), "shared": shared, "blocks": blocks}


@router.post("/responses")
@limiter.limit("240/minute")
async def save_block(
    request: Request,
    req: EmbedSaveRequest,
    session: AsyncSession = Depends(get_anon_session),
) -> dict[str, Any]:
    """Save one block's change (autosave). An answer emptied by the change
    stops counting as answered."""
    me = await _me(session)
    card = next(
        (
            c
            for c in await cards_repo.list_for_my_engagement(session)
            if c["block_id"] == req.block_id
        ),
        None,
    )
    if card is None:
        raise HTTPException(status_code=404, detail="block not found")
    try:
        change = page_blocks.clean_change(card, req.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await engagements_repo.touch_last_active(session)

    if me.get("shared_answers"):
        await session.commit()
        async with _admin_session() as admin:
            current = await shared_repo.get_value(admin, card["id"])
            value = page_blocks.apply_change(card, current, change)
            row = await shared_repo.save(
                admin,
                card_id=card["id"],
                engagement_id=me["id"],
                org_id=me["org_id"],
                recipient_id=me["recipient_id"],
                actor_label=me.get("recipient_name") or me.get("recipient_email") or "Respondent",
                change=change,
                new_value=value,
            )
            await admin.commit()
        return {
            "state": "answered" if row else "viewed",
            "value": row["value"] if row else None,
            "updated_by": me.get("recipient_name") or me.get("recipient_email"),
            "updated_at": _iso(row["updated_at"]) if row else None,
        }

    current = next(
        (
            r["response_value"]
            for r in await responses_repo.list_for_my_engagement(session)
            if r["card_id"] == card["id"]
        ),
        None,
    )
    value = page_blocks.apply_change(card, current, change)
    saved = await client_api.save_response(
        client_api.SaveResponseRequest(
            card_id=card["id"],
            state="answered" if value is not None else "viewed",
            response_value=value,
        ),
        session,
    )
    return {"state": saved["state"], "value": saved["response_value"]}
