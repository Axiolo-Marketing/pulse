"""Endpoint + bearer-auth tests for per-user API keys.

Covers:
  • POST /api/auth/me/api-keys returns the raw key exactly once.
  • GET  /api/auth/me/api-keys lists prefix metadata only.
  • DELETE /api/auth/me/api-keys/{id} sets revoked_at; revoked key stops working.
  • Bearer-auth path on /api/admin/clients (admin → 200, non-admin → 403,
    revoked → 401, unknown prefix → 401).
  • Cookie auth continues to work after the bearer-or-cookie generalisation.
  • `last_used_at` advances after a successful bearer auth.
  • Cross-user revoke attempt returns 404.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.api_keys import KEY_PREFIX, generate_key, hash_key, prefix_of
from pulse_api.auth.session import encode_session
from pulse_api.config import settings


async def _insert_key(
    db: AsyncSession,
    *,
    user_id: str,
    label: str = "test key",
    revoked: bool = False,
) -> tuple[str, str]:
    """Insert an API key row directly and return (raw_key, key_id).

    Bypasses the POST route so the bearer-auth tests can construct keys
    for arbitrary users (admin + non-admin) without juggling sessions.
    """
    raw = generate_key()
    prefix = prefix_of(raw)
    row = (
        await db.execute(
            text(
                "insert into public.api_keys "
                "(user_id, prefix, key_hash, label, revoked_at) "
                "values (cast(:u as uuid), :p, :h, :l, "
                "  case when :r then now() else null end) "
                "returning id::text"
            ),
            {
                "u": user_id,
                "p": prefix,
                "h": hash_key(raw),
                "l": label,
                "r": revoked,
            },
        )
    ).mappings().one()
    return raw, row["id"]


# 3. POST /api/auth/me/api-keys returns 201 + the raw key once.
async def test_create_api_key_returns_raw_key_once(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.post(
        "/api/auth/me/api-keys", json={"label": "MCP smoke"}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["key"].startswith(KEY_PREFIX)
    assert len(body["key"]) == len(KEY_PREFIX) + 32
    assert body["prefix"] == body["key"][len(KEY_PREFIX):len(KEY_PREFIX) + 8]
    assert body["label"] == "MCP smoke"
    assert "id" in body and uuid.UUID(body["id"])
    assert "created_at" in body
    assert body["last_used_at"] is None


# 4. GET list returns prefix-only metadata, no `key` or `key_hash`.
async def test_list_api_keys_omits_raw_and_hash(
    admin_authed: AsyncClient,
) -> None:
    created = await admin_authed.post(
        "/api/auth/me/api-keys", json={"label": "CI key"}
    )
    assert created.status_code == 201

    r = await admin_authed.get("/api/auth/me/api-keys")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert "key" not in row
    assert "key_hash" not in row
    assert row["label"] == "CI key"
    assert row["prefix"] == created.json()["prefix"]


# 5. Bearer auth for an admin reaches /api/admin/clients (200).
async def test_bearer_admin_key_reaches_admin_endpoint(
    client: AsyncClient, db: AsyncSession, seed_admin_user: dict[str, str]
) -> None:
    raw, _ = await _insert_key(db, user_id=seed_admin_user["id"])
    r = await client.get(
        "/api/admin/clients", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# 6. Bearer key for a non-admin user → 403 on /api/admin/clients.
async def test_bearer_non_admin_key_blocked_from_admin_endpoint(
    client: AsyncClient, db: AsyncSession, seed_user: dict[str, str]
) -> None:
    raw, _ = await _insert_key(db, user_id=seed_user["id"])
    r = await client.get(
        "/api/admin/clients", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 403


# 7. Existing session-cookie auth still works (regression check).
async def test_session_cookie_still_works_on_admin_endpoint(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.get("/api/admin/clients")
    assert r.status_code == 200


# 8. A revoked key → 401.
async def test_revoked_key_returns_401(
    client: AsyncClient, db: AsyncSession, seed_admin_user: dict[str, str]
) -> None:
    raw, _ = await _insert_key(
        db, user_id=seed_admin_user["id"], revoked=True
    )
    r = await client.get(
        "/api/admin/clients", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 401


# 9. Unknown prefix → 401, same as right-prefix-wrong-hash.
async def test_unknown_prefix_and_wrong_hash_both_return_401(
    client: AsyncClient, db: AsyncSession, seed_admin_user: dict[str, str]
) -> None:
    # Right prefix, wrong hash: insert a key whose hash doesn't match
    # the raw value we send on the wire.
    valid_raw, _ = await _insert_key(db, user_id=seed_admin_user["id"])
    # Build a string with the same prefix but a different body so the
    # hash differs.
    same_prefix = valid_raw[:len(KEY_PREFIX) + 8]
    wrong_hash_raw = same_prefix + "0" * 24
    assert len(wrong_hash_raw) == len(valid_raw)
    assert wrong_hash_raw != valid_raw

    r1 = await client.get(
        "/api/admin/clients",
        headers={"Authorization": f"Bearer {wrong_hash_raw}"},
    )
    assert r1.status_code == 401

    # Unknown prefix: a freshly generated key that was never inserted.
    unknown_raw = generate_key()
    r2 = await client.get(
        "/api/admin/clients",
        headers={"Authorization": f"Bearer {unknown_raw}"},
    )
    assert r2.status_code == 401


# 10. `last_used_at` advances after a successful auth.
async def test_last_used_at_advances_after_bearer_auth(
    client: AsyncClient, db: AsyncSession, seed_admin_user: dict[str, str]
) -> None:
    raw, key_id = await _insert_key(db, user_id=seed_admin_user["id"])

    before = (
        await db.execute(
            text(
                "select last_used_at from public.api_keys "
                "where id = cast(:i as uuid)"
            ),
            {"i": key_id},
        )
    ).mappings().one()["last_used_at"]
    assert before is None

    r = await client.get(
        "/api/admin/clients", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 200

    # The middleware's `_touch_last_used` opens a fresh session for the
    # UPDATE (it must not commit on the request's injected session). The
    # `client` fixture monkeypatches that helper to write through the
    # same db_conn the test reads from, so we can read the freshly-written
    # timestamp in this same transaction.
    after = (
        await db.execute(
            text(
                "select last_used_at from public.api_keys "
                "where id = cast(:i as uuid)"
            ),
            {"i": key_id},
        )
    ).mappings().one()["last_used_at"]
    assert after is not None


# 11. DELETE sets revoked_at; the key stops working immediately.
async def test_delete_revokes_key_and_blocks_subsequent_auth(
    client: AsyncClient, db: AsyncSession, seed_admin_user: dict[str, str]
) -> None:
    raw, key_id = await _insert_key(db, user_id=seed_admin_user["id"])

    # Sanity: key works before revoke.
    r = await client.get(
        "/api/admin/clients", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 200

    # Switch to the session-cookie path so DELETE doesn't have to
    # authenticate using a key it's about to nuke.
    client.cookies.set(
        settings.session_cookie_name, encode_session(seed_admin_user["id"])
    )
    r = await client.delete(f"/api/auth/me/api-keys/{key_id}")
    assert r.status_code == 204

    revoked_at = (
        await db.execute(
            text(
                "select revoked_at from public.api_keys "
                "where id = cast(:i as uuid)"
            ),
            {"i": key_id},
        )
    ).mappings().one()["revoked_at"]
    assert revoked_at is not None

    # Clear the cookie so the next request is bearer-only.
    client.cookies.delete(settings.session_cookie_name)
    r = await client.get(
        "/api/admin/clients", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == 401


# 11b. Timing parity: the unknown-prefix path runs the full
#      hash + compare_digest dance, just like the wrong-hash path. The
#      reviewer flagged that an early `if api_key is None: return None`
#      before `verify_key` made the miss path observably faster — this
#      test pins down the structural fix so it can't quietly regress.
async def test_unknown_prefix_runs_hash_and_compare_digest(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pulse_api.auth import api_keys as api_keys_lib
    from pulse_api.auth import middleware as mw
    from pulse_api.repos import api_keys as api_keys_repo

    # Force the lookup to miss for every call.
    async def _always_none(*_a, **_kw):  # noqa: ANN001 — match repo signature loosely
        return None

    monkeypatch.setattr(api_keys_repo, "get_by_prefix", _always_none)

    hash_calls: list[str] = []
    compare_calls: list[tuple[str, str]] = []

    real_hash = api_keys_lib.hash_key

    def _spy_hash(raw: str) -> str:
        hash_calls.append(raw)
        return real_hash(raw)

    real_compare = mw.hmac.compare_digest

    def _spy_compare(a: str, b: str) -> bool:
        compare_calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(api_keys_lib, "hash_key", _spy_hash)
    # Also patch the binding inside the middleware module — the bearer
    # path imports `api_keys as api_keys_lib` so we hit hash_key via that
    # module-level reference.
    monkeypatch.setattr(mw.api_keys_lib, "hash_key", _spy_hash)
    monkeypatch.setattr(mw.hmac, "compare_digest", _spy_compare)

    # Any well-formed `pulse_<32-hex>` will do — the patched lookup
    # always returns None, so we go through the miss path.
    bogus = generate_key()
    r = await client.get(
        "/api/admin/clients", headers={"Authorization": f"Bearer {bogus}"}
    )
    assert r.status_code == 401

    # Both must have been called even though the prefix lookup missed —
    # that's the whole point of the timing-parity fix.
    assert len(hash_calls) >= 1, "hash_key must be called on the miss path"
    assert len(compare_calls) >= 1, "compare_digest must run even on miss"
    # And the compare must be against the 64-char dummy hash, never a
    # short-circuited "" or None — otherwise hmac.compare_digest's
    # length-mismatch fast path leaks timing.
    candidate, stored = compare_calls[0]
    assert len(candidate) == 64
    assert len(stored) == 64


# 12. Another user's key id passed to DELETE returns 404 (no cross-user leak).
async def test_delete_another_users_key_returns_404(
    client: AsyncClient,
    db: AsyncSession,
    seed_user: dict[str, str],
    seed_admin_user: dict[str, str],
) -> None:
    """A non-admin user must not be able to revoke an admin's key by id."""
    _, admin_key_id = await _insert_key(
        db, user_id=seed_admin_user["id"], label="admin's"
    )

    # Authenticate as the non-admin (seed_user) and attempt to delete the
    # admin's key.
    client.cookies.set(
        settings.session_cookie_name, encode_session(seed_user["id"])
    )
    r = await client.delete(f"/api/auth/me/api-keys/{admin_key_id}")
    assert r.status_code == 404

    # Sanity: the row is untouched.
    revoked_at = (
        await db.execute(
            text(
                "select revoked_at from public.api_keys "
                "where id = cast(:i as uuid)"
            ),
            {"i": admin_key_id},
        )
    ).mappings().one()["revoked_at"]
    assert revoked_at is None
