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
"""
import hmac

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth import api_keys as api_keys_lib
from pulse_api.auth.api_keys import KEY_PREFIX, prefix_of
from pulse_api.auth.session import InvalidSessionError, decode_session
from pulse_api.config import settings
from pulse_api.db import admin_engine, get_admin_session
from pulse_api.models import User
from pulse_api.repos import api_keys as api_keys_repo
from pulse_api.repos import users as users_repo


_BEARER_PREFIX = "Bearer "

# 64-char dummy SHA-256-hex used on the miss path so that the constant-time
# compare runs in both branches. Length matches a real hash so
# `hmac.compare_digest` doesn't short-circuit on len mismatch.
_DUMMY_HASH = "0" * 64


async def _touch_last_used(api_key_id) -> None:  # type: ignore[no-untyped-def]
    """Open a fresh admin-engine session and bump `last_used_at` once.

    Lives at module scope so tests can monkeypatch it — production code
    talks to a brand-new connection that is independent of the request's
    injected session. Best-effort: any failure is swallowed because a
    failed touch should not turn an otherwise-authenticated request into
    a 500.
    """
    try:
        async with AsyncSession(admin_engine, expire_on_commit=False) as touch_session:
            await api_keys_repo.mark_used(touch_session, api_key_id)
            await touch_session.commit()
    except Exception:
        pass


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
    """Resolve `Authorization: Bearer pulse_<key>` to a User row, or None.

    The lookup is indexed by prefix so we read one row before doing the
    constant-time hash compare. Unknown prefix and "right prefix wrong
    hash" follow the SAME code path — we always compute the candidate hash
    and always run `hmac.compare_digest` against a real-length stored
    hash (a dummy 64-char hex on the miss path) so timing observers can't
    distinguish "no such prefix" from "wrong hash for prefix".

    On success, updates `last_used_at` best-effort on a brand-new
    short-lived session — never on the request's injected session, which
    the route handler then re-uses for its own transactional work.
    """
    if not authorization.startswith(_BEARER_PREFIX):
        return None
    raw_key = authorization[len(_BEARER_PREFIX):].strip()
    if not raw_key.startswith(KEY_PREFIX):
        return None
    try:
        prefix = prefix_of(raw_key)
    except ValueError:
        return None

    api_key = await api_keys_repo.get_by_prefix(session, prefix)
    # Always hash and always constant-time-compare. On the miss path we
    # compare against a dummy hash so the work matches the hit-then-fail
    # path. Do NOT early-return on `api_key is None` before this work.
    candidate_hash = api_keys_lib.hash_key(raw_key)
    stored = api_key.key_hash if api_key is not None else _DUMMY_HASH
    if not hmac.compare_digest(candidate_hash, stored):
        return None
    if api_key is None:
        # Prefix lookup miss — same outcome as hash compare miss, just got
        # here via the dummy hash path so timing matches.
        return None

    user = await users_repo.get_user_by_id(session, api_key.user_id)
    if user is None:
        return None

    # Best-effort `last_used_at` touch on a fresh session bound to a brand
    # new connection. Critically, we do NOT commit on `session` — that's
    # the request-scoped session the route handler will continue to use.
    # Committing it here would close the route's transaction prematurely.
    await _touch_last_used(api_key.id)
    return user


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
