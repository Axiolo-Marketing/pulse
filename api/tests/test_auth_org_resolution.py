"""``get_current_org_member`` active-org resolution tests.

Parametrized scenarios over ``(scenario, expected_status, source)`` to
replace six near-identical hand-written cases with one function. The
priority order under test mirrors what ``auth.middleware`` documents:

  1. API key's ``org_id`` if Bearer was used.
  2. Session payload's ``active_org_id``.
  3. ``users.last_active_org_id`` (with cookie re-issue on the next
     login, but we don't assert the cookie shape here — only that the
     resolution succeeds).

A non-resolving case (no membership, no last_active_org_id) yields 403.

We exercise the resolution by hitting ``GET /api/admin/clients`` because
it is the smallest endpoint protected by ``get_current_org_member``;
any 200 means the dep resolved and the role-flip succeeded.
"""
from __future__ import annotations

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.api_keys import generate_key, hash_key, prefix_of
from pulse_api.auth.session import encode_session
from pulse_api.config import settings


async def _insert_membership(
    db: AsyncSession, *, user_id: str, org_id: str, role: str = "owner"
) -> None:
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), :r) "
            "on conflict (org_id, user_id) do nothing"
        ),
        {"o": org_id, "u": user_id, "r": role},
    )


async def _make_other_org(db: AsyncSession) -> str:
    row = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values ('Acme', :s) returning id::text"
            ),
            {"s": f"acme-{secrets.token_hex(4)}"},
        )
    ).mappings().one()
    return row["id"]


async def _insert_key(
    db: AsyncSession, *, user_id: str, org_id: str
) -> str:
    raw = generate_key()
    await db.execute(
        text(
            "insert into public.api_keys "
            "(user_id, org_id, prefix, key_hash, label) "
            "values (cast(:u as uuid), cast(:o as uuid), :p, :h, 'auth-org-test')"
        ),
        {
            "u": user_id,
            "o": org_id,
            "p": prefix_of(raw),
            "h": hash_key(raw),
        },
    )
    return raw


# ── Parametrized scenarios ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "scenario, expected_status",
    [
        ("cookie_with_active_org", 200),
        ("cookie_no_active_org_user_has_last_active", 200),
        ("cookie_no_org_anywhere", 403),
        ("bearer_only", 200),
        ("bearer_with_stale_cookie_uses_bearer_org", 200),
        ("cookie_active_org_user_not_member", 403),
    ],
    ids=[
        "cookie + active_org payload",
        "cookie without active_org backfills from last_active_org_id",
        "no org info anywhere → 403",
        "bearer key resolves via key.org_id",
        "bearer wins over a cookie for org attribution",
        "session active_org_id user is not a member of → 403",
    ],
)
async def test_active_org_resolution(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    seed_user: dict[str, str],
    scenario: str,
    expected_status: int,
) -> None:
    """Drive every priority branch of ``_resolve_active_org_id`` end-to-end."""
    axiolo_id = seed_admin_user["org_id"]

    if scenario == "cookie_with_active_org":
        client.cookies.set(
            settings.session_cookie_name,
            encode_session(seed_admin_user["id"], axiolo_id),
        )

    elif scenario == "cookie_no_active_org_user_has_last_active":
        # users.last_active_org_id is already set on seed_admin_user.
        # Cookie carries only user_id (back-compat shape).
        client.cookies.set(
            settings.session_cookie_name, encode_session(seed_admin_user["id"])
        )

    elif scenario == "cookie_no_org_anywhere":
        # seed_user has no membership and no last_active_org_id.
        client.cookies.set(
            settings.session_cookie_name, encode_session(seed_user["id"])
        )

    elif scenario == "bearer_only":
        # No cookie. Key minted against Axiolo for the admin user.
        raw = await _insert_key(
            db, user_id=seed_admin_user["id"], org_id=axiolo_id
        )
        client.headers["Authorization"] = f"Bearer {raw}"

    elif scenario == "bearer_with_stale_cookie_uses_bearer_org":
        # Cookie points at a different org (Acme) — the admin user has
        # no membership on Acme, so if the cookie won we'd get 403.
        # But the bearer key targets Axiolo, where membership exists,
        # so the call should succeed.
        acme_id = await _make_other_org(db)
        await db.flush()
        client.cookies.set(
            settings.session_cookie_name,
            encode_session(seed_admin_user["id"], acme_id),
        )
        raw = await _insert_key(
            db, user_id=seed_admin_user["id"], org_id=axiolo_id
        )
        client.headers["Authorization"] = f"Bearer {raw}"

    elif scenario == "cookie_active_org_user_not_member":
        # Cookie points at Acme; user has no membership there.
        acme_id = await _make_other_org(db)
        await db.flush()
        client.cookies.set(
            settings.session_cookie_name,
            encode_session(seed_admin_user["id"], acme_id),
        )

    else:  # pragma: no cover — pytest enforces parametrize ids
        raise AssertionError(f"unknown scenario {scenario!r}")

    r = await client.get("/api/admin/clients")
    assert r.status_code == expected_status, (
        f"{scenario}: got {r.status_code}, body={r.text!r}"
    )
