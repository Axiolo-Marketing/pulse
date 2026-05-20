"""Email + password auth routes.

OAuth callbacks (Google, Microsoft) get their own modules in the next phase.
Email verification + forgot/reset password are deferred — for now, signup
creates an unverified user and login refuses to issue a session until
`email_verified_at` is set.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import email as email_module
from pulse_api.auth.email_messages import password_reset_email, verification_email
from pulse_api.auth.middleware import get_current_user
from pulse_api.auth.password import hash_password, verify_password
from pulse_api.auth.session import InvalidSessionError, encode_session
from pulse_api.auth.tokens import consume_token, issue_token
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models import User
from pulse_api.observability import limiter, log
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
    id: str
    email: str
    name: str | None
    is_admin: bool
    email_verified_at: datetime | None

    @classmethod
    def from_model(cls, u: User) -> "UserResponse":
        return cls(
            id=str(u.id),
            email=u.email,
            name=u.name,
            is_admin=u.is_admin,
            email_verified_at=u.email_verified_at,
        )


def _set_session_cookie(response: Response, user_id: str) -> None:
    token = encode_session(user_id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=False,  # TODO: True in production once HTTPS is the only origin
        path="/",
    )


@router.post("/signup", status_code=201, response_model=UserResponse)
@limiter.limit(settings.rate_limit_account_enumeration)
async def signup(
    request: Request,
    req: SignupRequest,
    session: AsyncSession = Depends(get_admin_session),
) -> UserResponse:
    existing = await users_repo.get_user_by_email(session, req.email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already registered")

    user = await users_repo.create_user(
        session,
        email=req.email,
        password_hash=hash_password(req.password),
        name=req.name,
    )
    await session.commit()

    token = issue_token("email-verify", {"user_id": str(user.id)})
    subject, body = verification_email(token, name=user.name)
    await email_module.send_email(user.email, subject, body)

    return UserResponse.from_model(user)


@router.post("/verify-email", response_model=UserResponse)
async def verify_email(
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
async def reset_password(
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

    await users_repo.update_password_hash(session, user.id, hash_password(req.new_password))
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
    if not verify_password(req.password, user.password_hash):
        log.info("auth.login.failed", email=req.email, reason="bad-password")
        raise HTTPException(status_code=401, detail="invalid credentials")
    if user.email_verified_at is None:
        log.info("auth.login.failed", email=req.email, reason="unverified")
        raise HTTPException(status_code=403, detail="email not verified")

    await users_repo.touch_last_login(session, user.id)
    await session.commit()

    _set_session_cookie(response, str(user.id))
    return UserResponse.from_model(user)


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"status": "ok"}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.from_model(user)
