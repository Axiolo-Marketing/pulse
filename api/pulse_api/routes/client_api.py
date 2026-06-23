"""Client-facing endpoints. All authenticated by the X-Pulse-Token header.

Every route depends on `get_anon_session`, which:
  1. Rejects missing token (401).
  2. Looks up the engagement by token. Rejected? No row → empty results
     from RLS; the route either returns 404 (singletons like /me) or []
     (lists).
  3. Sets `pulse.token` as a session-local GUC so RLS policies fire.

Inserts derive `engagement_id` from `pulse_request_engagement_id()`
server-side rather than trusting the request body, so a hostile client
can't forge the engagement_id field on a write.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.config import settings
from pulse_api.db import get_anon_session
from pulse_api.observability import limiter
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import engagements as engagements_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import uploads as uploads_repo
from pulse_api.routes.orgs import serve_logo_file

router = APIRouter(prefix="/api", tags=["client"])


VALID_STATES = ("viewed", "answered", "skipped", "needs_edit")


class SaveResponseRequest(BaseModel):
    card_id: str
    state: str = Field(pattern=r"^(viewed|answered|skipped|needs_edit)$")
    response_value: dict[str, Any] | None = None


class ViewRequest(BaseModel):
    card_id: str


class ClientMe(BaseModel):
    """Bootstrap payload for the client deck (``GET /api/me``).

    Mirrors the legacy free-text fields (``org_name`` is the consultant's
    label for their customer's company — distinct from the owning
    organization's ``org_logo_path`` / ``org_branding``, which come from
    the multi-tenant ``organizations`` row). Unknown/extra keys are
    permitted so the contract can grow without a breaking change.

    Attributes:
        id: Engagement (client) UUID.
        name: Customer-facing engagement contact name.
        org_name: Legacy free-text customer-org label.
        engagement_name: Optional engagement label.
        brief: Optional engagement brief.
        voice_enabled: Whether the deck should offer the voice recorder.
            Defaults ``False`` — voice is opt-in per engagement.
        created_at: Insert timestamp.
        last_active_at: Last client activity timestamp.
        org_logo_path: Owning org's logo path, or ``None``. The deck
            fetches the bytes via ``GET /api/me/logo``.
        org_branding: Owning org's branding overrides, or ``None`` to use
            the deck's built-in defaults.
    """

    model_config = {"extra": "allow"}

    id: str
    name: str
    org_name: str | None = None
    engagement_name: str | None = None
    brief: str | None = None
    voice_enabled: bool = False
    created_at: Any | None = None
    last_active_at: Any | None = None
    org_logo_path: str | None = None
    org_branding: dict[str, Any] | None = None


@router.get("/me", response_model=ClientMe)
@limiter.limit(settings.rate_limit_token_validation)
async def get_me(
    request: Request,
    session: AsyncSession = Depends(get_anon_session),
) -> dict:
    me = await engagements_repo.get_my_engagement(session)
    if me is None:
        raise HTTPException(status_code=404, detail="client not found")
    return me


@router.get("/me/logo")
@limiter.limit(settings.rate_limit_token_validation)
async def get_my_org_logo(
    request: Request,
    session: AsyncSession = Depends(get_anon_session),
) -> FileResponse:
    """Serve the owning org's logo for the token-bound client.

    Auth: the request's ``X-Pulse-Token``. The org is resolved from the
    token's client row (and the ``pulse.org_id`` GUC) — the client never
    sends a filename, so there is no traversal surface. Returns 404 when
    the org has no logo or the stored file is missing.

    Rate-limited identically to ``GET /api/me`` since each call resolves
    the token via a DB round-trip plus a disk read.
    """
    logo_path = await engagements_repo.get_my_org_logo_path(session)
    if not logo_path:
        raise HTTPException(status_code=404, detail="logo not found")
    return serve_logo_file(logo_path)


@router.patch("/me/heartbeat")
async def heartbeat(session: AsyncSession = Depends(get_anon_session)) -> dict[str, str]:
    updated = await engagements_repo.touch_last_active(session)
    if not updated:
        raise HTTPException(status_code=404, detail="client not found")
    return {"status": "ok"}


@router.get("/cards")
async def list_cards(session: AsyncSession = Depends(get_anon_session)) -> list[dict]:
    return await cards_repo.list_for_my_engagement(session)


@router.get("/responses")
async def list_responses(session: AsyncSession = Depends(get_anon_session)) -> list[dict]:
    return await responses_repo.list_for_my_engagement(session)


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
    return await uploads_repo.list_for_my_engagement(session)
