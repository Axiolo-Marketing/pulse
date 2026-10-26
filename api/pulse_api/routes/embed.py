"""Embed API — what a client document's ``pulse-blocks.js`` calls.

Same credential as the deck (the respondent's ``X-Pulse-Token``, RLS via
``get_anon_session``), addressed by page block id instead of card id. Cross-
origin calls are allowed only from ``settings.embed_allowed_origins`` and
only on this prefix (``pulse_api.embed_cors``), without cookies.

Saves go through ``client_api.save_response`` so completion alerts,
webhooks and progress behave exactly as they do for the deck.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import page_blocks, webhooks
from pulse_api.config import settings
from pulse_api.db import get_anon_session
from pulse_api.observability import limiter
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import engagements as engagements_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.routes import client_api

router = APIRouter(prefix="/api/embed", tags=["embed"])


class EmbedSaveRequest(BaseModel):
    block_id: str = Field(min_length=1, max_length=64)
    value: dict[str, Any]


@router.get("/blocks")
@limiter.limit(settings.rate_limit_token_validation)
async def get_blocks(
    request: Request,
    session: AsyncSession = Depends(get_anon_session),
) -> dict[str, Any]:
    """The respondent's name and, per page block, its saved answer."""
    me = await engagements_repo.get_my_engagement(session)
    if me is None:
        raise HTTPException(status_code=404, detail="link not found")
    unopened = await engagements_repo.my_unopened_recipient_id(session)
    if unopened is not None:
        webhooks.schedule_deck_opened(unopened)
    cards = [c for c in await cards_repo.list_for_my_engagement(session) if c["block_id"]]
    responses = {
        r["card_id"]: r for r in await responses_repo.list_for_my_engagement(session)
    }
    blocks = {}
    for c in cards:
        r = responses.get(c["id"])
        blocks[c["block_id"]] = {
            "type": c["response_type"],
            "state": r["state"] if r else "not_started",
            "value": r["response_value"] if r else None,
        }
    return {"respondent": me.get("recipient_name"), "blocks": blocks}


@router.post("/responses")
@limiter.limit("240/minute")
async def save_block(
    request: Request,
    req: EmbedSaveRequest,
    session: AsyncSession = Depends(get_anon_session),
) -> dict[str, Any]:
    """Save one block's answer (autosave). An emptied answer goes back to
    ``viewed`` so it stops counting as answered."""
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
        value = page_blocks.clean_answer(card, req.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await engagements_repo.touch_last_active(session)
    row = await client_api.save_response(
        client_api.SaveResponseRequest(
            card_id=card["id"],
            state="answered" if value is not None else "viewed",
            response_value=value,
        ),
        session,
    )
    return {"state": row["state"], "value": row["response_value"]}
