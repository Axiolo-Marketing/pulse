"""FastAPI dependencies that resolve the current operator User from
either the session cookie OR an `Authorization: Bearer pulse_<key>` header.

Two layers:
- `get_current_user` — required. Raises 401 if neither auth method works.
- `get_current_admin` — additionally requires `is_admin=True`. Raises 403.

These dependencies use the admin DB session (BYPASSRLS) because the
`users` and `api_keys` tables have no RLS — admin sessions are gated
entirely at this auth layer.

Cookie path runs first so existing browser sessions keep working
unchanged. Bearer path runs second so non-browser callers (CLI, CI,
MCP) can authenticate without a cookie jar.

Bearer validation itself lives in `auth.api_keys.verify_bearer` so the
REST middleware and the MCP server (`mcp.server.authenticate_request`)
share one implementation — any future hardening lands in both at once.
"""
from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth import api_keys as api_keys_lib
from pulse_api.auth.session import InvalidSessionError, decode_session
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models import User
from pulse_api.repos import users as users_repo


async def _user_from_cookie(
    pulse_session: str, session: AsyncSession
) -> User | None:
    """Decode the signed session cookie and load the User row, or None."""
    try:
        user_id = decode_session(pulse_session, settings.session_max_age_seconds)
    except InvalidSessionError:
        return None
    return await users_repo.get_user_by_id(session, user_id)


async def _user_from_bearer(
    authorization: str, session: AsyncSession
) -> User | None:
    """Thin wrapper around `api_keys_lib.verify_bearer`.

    Kept for backwards compatibility with internal call sites and tests
    that import it by name. The actual logic lives in
    `auth.api_keys.verify_bearer` so the MCP server can call into the
    same code path without duplicating constant-time compare semantics.
    """
    return await api_keys_lib.verify_bearer(authorization, session)


async def get_current_user(
    pulse_session: str | None = Cookie(default=None, alias=settings.session_cookie_name),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_admin_session),
) -> User:
    if pulse_session:
        user = await _user_from_cookie(pulse_session, session)
        if user is not None:
            return user
        # Fall through to bearer if a bad cookie was sent alongside a
        # valid bearer header — keeps the migration story painless.

    if authorization:
        user = await _user_from_bearer(authorization, session)
        if user is not None:
            return user

    raise HTTPException(status_code=401, detail="not authenticated")


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user
