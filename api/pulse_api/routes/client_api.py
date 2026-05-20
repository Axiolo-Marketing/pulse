"""Client-facing endpoints. All authenticated by the X-Pulse-Token header.

Every route depends on `get_anon_session`, which:
  1. Rejects missing token (401).
  2. Looks up the client by token. Rejected? No row → empty results from
     RLS; the route either returns 404 (singletons like /me) or [] (lists).
  3. Sets `pulse.token` as a session-local GUC so RLS policies fire.

Inserts derive `client_id` from `pulse_request_client_id()` server-side
rather than trusting the request body, so a hostile client can't forge
the client_id field on a write.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.config import settings
from pulse_api.db import get_anon_session
from pulse_api.observability import limiter
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import clients as clients_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import uploads as uploads_repo

router = APIRouter(prefix="/api", tags=["client"])


VALID_STATES = ("viewed", "answered", "skipped", "needs_edit")


class SaveResponseRequest(BaseModel):
    card_id: str
    state: str = Field(pattern=r"^(viewed|answered|skipped|needs_edit)$")
    response_value: dict[str, Any] | None = None


class ViewRequest(BaseModel):
    card_id: str


@router.get("/me")
@limiter.limit(settings.rate_limit_token_validation)
async def get_me(
    request: Request,
    session: AsyncSession = Depends(get_anon_session),
) -> dict:
    me = await clients_repo.get_my_client(session)
    if me is None:
        raise HTTPException(status_code=404, detail="client not found")
    return me


@router.patch("/me/heartbeat")
async def heartbeat(session: AsyncSession = Depends(get_anon_session)) -> dict[str, str]:
    updated = await clients_repo.touch_last_active(session)
    if not updated:
        raise HTTPException(status_code=404, detail="client not found")
    return {"status": "ok"}


@router.get("/cards")
async def list_cards(session: AsyncSession = Depends(get_anon_session)) -> list[dict]:
    return await cards_repo.list_for_my_client(session)


@router.get("/responses")
async def list_responses(session: AsyncSession = Depends(get_anon_session)) -> list[dict]:
    return await responses_repo.list_for_my_client(session)


@router.post("/responses/view")
async def mark_viewed(
    req: ViewRequest,
    session: AsyncSession = Depends(get_anon_session),
) -> dict:
    row = await responses_repo.mark_viewed(session, req.card_id)
    if row is None:
        raise HTTPException(status_code=404, detail="card not found")
    await session.commit()
    return row


@router.post("/responses")
async def save_response(
    req: SaveResponseRequest,
    session: AsyncSession = Depends(get_anon_session),
) -> dict:
    row = await responses_repo.upsert_answer(
        session,
        card_id=req.card_id,
        state=req.state,
        response_value=req.response_value,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="card not found")
    await session.commit()
    return row


@router.get("/uploads")
async def list_uploads(session: AsyncSession = Depends(get_anon_session)) -> list[dict]:
    return await uploads_repo.list_for_my_client(session)
