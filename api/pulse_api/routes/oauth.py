"""OAuth authorize + callback endpoints.

One pair of handlers serves both providers — `provider` is a path param,
looked up via `get_provider(name)`. State is carried in a signed cookie
issued at authorize and consumed at callback; comparing the cookie's
payload to the `state` query param is the CSRF guard.

User linking precedence on a successful callback:
  1. Existing oauth_identities row with matching (provider, sub) → log in that user.
  2. Existing users row with the same email → link a new identity to it, log in.
  3. Neither → create a new user (email_verified_at = now, since the
     provider has already verified the email) and link the identity.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.oauth import OAuthProviderError, get_provider
from pulse_api.auth.session import InvalidSessionError, encode_session
from pulse_api.auth.tokens import consume_token, issue_token
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models._helpers import utcnow_naive
from pulse_api.repos import users as users_repo

router = APIRouter(prefix="/api/auth", tags=["auth"])

OAUTH_STATE_MAX_AGE = 60 * 10  # 10 minutes


def _state_cookie_name(provider: str) -> str:
    return f"oauth_state_{provider}"


@router.get("/{provider}/authorize")
async def oauth_authorize(provider: str) -> RedirectResponse:
    config = get_provider(provider)
    if config is None:
        raise HTTPException(status_code=404, detail="unknown provider")

    state = secrets.token_urlsafe(16)
    state_cookie = issue_token(f"oauth-state-{provider}", {"state": state})
    authorize_url = config.build_authorize_url(state)

    redirect = RedirectResponse(url=authorize_url, status_code=302)
    redirect.set_cookie(
        key=_state_cookie_name(provider),
        value=state_cookie,
        max_age=OAUTH_STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # TODO: True in production
        path="/",
    )
    return redirect


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
    session: AsyncSession = Depends(get_admin_session),
) -> RedirectResponse:
    config = get_provider(provider)
    if config is None:
        raise HTTPException(status_code=404, detail="unknown provider")

    # 1. CSRF: state query param must match the signed state cookie.
    cookie_value = request.cookies.get(_state_cookie_name(provider))
    if not cookie_value:
        raise HTTPException(status_code=400, detail="missing oauth state cookie")
    try:
        cookie_payload = consume_token(
            f"oauth-state-{provider}", cookie_value, OAUTH_STATE_MAX_AGE
        )
    except InvalidSessionError as exc:
        raise HTTPException(status_code=400, detail=f"invalid oauth state: {exc}") from exc
    if cookie_payload.get("state") != state:
        raise HTTPException(status_code=400, detail="oauth state mismatch")

    # 2. Code → tokens → userinfo.
    try:
        tokens = await config.exchange_code(code)
        access_token = tokens.get("access_token")
        if not access_token:
            raise OAuthProviderError(f"{provider} returned no access_token")
        userinfo = await config.fetch_userinfo(access_token)
    except OAuthProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    email = (userinfo.get("email") or "").lower().strip() or None
    sub = userinfo.get("sub")
    name = userinfo.get("name")
    if not email or not sub:
        raise HTTPException(status_code=400, detail="provider returned incomplete userinfo")

    # 3. Link or create.
    identity = await users_repo.find_oauth_identity(session, provider, sub)
    if identity is not None:
        user = await users_repo.get_user_by_id(session, identity.user_id)
        if user is None:
            # Identity row points to a deleted user — bail rather than fix silently.
            raise HTTPException(status_code=500, detail="dangling oauth identity")
    else:
        user = await users_repo.get_user_by_email(session, email)
        if user is None:
            user = await users_repo.create_user(
                session,
                email=email,
                password_hash=None,
                name=name,
                email_verified_at=utcnow_naive(),
            )
        await users_repo.link_oauth_identity(
            session, user_id=user.id, provider=provider, provider_user_id=sub
        )

    await users_repo.touch_last_login(session, user.id)
    await session.commit()

    # 4. Set session, clear state cookie, redirect back to the frontend.
    redirect = RedirectResponse(
        url=f"{settings.frontend_base_url.rstrip('/')}/admin/", status_code=302
    )
    redirect.delete_cookie(_state_cookie_name(provider), path="/")
    redirect.set_cookie(
        key=settings.session_cookie_name,
        value=encode_session(str(user.id)),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return redirect
