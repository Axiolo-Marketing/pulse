"""Public invite-acceptance routes.

The invite acceptance page (``/invite?token=…`` on the frontend) calls
these endpoints to resolve a signed invite token and then complete the
flow with either a password or an OAuth provider.

Auth model: **the token IS the auth**. No cookie, no Bearer. The token
is a signed payload + an SHA-256 hash lookup; either fails or succeeds
without leaking which side rejected.

Two endpoints:

* ``GET  /api/invites/{token}`` — resolve a token to its metadata
  (org name, invitee email, role, expiry, status). Used by the
  acceptance page to render "Join {{org}} as {{role}}" before the
  user picks a credential.
* ``POST /api/invites/{token}/accept`` — apply the invite. Body is one
  of:
  - ``{"auth": "password", "password": "...", "name": "..."}`` — create
    the user with the password, attach the invite, sign them in.
  - ``{"auth": "google"|"microsoft"}`` — return a redirect URL to
    ``/api/auth/{provider}/authorize?invite_token=…`` so the OAuth
    flow carries the token through and accepts on callback.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.invites import attach_invite_to_user
from pulse_api.auth.password import hash_password_async
from pulse_api.auth.session import InvalidSessionError, write_session
from pulse_api.auth.tokens import consume_token
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models._helpers import utcnow_naive
from pulse_api.observability import limiter
from pulse_api.repos import invites as invites_repo
from pulse_api.repos import users as users_repo

router = APIRouter(prefix="/api/invites", tags=["invites"])

# ── Request/response shapes ────────────────────────────────────────────────


class InviteMetadata(BaseModel):
    """Returned by ``GET /api/invites/{token}``.

    Note: ``org_id`` is intentionally omitted — exposing it would let a
    token-guesser enumerate the org structure. The acceptance page only
    needs the human-readable name to render copy.
    """

    org_name: str
    email: str
    role: str
    expires_at: object  # datetime
    status: Literal["pending", "expired", "accepted", "revoked"]


class PasswordAcceptRequest(BaseModel):
    """``POST /api/invites/{token}/accept`` body for the password path."""

    auth: Literal["password"]
    password: str = Field(min_length=8, max_length=256)
    name: str | None = Field(default=None, max_length=200)


class OAuthAcceptRequest(BaseModel):
    """``POST /api/invites/{token}/accept`` body for the OAuth path."""

    auth: Literal["google", "microsoft"]


class PasswordAcceptResponse(BaseModel):
    """Returned on a successful password-based acceptance."""

    user_id: str
    org_id: str
    role: str


class OAuthAcceptResponse(BaseModel):
    """Returned for the OAuth path — the URL to redirect the browser to."""

    redirect_url: str


# ── Helpers ────────────────────────────────────────────────────────────────


async def _resolve_invite_metadata(
    session: AsyncSession,
    raw_token: str,
    *,
    for_update: bool = False,
) -> tuple[dict[str, Any], str]:
    """Resolve a raw token to ``(invite_row, status)``.

    Returns the invite row and a status string
    ``pending|expired|accepted|revoked``. Raises ``HTTPException(404)``
    if the token doesn't resolve to any invite at all (malformed
    signed token or unknown hash).

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        raw_token: Signed token from the email link or the URL.
        for_update: If True, acquire a row-level lock on the matching
            invite row for the rest of the transaction — the accept
            path uses this so concurrent acceptances serialize at the
            DB layer rather than racing through user creation.
    """
    try:
        consume_token(
            "org-invite", raw_token, settings.invite_token_max_age_seconds
        )
    except InvalidSessionError as exc:
        # Treat any signing failure (tampered, expired by signer) as 404.
        # The DB might still hold the row, but without a valid signed
        # token the caller hasn't proven they came from the email link.
        # Hash-only lookups would otherwise let someone brute-force the
        # 32-hex token_hash space.
        raise HTTPException(status_code=404, detail="invite not found") from exc

    token_hash = invites_repo.hash_invite_token(raw_token)
    invite = await invites_repo.find_invite_by_token_hash(
        session, token_hash, for_update=for_update
    )
    if invite is None:
        raise HTTPException(status_code=404, detail="invite not found")

    return invite, invites_repo.invite_status(invite)


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/{token}", response_model=InviteMetadata)
async def get_invite(
    token: str,
    session: AsyncSession = Depends(get_admin_session),
) -> InviteMetadata:
    """Resolve a signed invite token to its metadata.

    Returns the org name, invitee email, role, expiry, and status.
    ``status`` is one of ``pending|expired|accepted``; the frontend
    branches on it to render either the credential form, a "this link
    expired" page, or a "this invite has already been used" page.
    """
    invite, status = await _resolve_invite_metadata(session, token)
    return InviteMetadata(
        org_name=str(invite["org_name"]),
        email=str(invite["email"]),
        role=str(invite["role"]),
        expires_at=invite["expires_at"],
        status=status,  # type: ignore[arg-type]
    )


@router.post("/{token}/accept")
@limiter.limit(settings.rate_limit_sensitive)
async def accept_invite(
    request: Request,
    token: str,
    body: dict[str, Any],
    response: Response,
    session: AsyncSession = Depends(get_admin_session),
) -> PasswordAcceptResponse | OAuthAcceptResponse:
    """Complete an invite acceptance.

    The body discriminator is ``auth``:

    * ``"password"`` — create the user with the supplied password,
      attach the invite, mark email-verified (the OAuth-verified-email
      pattern, but here proven by clicking the email's link), sign the
      user in by setting the session cookie, return ``user_id`` +
      ``org_id`` + ``role``.
    * ``"google"`` / ``"microsoft"`` — return a redirect URL pointing
      at ``/api/auth/{provider}/authorize?invite_token=<raw>``. The
      OAuth callback resolves the token from the signed state cookie
      and accepts.

    Status codes:

    * 404 — token doesn't resolve.
    * 410 — invite is expired, already accepted, revoked, or was
      claimed by a concurrent acceptance request between the lookup
      and the atomic UPDATE.
    * 409 — token is fine but a user with the invite's email already
      has a password (password-path only — they should sign in
      normally, then accept).
    """
    # Lock the invite row up front for the password path: two
    # simultaneous POSTs against the same token then serialize at the
    # DB layer, and the `accept_atomically` UPDATE below picks exactly
    # one winner. The OAuth-redirect path doesn't actually claim the
    # invite here (the callback does), so we don't need to hold the
    # lock through that branch — but resolving with for_update=True
    # for both paths keeps the read pattern uniform and the cost
    # negligible.
    invite, status = await _resolve_invite_metadata(
        session, token, for_update=True
    )
    if status != "pending":
        # 410 Gone — the token resolves but isn't usable.
        raise HTTPException(
            status_code=410,
            detail=f"invite is {status}",
        )

    auth_method = body.get("auth")
    if auth_method == "password":
        try:
            req = PasswordAcceptRequest.model_validate(body)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await _accept_with_password(
            session, invite, req=req, response=response
        )
    if auth_method in ("google", "microsoft"):
        try:
            req = OAuthAcceptRequest.model_validate(body)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _accept_with_oauth(invite, req=req, raw_token=token)
    raise HTTPException(
        status_code=422,
        detail="auth must be one of 'password', 'google', 'microsoft'",
    )


async def _accept_with_password(
    session: AsyncSession,
    invite: dict[str, Any],
    *,
    req: PasswordAcceptRequest,
    response: Response,
) -> PasswordAcceptResponse:
    """Atomically claim the invite, then create-or-attach the user.

    The atomic claim runs FIRST — before any user-creating side
    effects — so a concurrent acceptance request that has already won
    the FOR UPDATE lock and committed cannot let this one through to
    duplicate-create the user. ``accept_atomically`` returns False in
    that case and we return 410.
    """
    # Atomic claim. The FOR UPDATE in the caller ensures we are the
    # only writer touching this row at this instant; the conditional
    # UPDATE then refuses if a prior tab already stamped accepted_at
    # or revoked_at before we acquired the lock.
    claimed = await invites_repo.accept_atomically(session, invite["id"])
    if not claimed:
        raise HTTPException(
            status_code=410, detail="invite no longer redeemable"
        )

    email = str(invite["email"]).lower().strip()
    existing = await users_repo.get_user_by_email(session, email)

    if existing is not None:
        # A user with this email exists. If they already have a
        # password, they should sign in normally (and the OAuth-style
        # email-match invite-accept inside `/api/auth/login` would
        # attach the invite). Refuse the password-set flow here.
        if existing.password_hash is not None:
            raise HTTPException(
                status_code=409,
                detail="account exists; sign in to accept the invite",
            )
        # OAuth-only existing user setting their first password —
        # update the hash and proceed to attach. The atomic claim
        # above already ran, so the second tab cannot reach this.
        await users_repo.update_password_hash(
            session, existing.id, await hash_password_async(req.password)
        )
        user_id = existing.id
    else:
        user = await users_repo.create_user(
            session,
            email=email,
            password_hash=await hash_password_async(req.password),
            name=req.name,
            email_verified_at=utcnow_naive(),
        )
        user_id = user.id

    await attach_invite_to_user(session, invite=invite, user_id=user_id)
    await users_repo.touch_last_login(session, user_id)
    await session.commit()

    write_session(
        response,
        user_id=user_id,
        active_org_id=invite["org_id"],
    )
    return PasswordAcceptResponse(
        user_id=str(user_id),
        org_id=str(invite["org_id"]),
        role=str(invite["role"]),
    )


def _accept_with_oauth(
    invite: dict[str, Any],
    *,
    req: OAuthAcceptRequest,
    raw_token: str,
) -> OAuthAcceptResponse:
    """Return the OAuth-authorize URL with the invite token in tow.

    The callback resolves the token from the signed state cookie (set
    by ``oauth_authorize``), so the raw token only travels through the
    frontend redirect — never persisted anywhere.
    """
    # invite is unused beyond the existence-check above; the OAuth
    # callback re-resolves the token. Keeping the arg for parity with
    # the password helper + future audit-log hook.
    _ = invite
    provider = req.auth
    # The token may contain characters that are URL-safe-base64 but we
    # still pass it through the query string — ``URLSafeTimedSerializer``
    # only uses chars allowed in URLs, so no extra escaping needed.
    redirect_url = (
        f"/api/auth/{provider}/authorize?invite_token={raw_token}"
    )
    return OAuthAcceptResponse(redirect_url=redirect_url)
