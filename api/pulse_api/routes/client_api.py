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
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import reactive
from pulse_api.config import settings
from pulse_api.db import get_anon_session
from pulse_api.observability import limiter
from pulse_api.repos import card_generations as card_generations_repo
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import engagements as engagements_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import uploads as uploads_repo
from pulse_api.routes.orgs import serve_logo_file

router = APIRouter(prefix="/api", tags=["client"])


VALID_STATES = ("viewed", "answered", "skipped", "needs_edit")

# Schemes the deck itself accepts for a `document-link` answer (SPEC.md §4:
# `{url, note?}`). Kept in sync with `isValidUrl()` in `src/lib/render.ts`,
# which restricts the deck's own link input to the same two schemes.
_ALLOWED_URL_SCHEMES = ("http", "https")


class SaveResponseRequest(BaseModel):
    card_id: str
    state: str = Field(pattern=r"^(viewed|answered|skipped|needs_edit)$")
    response_value: dict[str, Any] | None = None


def _reject_unsafe_response_url(response_value: dict[str, Any] | None) -> None:
    """Reject a `response_value.url` that isn't an absolute http(s) URL.

    Trust boundary: `response_value` is attacker-controlled. It comes
    straight from the deck-token holder (the client) via this route, and a
    direct API call bypasses the deck UI's own scheme check (`isValidUrl`
    in `src/lib/render.ts`). The victim is the *operator*: the admin
    console later renders any `url` key as a clickable `<a href=...>` (v1
    `src/scripts/admin.ts`, v2 `src/components/admin/detail/parts.tsx`).
    HTML-escaping / JSX neutralizes markup characters but not the URL
    *scheme* — a stored `javascript:`/`data:`/`vbscript:` (or
    protocol-relative `//...`) value would still execute in the operator's
    browser the moment they click the rendered link.

    Only the `document-link` response type currently populates a `url`
    key, but this checks any `response_value` dict so the rule holds
    regardless of `response_type` — conservative by design: a
    `response_value` with no `url` key, or where `url` isn't a non-empty
    string, is left untouched so non-URL response types are unaffected.

    Args:
        response_value: The raw, still-untrusted value from the request
            body, before it reaches `responses_repo.upsert_answer`.

    Raises:
        HTTPException: 400 if `url` is present, a non-empty string, and
            not an absolute `http://` or `https://` URL.
    """
    if not isinstance(response_value, dict):
        return
    url = response_value.get("url")
    if not isinstance(url, str) or not url:
        return
    parsed = urlsplit(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="response_value.url must be an absolute http:// or https:// URL",
        )


class ViewRequest(BaseModel):
    card_id: str


class ClientMe(BaseModel):
    """Bootstrap payload for the client deck (``GET /api/me``).

    The owning organization's branding rides along via ``org_logo_path`` /
    ``org_branding``, which come from the multi-tenant ``organizations``
    row. Unknown/extra keys are permitted so the contract can grow without
    a breaking change.

    Attributes:
        id: Engagement (client) UUID.
        name: Customer-facing engagement contact name.
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
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_anon_session),
) -> dict:
    _reject_unsafe_response_url(req.response_value)
    row = await responses_repo.upsert_answer(
        session,
        card_id=req.card_id,
        state=req.state,
        response_value=req.response_value,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="card not found")
    card_meta = row.pop("card")
    await session.commit()

    # Reactive cards: extract the trigger text once, here, from the
    # request body this route already validated — and pass it straight
    # through to `run_generation` rather than letting it re-derive the
    # trigger from a `responses` row read after the fact. That read used
    # to race this request's own commit (the outer connection-level
    # transaction in `get_anon_session` doesn't COMMIT until after
    # BackgroundTasks run on this stack) and could observe a stale
    # pre-save snapshot — see `reactive.run_generation`'s docstring.
    # `is_candidate` still gates on the cheap deployment/source checks;
    # `run_generation` re-checks everything with fresh reads once it
    # actually runs.
    trigger = reactive.extract_trigger_text(
        card_meta["response_type"], req.state, req.response_value
    )
    if trigger is not None and reactive.is_candidate(card_source=card_meta["source"]):
        background_tasks.add_task(
            reactive.run_generation,
            response_id=row["id"],
            recipient_id=row["recipient_id"],
            engagement_id=row["engagement_id"],
            card_id=row["card_id"],
            trigger_text=trigger,
        )

    return row


@router.get("/generations")
@limiter.limit(settings.rate_limit_default)
async def list_generations(
    request: Request,
    response_id: str | None = None,
    session: AsyncSession = Depends(get_anon_session),
) -> list[dict]:
    """Poll surface for the deck's post-save "checking for a follow-up"
    loop. RLS (`card_generations_self_read`, migration 0017) scopes this
    to the caller's own recipient; `response_id` narrows further to the
    one save the deck just made, since that's the only generation the
    client cares about right after a correction."""
    return await card_generations_repo.list_for_my_recipient(
        session, response_id=response_id
    )


@router.get("/uploads")
async def list_uploads(session: AsyncSession = Depends(get_anon_session)) -> list[dict]:
    return await uploads_repo.list_for_my_engagement(session)
