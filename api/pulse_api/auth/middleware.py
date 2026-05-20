"""FastAPI dependencies that resolve the current operator User from the session cookie.

Two layers:
- `get_current_user` — required. Raises 401 if cookie missing/invalid/expired.
- `get_current_admin` — additionally requires `is_admin=True`. Raises 403 otherwise.

These dependencies use the admin DB session (BYPASSRLS) because the `users`
table has no RLS — admin sessions are gated entirely at this auth layer.
"""
from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.session import InvalidSessionError, decode_session
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models import User
from pulse_api.repos import users as users_repo


async def get_current_user(
    pulse_session: str | None = Cookie(default=None, alias=settings.session_cookie_name),
    session: AsyncSession = Depends(get_admin_session),
) -> User:
    if not pulse_session:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        user_id = decode_session(pulse_session, settings.session_max_age_seconds)
    except InvalidSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = await users_repo.get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user
