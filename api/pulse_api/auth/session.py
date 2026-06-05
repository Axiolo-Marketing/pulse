"""Signed-cookie session tokens.

Payload carries ``user_id`` and the operator's currently-active org id
(``active_org_id``). The signing key (``SESSION_SECRET``) ensures the
token can't be forged; the embedded timestamp + ``max_age`` check on
decode gives us expiry. No server-side session store — the browser's
cookie IS the session.

Multi-tenant note: ``active_org_id`` is treated as optional during
decode so existing cookies issued before this refactor still authenticate.
The middleware (``auth.middleware.get_current_org_member``) backfills
the missing field from ``users.last_active_org_id`` and re-issues a
fresh cookie, so the migration is transparent — clients keep their
session and only see a refreshed Set-Cookie on the next admin call.

``URLSafeTimedSerializer`` produces base64-url-safe tokens with embedded
timestamps; passing ``max_age`` on ``loads()`` enforces expiry.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from pulse_api.config import settings


class InvalidSessionError(Exception):
    """Raised when a cookie is missing, tampered, or expired."""


def cookie_secure_flag() -> bool:
    """Return True iff cookies should be set Secure-only (HTTPS only).

    Defaults to True in every environment except ``development`` so
    staging and production never accidentally ship cookies over HTTP.
    Tests use the default ``settings.environment = "development"``,
    keeping the existing assertions intact while production gets the
    Secure flag automatically.
    """
    return settings.environment != "development"


def _serializer() -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is not set; refusing to sign sessions.")
    return URLSafeTimedSerializer(settings.session_secret, salt="pulse-session")


def encode_session(
    user_id: str | uuid.UUID,
    active_org_id: str | uuid.UUID | None = None,
) -> str:
    """Sign and return an opaque session token.

    Args:
        user_id: UUID (or its string form) of the authenticated user.
        active_org_id: UUID of the org the operator is currently scoped
            to. Optional — historical cookies didn't carry this and the
            middleware backfills from ``users.last_active_org_id``.

    Returns:
        Opaque signed token suitable as a cookie value.
    """
    payload: dict[str, Any] = {"user_id": str(user_id)}
    if active_org_id is not None:
        payload["active_org_id"] = str(active_org_id)
    return _serializer().dumps(payload)


def decode_session(token: str, max_age_seconds: int) -> str:
    """Decode and return the ``user_id`` from a session token.

    Kept for backward compatibility with callers (and tests) that only
    need the user id. Prefer ``decode_session_payload`` for the full
    multi-tenant payload.

    Args:
        token: Signed session cookie value.
        max_age_seconds: Reject tokens older than this.

    Returns:
        User id as a string.

    Raises:
        InvalidSessionError: If the token is missing, tampered, expired,
            or carries a malformed payload.
    """
    return decode_session_payload(token, max_age_seconds)["user_id"]


def decode_session_payload(token: str, max_age_seconds: int) -> dict[str, Any]:
    """Return the full session payload from a valid, non-expired token.

    ``active_org_id`` is treated as optional — historical cookies
    (pre-multi-tenant) only carry ``user_id`` and the middleware
    backfills from ``users.last_active_org_id`` on the next request.

    Args:
        token: Signed session cookie value.
        max_age_seconds: Reject tokens older than this.

    Returns:
        Dict with at least a ``user_id`` key and optionally
        ``active_org_id``.

    Raises:
        InvalidSessionError: If the token is missing, tampered, expired,
            or carries a malformed payload.
    """
    try:
        data = _serializer().loads(token, max_age=max_age_seconds)
    except SignatureExpired as exc:
        raise InvalidSessionError("session expired") from exc
    except BadSignature as exc:
        raise InvalidSessionError("invalid signature") from exc
    if not isinstance(data, dict) or "user_id" not in data:
        raise InvalidSessionError("malformed payload")
    return data


# ── Convenience helpers used by routes + middleware ─────────────────────


def read_session(request: Request) -> dict[str, Any] | None:
    """Return the decoded session payload from the request cookies.

    Returns ``None`` on a missing/invalid/expired cookie so callers can
    treat "no session" and "bad session" identically (always 401).
    """
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    try:
        return decode_session_payload(raw, settings.session_max_age_seconds)
    except InvalidSessionError:
        return None


def write_session(
    response: Response,
    *,
    user_id: str | uuid.UUID,
    active_org_id: str | uuid.UUID | None,
) -> None:
    """Set the signed session cookie on ``response``.

    Args:
        response: Outgoing response to attach the cookie to.
        user_id: User who just authenticated.
        active_org_id: Org the session is currently scoped to. May be
            ``None`` for users who have no membership yet (the middleware
            will then reject ``/api/admin/*`` calls).
    """
    response.set_cookie(
        key=settings.session_cookie_name,
        value=encode_session(user_id, active_org_id),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        # Secure-only in production; HTTP cookies allowed in dev for local testing
        secure=cookie_secure_flag(),
        path="/",
    )


def clear_session(response: Response) -> None:
    """Delete the session cookie on ``response`` (logout)."""
    response.delete_cookie(key=settings.session_cookie_name, path="/")
