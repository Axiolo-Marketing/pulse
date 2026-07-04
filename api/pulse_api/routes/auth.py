"""Email + password auth routes plus per-user API key management.

OAuth callbacks (Google, Microsoft) live in their own module
(``routes/oauth.py``). Email verification + forgot/reset password are
covered here. API key management endpoints sit under ``/api/auth/me/``
so the operator surface is one short hop from ``/api/auth/me``.

The session payload carries ``(user_id, active_org_id)`` after the PR 2
auth refactor. ``encode_session`` now takes the active org id; we
backfill it from ``users.last_active_org_id`` whenever the user has one
so single-org operators never need to "pick an org" before they can
work.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import email as email_module
from pulse_api.audit import record_audit
from pulse_api.auth import api_keys as api_keys_lib
from pulse_api.auth.email_messages import password_reset_email, verification_email
from pulse_api.auth.middleware import get_current_org_member, get_current_user
from pulse_api.auth.password import hash_password_async, verify_password_async
from pulse_api.auth.session import (
    InvalidSessionError,
    clear_session,
    write_session,
)
from pulse_api.auth.tokens import consume_token, issue_token
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models import ApiKey, OrganizationMembership, User
from pulse_api.observability import limiter, log
from pulse_api.repos import api_keys as api_keys_repo
from pulse_api.repos import users as users_repo

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=256)


class UserResponse(BaseModel):
    """Shape of the ``/api/auth/me`` payload.

    ``is_superadmin`` lets the frontend gate the superadmin nav item
    client-side; the server still enforces it independently in the
    superadmin dep. ``active_org_id`` is the org the operator is
    currently scoped to (may be ``None`` for a user with no membership
    yet — that user can sign in but can't reach any ``/api/admin/*``
    endpoint until they accept an invite).
    """

    id: str
    email: str
    name: str | None
    is_superadmin: bool
    active_org_id: str | None
    email_verified_at: datetime | None
    has_password: bool

    @classmethod
    def from_model(cls, u: User) -> "UserResponse":
        return cls(
            id=str(u.id),
            email=u.email,
            name=u.name,
            is_superadmin=u.is_superadmin,
            active_org_id=(
                str(u.last_active_org_id) if u.last_active_org_id else None
            ),
            email_verified_at=u.email_verified_at,
            has_password=u.password_hash is not None,
        )


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str | None = Field(default=None, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class OAuthIdentityResponse(BaseModel):
    provider: str
    linked_at: datetime


class CreateApiKeyRequest(BaseModel):
    """Request body for ``POST /api/auth/me/api-keys``.

    ``org_id`` is optional — when omitted, defaults to the operator's
    currently-active org. When supplied, the route verifies the operator
    is a member of that org before minting the key.
    """

    label: str = Field(min_length=1, max_length=100)
    org_id: str | None = None


class ApiKeySummary(BaseModel):
    id: str
    label: str
    prefix: str
    org_id: str
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyWithSecret(ApiKeySummary):
    """Includes the raw key — only returned by POST /api/auth/me/api-keys
    once, at creation. The list endpoint never returns this shape."""

    key: str


@router.post("/signup", status_code=201, response_model=UserResponse)
@limiter.limit(settings.rate_limit_account_enumeration)
async def signup(
    request: Request,
    req: SignupRequest,
    session: AsyncSession = Depends(get_admin_session),
) -> UserResponse:
    if not settings.signup_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    existing = await users_repo.get_user_by_email(session, req.email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")

    user = await users_repo.create_user(
        session,
        email=req.email,
        password_hash=await hash_password_async(req.password),
        name=req.name,
    )
    await session.commit()

    token = issue_token("email-verify", {"user_id": str(user.id)})
    subject, body = verification_email(token, name=user.name)
    await email_module.send_email(user.email, subject, body)

    return UserResponse.from_model(user)


@router.post("/verify-email", response_model=UserResponse)
@limiter.limit(settings.rate_limit_sensitive)
async def verify_email(
    request: Request,
    req: VerifyEmailRequest,
    session: AsyncSession = Depends(get_admin_session),
) -> UserResponse:
    try:
        payload = consume_token(
            "email-verify", req.token, settings.verify_email_token_max_age_seconds
        )
    except InvalidSessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = await users_repo.get_user_by_id(session, payload["user_id"])
    if user is None:
        raise HTTPException(status_code=400, detail="user not found")

    await users_repo.mark_email_verified(session, user.id)
    await session.commit()
    await session.refresh(user)
    return UserResponse.from_model(user)


@router.post("/forgot-password")
@limiter.limit(settings.rate_limit_account_enumeration)
async def forgot_password(
    request: Request,
    req: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_admin_session),
) -> dict[str, str]:
    """Always returns 200 — don't leak whether the email exists.

    Sends a reset email iff the user exists and has a password_hash
    (OAuth-only users can't reset what they don't have)."""
    user = await users_repo.get_user_by_email(session, req.email)
    if user is not None and user.password_hash is not None:
        token = issue_token("password-reset", {"user_id": str(user.id)})
        subject, body = password_reset_email(token, name=user.name)
        await email_module.send_email(user.email, subject, body)
    return {"status": "ok"}


@router.post("/reset-password")
@limiter.limit(settings.rate_limit_sensitive)
async def reset_password(
    request: Request,
    req: ResetPasswordRequest,
    session: AsyncSession = Depends(get_admin_session),
) -> dict[str, str]:
    try:
        payload = consume_token(
            "password-reset", req.token, settings.reset_password_token_max_age_seconds
        )
    except InvalidSessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = await users_repo.get_user_by_id(session, payload["user_id"])
    if user is None:
        raise HTTPException(status_code=400, detail="user not found")

    await users_repo.update_password_hash(
        session, user.id, await hash_password_async(req.new_password)
    )
    await session.commit()
    return {"status": "ok"}


@router.post("/login", response_model=UserResponse)
@limiter.limit(settings.rate_limit_token_validation)
async def login(
    request: Request,
    req: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_admin_session),
) -> UserResponse:
    user = await users_repo.get_user_by_email(session, req.email)
    # Constant-message responses on credential failures — don't leak
    # whether the email exists.
    if user is None or user.password_hash is None:
        log.info("auth.login.failed", email=req.email, reason="unknown-or-oauth-only")
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not await verify_password_async(req.password, user.password_hash):
        log.info("auth.login.failed", email=req.email, reason="bad-password")
        raise HTTPException(status_code=401, detail="invalid credentials")
    if user.email_verified_at is None:
        log.info("auth.login.failed", email=req.email, reason="unverified")
        raise HTTPException(status_code=403, detail="email not verified")

    await users_repo.touch_last_login(session, user.id)
    await session.commit()

    # Mint a session with the user's last-active org so the next
    # ``/api/admin/*`` call doesn't have to backfill from the user row.
    write_session(
        response,
        user_id=user.id,
        active_org_id=user.last_active_org_id,
    )
    return UserResponse.from_model(user)


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    clear_session(response)
    return {"status": "ok"}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.from_model(user)


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    req: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_admin_session),
) -> UserResponse:
    name = req.name.strip() if req.name is not None else None
    await users_repo.update_name(session, user.id, name or None)
    await session.commit()
    await session.refresh(user)
    return UserResponse.from_model(user)


@router.post("/change-password", response_model=UserResponse)
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_admin_session),
) -> UserResponse:
    # Users with an existing password must prove it. OAuth-only users
    # (no password_hash) can set an initial one — the session cookie is
    # the auth gate, and they reached it by completing the OAuth flow.
    if user.password_hash is not None:
        if not req.current_password or not await verify_password_async(
            req.current_password, user.password_hash
        ):
            log.info("auth.change_password.failed", user_id=str(user.id), reason="bad-current")
            raise HTTPException(status_code=400, detail="current password is incorrect")

    await users_repo.update_password_hash(
        session, user.id, await hash_password_async(req.new_password)
    )
    await session.commit()
    await session.refresh(user)
    return UserResponse.from_model(user)


@router.get("/me/identities", response_model=list[OAuthIdentityResponse])
async def list_identities(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_admin_session),
) -> list[OAuthIdentityResponse]:
    identities = await users_repo.list_oauth_identities(session, user.id)
    return [
        OAuthIdentityResponse(provider=i.provider, linked_at=i.created_at)
        for i in identities
    ]


# ── API keys ──────────────────────────────────────────────────────────────


def _summary(row: ApiKey) -> ApiKeySummary:
    return ApiKeySummary(
        id=str(row.id),
        label=row.label,
        prefix=row.prefix,
        org_id=str(row.org_id),
        last_used_at=row.last_used_at,
        created_at=row.created_at,
    )


@router.get("/me/api-keys", response_model=list[ApiKeySummary])
async def list_api_keys(
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    session: AsyncSession = Depends(get_admin_session),
) -> list[ApiKeySummary]:
    """Active keys for the operator, scoped to their currently-active org.

    Keys minted against other orgs the user belongs to are hidden until
    the operator switches into that org. The Settings page is per-org;
    showing every key the user has across every org would clutter the
    UI and surface keys that aren't usable from the current context.
    """
    user, membership = org_member
    keys = await api_keys_repo.list_for_user_in_org(
        session, user_id=user.id, org_id=membership.org_id
    )
    return [_summary(k) for k in keys]


@router.post("/me/api-keys", status_code=201, response_model=ApiKeyWithSecret)
@limiter.limit(settings.rate_limit_token_validation)
async def create_api_key(
    request: Request,
    req: CreateApiKeyRequest,
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    session: AsyncSession = Depends(get_admin_session),
) -> ApiKeyWithSecret:
    """Mint a fresh API key for the operator.

    ``org_id`` in the request defaults to the operator's active org.
    When the operator names a different org we verify they are a member
    of it before minting — otherwise an owner of org A could mint a key
    that would authenticate as anyone in org B.

    The raw key is returned exactly once in this response and never
    again — the UI surfaces it inline and warns the operator. On disk we
    keep only ``prefix`` + ``key_hash``; the raw value isn't recoverable.
    """
    user, active_membership = org_member

    # Resolve target org. Default to the current active org.
    target_org_id: uuid.UUID
    if req.org_id is None:
        target_org_id = active_membership.org_id
    else:
        try:
            target_org_id = uuid.UUID(req.org_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="invalid org_id"
            ) from exc

        # If the operator named the active org, they're a member by
        # construction (active_membership). Otherwise look it up.
        if target_org_id != active_membership.org_id:
            result = await session.execute(
                text(
                    "select 1 from public.organization_memberships "
                    "where user_id = cast(:u as uuid) "
                    "  and org_id  = cast(:o as uuid) limit 1"
                ),
                {"u": str(user.id), "o": str(target_org_id)},
            )
            if result.scalar() is None:
                raise HTTPException(
                    status_code=403,
                    detail="user is not a member of the target organization",
                )

    raw = api_keys_lib.generate_key()
    prefix = api_keys_lib.prefix_of(raw)
    key_hash = api_keys_lib.hash_key(raw)

    row = await api_keys_repo.create_for_user(
        session,
        user_id=user.id,
        org_id=target_org_id,
        prefix=prefix,
        key_hash=key_hash,
        label=req.label.strip(),
    )
    # NEVER record the raw key — only the prefix is safe. The Activity
    # UI renders "Operator created API key 'CLI script' (pulse_abc1…)".
    await record_audit(
        session,
        org_id=target_org_id,
        user_id=user.id,
        action="api_key.create",
        target_type="api_key",
        target_id=str(row.id),
        metadata={"label": row.label, "prefix": row.prefix},
    )
    await session.commit()
    await session.refresh(row)

    return ApiKeyWithSecret(
        id=str(row.id),
        label=row.label,
        prefix=row.prefix,
        org_id=str(row.org_id),
        last_used_at=row.last_used_at,
        created_at=row.created_at,
        key=raw,
    )


@router.delete("/me/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_admin_session),
) -> None:
    """Hard-revoke. The key stops authenticating immediately — the
    partial index ``api_keys_prefix_idx`` excludes revoked rows, so
    subsequent bearer lookups won't even see it.

    Scoping is by user, NOT by active org: an owner who switched orgs
    can still revoke any of their personal keys regardless of which org
    they were minted against. Cross-user attempts return 404 (not 403)
    so the existence of someone else's key id can't be probed.

    The audit row is written under the key's own ``org_id`` so the
    activity feed surfaces it in the org the key was minted against —
    not the operator's currently-active org. Cross-org owners revoking
    a key from outside the key's org therefore still leave a trail in
    the right place.
    """
    try:
        as_uuid = uuid.UUID(key_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="api key not found") from exc

    # Peek at the key BEFORE revoke so the audit log can capture its
    # org_id + prefix (needed for the activity row and to attribute it
    # to the right org). Same `user_id` predicate as the revoke so this
    # also short-circuits cross-user probes.
    snapshot = await session.execute(
        text(
            "select org_id::text as org_id, prefix, label "
            "from public.api_keys "
            "where id = cast(:k as uuid) "
            "  and user_id = cast(:u as uuid) "
            "  and revoked_at is null"
        ),
        {"k": str(as_uuid), "u": str(user.id)},
    )
    snapshot_row = snapshot.mappings().one_or_none()

    ok = await api_keys_repo.revoke(
        session, api_key_id=as_uuid, user_id=user.id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="api key not found")

    if snapshot_row is not None:
        await record_audit(
            session,
            org_id=str(snapshot_row["org_id"]),
            user_id=user.id,
            action="api_key.revoke",
            target_type="api_key",
            target_id=str(as_uuid),
            metadata={
                "label": str(snapshot_row["label"]),
                "prefix": str(snapshot_row["prefix"]),
            },
        )
    await session.commit()
