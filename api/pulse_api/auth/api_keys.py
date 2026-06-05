"""Per-user API-key primitives — generate, prefix-extract, hash, verify.

Stdlib-plus-SQLAlchemy. Keys carry 128 bits of entropy
(`secrets.token_hex(16)`), so a plain SHA-256 + constant-time compare is
the right pattern — argon2's slow comparison is wasted on full-entropy
material.

Format: `pulse_<32-hex>`. The `pulse_` literal makes leaked keys greppable
in logs and source repos. The first 8 hex chars after the underscore go in
an indexed `prefix` column so auth can fetch a single candidate row before
running the constant-time hash compare.

`verify_bearer` is the single source of truth for resolving an
`Authorization: Bearer pulse_<key>` header to a User row. Both the REST
middleware (`auth.middleware._user_from_bearer`) and the MCP server
(`mcp.server.authenticate_request`) call into it so any future hardening
lands in both paths at once.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.db import admin_engine
from pulse_api.models import ApiKey, User
from pulse_api.repos import api_keys as api_keys_repo
from pulse_api.repos import users as users_repo

KEY_PREFIX = "pulse_"
RAW_HEX_LEN = 32
PREFIX_LEN = 8

_BEARER_PREFIX = "Bearer "

# 64-char dummy SHA-256-hex used on the miss path so that the constant-time
# compare runs in both branches. Length matches a real hash so
# `hmac.compare_digest` doesn't short-circuit on len mismatch.
_DUMMY_HASH = "0" * 64


def generate_key() -> str:
    """Return a fresh `pulse_<32-hex>` key. 128 bits of entropy."""
    return f"{KEY_PREFIX}{secrets.token_hex(RAW_HEX_LEN // 2)}"


def prefix_of(raw: str) -> str:
    """Return the 8-char prefix used for indexed lookup.

    Raises ValueError if `raw` doesn't match the expected `pulse_<32-hex>`
    shape — callers in the auth path catch this and return 401 rather than
    leaking the bug as a 500.
    """
    if not raw.startswith(KEY_PREFIX):
        raise ValueError("key missing 'pulse_' prefix")
    body = raw[len(KEY_PREFIX):]
    if len(body) != RAW_HEX_LEN:
        raise ValueError("key body must be 32 hex chars")
    return body[:PREFIX_LEN]


def hash_key(raw: str) -> str:
    """SHA-256 hex of the raw key. The only form we store on disk."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_key(raw: str, stored_hash: str) -> bool:
    """Constant-time compare against a stored hash.

    Returns False (never raises) for any malformed input — the auth path
    feeds attacker-controlled strings through here.
    """
    try:
        candidate = hash_key(raw)
    except (AttributeError, TypeError):
        return False
    return hmac.compare_digest(candidate, stored_hash)


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


async def verify_bearer(
    authorization: str,
    session: AsyncSession,
) -> tuple[User, ApiKey] | None:
    """Resolve ``Authorization: Bearer pulse_<key>`` to (User, ApiKey), or None.

    Returns the ApiKey alongside the user so the auth layer can read
    ``api_key.org_id`` for the role-flip — the key, not the cookie,
    determines which org a Bearer-authenticated request is scoped to.

    Single source of truth for bearer validation — both the REST
    middleware and the MCP server call into this. Constant-time hash
    compare with a dummy 64-char hex on the prefix-miss path so timing
    observers can't distinguish unknown prefix from wrong hash. On
    success, schedules ``last_used_at`` on a fresh short-lived session
    so the caller's session is never committed.
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
    candidate_hash = hash_key(raw_key)
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
    # the caller's session which a route handler or MCP tool will continue
    # to use. Committing it here would close that transaction prematurely.
    await _touch_last_used(api_key.id)
    return user, api_key
