"""OAuth authorize + callback endpoints.

One pair of handlers serves both providers — ``provider`` is a path
param, looked up via ``get_provider(name)``. State is carried in a
signed cookie issued at authorize and consumed at callback; comparing
the cookie's payload to the ``state`` query param is the CSRF guard.

User linking precedence on a successful callback (PR 2 of the multi-
tenant refactor):

1. Existing ``oauth_identities`` row with matching ``(provider, sub)``
   → log that user in.
2. Existing ``users`` row with the same email → link a new identity,
   log in.
3. Neither → look up ``organization_invites`` by the OAuth-verified
   email. If a pending, non-expired invite exists: create the user,
   link the identity, insert the membership, mark the invite accepted,
   sign in scoped to the invite's org. If not: redirect back to the
   frontend with ``?error=invitation_required`` and DO NOT create a
   user. Self-signup is disabled — invite-only by design (plan section
   "Scope decisions").
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.invites import attach_invite_to_user as _attach_invite_to_user
from pulse_api.auth.oauth import OAuthProviderError, get_provider
from pulse_api.auth.session import (
    InvalidSessionError,
    cookie_secure_flag,
    encode_session,
)
from pulse_api.auth.tokens import consume_token, issue_token
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models._helpers import utcnow_naive
from pulse_api.repos import invites as invites_repo
from pulse_api.repos import users as users_repo

router = APIRouter(prefix="/api/auth", tags=["auth"])

OAUTH_STATE_MAX_AGE = 60 * 10  # 10 minutes


def _state_cookie_name(provider: str) -> str:
    return f"oauth_state_{provider}"


def _admin_redirect(error: str | None = None) -> RedirectResponse:
    """Build a redirect to the admin frontend, optionally with an error.

    Plan's PR 4 will polish the error rendering; for PR 2 a query-string
    error code is enough to let the SPA show "you need an invitation".
    """
    base = f"{settings.frontend_base_url.rstrip('/')}/admin/"
    url = f"{base}?error={error}" if error else base
    return RedirectResponse(url=url, status_code=302)


async def _find_pending_invite(
    session: AsyncSession, email: str
) -> dict | None:
    """Return the oldest pending, non-expired invite for ``email``.

    Uses raw SQL because invites are a thin row we read once and update
    in the same transaction — staying SQL keeps the FOR UPDATE
    semantics obvious and avoids dragging in a SQLModel update flow
    that doesn't add anything here.

    Returns a dict with ``{id, org_id, role}`` or ``None``.
    """
    result = await session.execute(
        text(
            "select id::text, org_id::text, role from public.organization_invites "
            "where lower(email) = lower(:e) "
            "  and accepted_at is null "
            "  and revoked_at is null "
            "  and expires_at > now() "
            "order by created_at "
            "limit 1 "
            "for update skip locked"
        ),
        {"e": email},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def _accept_oauth_invite(
    session: AsyncSession,
    *,
    invite: dict,
    email: str,
    name: str | None,
    provider: str,
    sub: str,
) -> str:
    """Create the user + membership for an OAuth-verified invite.

    Runs entirely in the request's transaction — either everything
    lands or nothing does. Returns the new user id (str).
    """
    user = await users_repo.create_user(
        session,
        email=email,
        password_hash=None,
        name=name,
        email_verified_at=utcnow_naive(),
    )
    await users_repo.link_oauth_identity(
        session,
        user_id=user.id,
        provider=provider,
        provider_user_id=sub,
    )
    await _attach_invite_to_user(session, invite=invite, user_id=str(user.id))
    return str(user.id)


@router.get("/{provider}/authorize")
async def oauth_authorize(
    provider: str, invite_token: str | None = None
) -> RedirectResponse:
    """Start the OAuth dance.

    The optional ``invite_token`` query param is the raw signed invite
    token from an emailed invite link. When present, we stash it inside
    the signed state cookie so the callback can resolve it after the
    provider hands us back an OAuth-verified identity. This is what
    powers "Accept invite with Google/Microsoft" — the explicit token
    proves the inviter trusts this identity even if the invite was
    sent to a different email than what the provider returns.
    """
    config = get_provider(provider)
    if config is None:
        raise HTTPException(status_code=404, detail="unknown provider")

    state = secrets.token_urlsafe(16)
    state_payload: dict[str, str] = {"state": state}
    if invite_token:
        state_payload["invite_token"] = invite_token
    state_cookie = issue_token(f"oauth-state-{provider}", state_payload)
    authorize_url = config.build_authorize_url(state)

    redirect = RedirectResponse(url=authorize_url, status_code=302)
    redirect.set_cookie(
        key=_state_cookie_name(provider),
        value=state_cookie,
        max_age=OAUTH_STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        # Secure-only in production; HTTP cookies allowed in dev for local testing
        secure=cookie_secure_flag(),
        path="/",
    )
    return redirect


async def _resolve_invite_from_raw_token(
    session: AsyncSession, raw_token: str
) -> dict[str, Any] | None:
    """Resolve a raw signed invite token to its row, if still acceptable.

    Returns ``None`` for any of:
    * malformed/tampered/expired signed token,
    * no matching ``token_hash`` row,
    * invite already accepted (``accepted_at is not null``),
    * invite past ``expires_at``.

    Caller maps the ``None`` to a frontend error redirect.
    """
    try:
        consume_token(
            "org-invite", raw_token, settings.invite_token_max_age_seconds
        )
    except InvalidSessionError:
        return None

    invite = await invites_repo.find_invite_by_token_hash(
        session, invites_repo.hash_invite_token(raw_token)
    )
    if invite is None:
        return None
    if invite["accepted_at"] is not None:
        return None
    if invites_repo.invite_status(invite) != "pending":
        return None
    return invite


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

    # 2b. Explicit invite-token-in-state branch.
    # If the authorize step stashed a raw invite token in the signed
    # state cookie, we resolve it now. A valid, still-pending token
    # wins over the email-lookup path — the inviter explicitly trusted
    # this provider identity by linking to the OAuth authorize URL
    # from the invite acceptance page. An invalid/expired token here
    # falls back to error-redirect rather than silently switching to
    # the email-lookup invite path (the user clicked a specific link;
    # we shouldn't accept a different invite under their nose).
    explicit_invite: dict[str, Any] | None = None
    raw_invite_token = cookie_payload.get("invite_token")
    if raw_invite_token:
        explicit_invite = await _resolve_invite_from_raw_token(
            session, raw_invite_token
        )
        if explicit_invite is None:
            return _admin_redirect(error="invite_invalid")

    # 3. Link or create. The invite-acceptance branch replaces the old
    #    "create new user out of thin air" path — self-signup is gone.
    identity = await users_repo.find_oauth_identity(session, provider, sub)
    active_org_id: str | None = None
    if identity is not None:
        user = await users_repo.get_user_by_id(session, identity.user_id)
        if user is None:
            # Identity row points to a deleted user — bail rather than fix silently.
            raise HTTPException(status_code=500, detail="dangling oauth identity")
        user_id = str(user.id)
        if explicit_invite is not None:
            await _attach_invite_to_user(
                session, invite=explicit_invite, user_id=user_id
            )
            active_org_id = explicit_invite["org_id"]
        else:
            active_org_id = (
                str(user.last_active_org_id) if user.last_active_org_id else None
            )
    else:
        existing = await users_repo.get_user_by_email(session, email)
        if existing is not None:
            await users_repo.link_oauth_identity(
                session, user_id=existing.id, provider=provider, provider_user_id=sub
            )
            user_id = str(existing.id)
            if explicit_invite is not None:
                await _attach_invite_to_user(
                    session, invite=explicit_invite, user_id=user_id
                )
                active_org_id = explicit_invite["org_id"]
            else:
                # An existing user with a pending invite should still join
                # the inviting org on this sign-in. Accept it transactionally.
                pending = await _find_pending_invite(session, email)
                if pending is not None:
                    await _attach_invite_to_user(
                        session, invite=pending, user_id=user_id
                    )
                    active_org_id = pending["org_id"]
                else:
                    active_org_id = (
                        str(existing.last_active_org_id)
                        if existing.last_active_org_id
                        else None
                    )
        else:
            invite = explicit_invite or await _find_pending_invite(session, email)
            if invite is None:
                # Hard 302 back to the frontend with an error code. The
                # SPA renders "you need an invitation" — see plan PR 4
                # for the polished UX.
                return _admin_redirect(error="invitation_required")
            user_id = await _accept_oauth_invite(
                session,
                invite=invite,
                email=email,
                name=name,
                provider=provider,
                sub=sub,
            )
            active_org_id = invite["org_id"]

    await users_repo.touch_last_login(session, user_id)
    await session.commit()

    # 4. Set session, clear state cookie, redirect back to the frontend.
    redirect = RedirectResponse(
        url=f"{settings.frontend_base_url.rstrip('/')}/admin/", status_code=302
    )
    redirect.delete_cookie(_state_cookie_name(provider), path="/")
    redirect.set_cookie(
        key=settings.session_cookie_name,
        value=encode_session(user_id, active_org_id),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        # Secure-only in production; HTTP cookies allowed in dev for local testing
        secure=cookie_secure_flag(),
        path="/",
    )
    return redirect
