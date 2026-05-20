"""ClickUp OAuth + webhook endpoints.

Two distinct flows live here:

1. **Operator authorization**: an admin-authed user grants Pulse access to
   their ClickUp via OAuth. Pulse stores the access_token (encrypted),
   discovers the user's workspaces ("teams"), and registers a webhook in
   each workspace so status changes flow back.

2. **Webhook receiver**: ClickUp POSTs to `/api/clickup/webhook` whenever
   a tracked event fires. The receiver verifies the HMAC signature against
   the per-workspace secret stored at OAuth time, then routes the event
   to a card/response update. No session auth on this path.

The auth + state-cookie shape mirrors the existing Google/Microsoft
OAuth code in `routes/oauth.py`; differences are noted inline.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import clickup
from pulse_api.auth.middleware import get_current_admin
from pulse_api.auth.session import InvalidSessionError
from pulse_api.auth.tokens import consume_token, issue_token
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models import User
from pulse_api.observability import log
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import users as users_repo

# Use a dedicated prefix so the `clickup` segment never gets matched as
# the `provider` path param on `/api/auth/{provider}/authorize` in the
# generic OAuth router.
router = APIRouter(prefix="/api", tags=["clickup"])

OAUTH_STATE_MAX_AGE = 60 * 10  # 10 minutes
STATE_COOKIE_NAME = "oauth_state_clickup"

# Events we register on the per-workspace webhook. Keep this list small —
# every event adds load on the receiver side. Status changes are the
# only thing the v1 UI reflects.
WEBHOOK_EVENTS = ["taskStatusUpdated"]


# ── OAuth: authorize ──────────────────────────────────────────────────────


@router.get("/auth/clickup/authorize")
async def clickup_authorize(
    _: User = Depends(get_current_admin),
) -> RedirectResponse:
    """Admin-only. Generates state, sets a signed cookie, redirects to
    ClickUp's consent screen."""
    if not settings.clickup_client_id or not settings.clickup_redirect_uri:
        raise HTTPException(status_code=503, detail="clickup is not configured")

    state = secrets.token_urlsafe(16)
    state_cookie = issue_token("oauth-state-clickup", {"state": state})
    authorize_url = clickup.build_authorize_url(
        client_id=settings.clickup_client_id,
        redirect_uri=settings.clickup_redirect_uri,
        state=state,
    )

    redirect = RedirectResponse(url=authorize_url, status_code=302)
    redirect.set_cookie(
        key=STATE_COOKIE_NAME,
        value=state_cookie,
        max_age=OAUTH_STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return redirect


# ── OAuth: callback ──────────────────────────────────────────────────────


@router.get("/auth/clickup/callback")
async def clickup_callback(
    code: str,
    state: str,
    request: Request,
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> RedirectResponse:
    """Verify state, exchange code for token, register a webhook per
    authorized workspace, store everything encrypted, redirect to admin."""
    # 1. CSRF state check
    cookie = request.cookies.get(STATE_COOKIE_NAME)
    if not cookie:
        raise HTTPException(status_code=400, detail="missing clickup oauth state cookie")
    try:
        payload = consume_token("oauth-state-clickup", cookie, OAUTH_STATE_MAX_AGE)
    except InvalidSessionError as exc:
        raise HTTPException(status_code=400, detail=f"invalid clickup state: {exc}") from exc
    if payload.get("state") != state:
        raise HTTPException(status_code=400, detail="clickup oauth state mismatch")

    # 2. Exchange code for an access token
    try:
        access_token = await clickup.exchange_code(
            client_id=settings.clickup_client_id,
            client_secret=settings.clickup_client_secret,
            code=code,
        )
    except clickup.ClickUpError as exc:
        log.warning("clickup.oauth.exchange_failed", detail=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    api = clickup.ClickUpClient(access_token)

    # 3. Capture the ClickUp user id (for diagnostics) and stash the
    # encrypted token. crypto.encrypt happens inside set_clickup_token.
    try:
        cu_user = await api.get_authorized_user()
    except clickup.ClickUpError:
        cu_user = {}
    await users_repo.set_clickup_token(
        session, user.id, access_token=access_token, clickup_user_id=str(cu_user.get("id") or "") or None
    )

    # 4. For each workspace, create a webhook + remember its secret.
    try:
        teams = await api.get_authorized_teams()
    except clickup.ClickUpError as exc:
        log.warning("clickup.oauth.teams_failed", detail=str(exc))
        teams = []

    webhook_endpoint = settings.clickup_redirect_uri.rsplit("/", 1)[0] + "/webhook" \
        if "/api/auth/clickup/callback" in settings.clickup_redirect_uri \
        else settings.clickup_redirect_uri.replace(
            "/api/auth/clickup/callback", "/api/clickup/webhook"
        )

    for team in teams:
        team_id = str(team.get("id"))
        if not team_id:
            continue
        try:
            wh = await api.create_webhook(
                team_id, endpoint=webhook_endpoint, events=WEBHOOK_EVENTS
            )
        except clickup.ClickUpError as exc:
            # Don't fail the whole connect on a single webhook failure —
            # other workspaces still get registered. The operator can
            # disconnect + reconnect if needed.
            log.warning("clickup.oauth.webhook_failed", team_id=team_id, detail=str(exc))
            continue
        await users_repo.save_clickup_workspace(
            session,
            user_id=user.id,
            workspace_id=team_id,
            workspace_name=team.get("name"),
            webhook_id=str(wh.get("id") or "") or None,
            webhook_secret=wh.get("secret"),
        )

    await session.commit()

    # 5. Redirect back to admin with a flash.
    return RedirectResponse(
        url=f"{settings.frontend_base_url.rstrip('/')}/admin/?clickup=connected",
        status_code=302,
    )


# ── Disconnect ───────────────────────────────────────────────────────────


@router.post("/auth/clickup/disconnect")
async def clickup_disconnect(
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> dict:
    """Best-effort: delete the per-workspace webhooks via the ClickUp API
    (if we still have a valid token), then drop the local rows + clear
    the token. Always returns 200 — disconnect should never block."""
    token = await users_repo.get_clickup_token(session, user.id)
    workspaces = await users_repo.delete_clickup_workspaces_for_user(session, user.id)

    if token:
        api = clickup.ClickUpClient(token)
        for ws in workspaces:
            wh_id = ws.get("webhook_id")
            if not wh_id:
                continue
            try:
                await api.delete_webhook(wh_id)
            except clickup.ClickUpError as exc:
                log.warning("clickup.disconnect.webhook_delete_failed", webhook_id=wh_id, detail=str(exc))

    await users_repo.clear_clickup_token(session, user.id)
    await session.commit()
    return {"status": "ok", "workspaces_removed": len(workspaces)}


# ── Webhook receiver ─────────────────────────────────────────────────────


@router.post("/clickup/webhook")
async def clickup_webhook(
    request: Request,
    session: AsyncSession = Depends(get_admin_session),
) -> dict:
    """ClickUp POSTs here on every subscribed event. We verify the HMAC
    signature against the workspace's stored secret, then route the
    event by `event` name.

    Replay safety: ClickUp sends the same body + signature on retry if
    Pulse times out. The handler is idempotent — `set_clickup_status_by_card`
    on the same (card_id, status) is a no-op."""
    body = await request.body()
    signature = request.headers.get("x-signature") or ""

    # Parse first so we can find the team_id to look up the secret. JSON
    # parse errors → 400 directly (ClickUp won't retry on a malformed
    # body that's our own fault to interpret).
    import json
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="malformed json body") from None

    team_id = str(payload.get("team_id") or "")
    if not team_id:
        raise HTTPException(status_code=400, detail="missing team_id")

    secret = await users_repo.get_workspace_webhook_secret(session, team_id)
    if secret is None:
        # Unknown workspace — we never registered for this team. Refuse
        # without leaking which teams we know about.
        log.warning("clickup.webhook.unknown_team", team_id=team_id)
        raise HTTPException(status_code=401, detail="unknown workspace")

    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        log.warning("clickup.webhook.bad_signature", team_id=team_id)
        raise HTTPException(status_code=401, detail="invalid signature")

    event = payload.get("event")
    task_id = str(payload.get("task_id") or "")

    if event == "taskStatusUpdated" and task_id:
        card = await cards_repo.get_card_by_clickup_task_id(session, task_id)
        if card is None:
            # Task we don't track — ack so ClickUp doesn't retry.
            log.info("clickup.webhook.unknown_task", task_id=task_id)
            return {"status": "ok", "action": "ignored-unknown-task"}
        # Status string lives at history_items[].after.status (per ClickUp
        # webhook docs) or as a top-level `status` field on some events.
        new_status = _extract_status(payload)
        if new_status:
            await responses_repo.set_clickup_status_by_card(
                session, card["id"], new_status
            )
            await session.commit()
            return {"status": "ok", "action": "updated", "card_id": card["id"]}
        log.info("clickup.webhook.no_status_in_payload", task_id=task_id)
        return {"status": "ok", "action": "ignored-no-status"}

    # Other events: acknowledge but don't act. Keeping the receiver
    # accepting unknown events means we can register more events later
    # without breaking older ClickUp configs.
    return {"status": "ok", "action": "noop", "event": event}


def _extract_status(payload: dict) -> str | None:
    """ClickUp's webhook payload shape varies a bit. The status string
    lands on `history_items[].after.status` for status-changed events,
    sometimes also directly on `status`. Take whichever is present."""
    history = payload.get("history_items") or []
    for item in history:
        after = item.get("after") or {}
        s = after.get("status")
        if isinstance(s, str):
            return s
        if isinstance(s, dict):
            name = s.get("status") or s.get("name")
            if isinstance(name, str):
                return name
    direct = payload.get("status")
    if isinstance(direct, str):
        return direct
    if isinstance(direct, dict):
        return direct.get("status") or direct.get("name")
    return None
