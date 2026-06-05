"""Endpoint tests for the email + password auth flow.

Covers: POST /api/auth/signup, /login, /logout, GET /api/auth/me.

OAuth + email verification + password reset are tested in their own
modules in the next phase.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.config import settings


@pytest.fixture(autouse=True)
def _enable_signup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open the signup gate for this module.

    Production is invite-only (`settings.signup_enabled = False`). The
    email+password tests below predate the gate; they exercise the
    signup path itself, so they opt in.
    """
    monkeypatch.setattr(settings, "signup_enabled", True)


# ── signup ────────────────────────────────────────────────────────────────


async def test_signup_disabled_returns_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invite-only default. Locks in the production policy."""
    monkeypatch.setattr(settings, "signup_enabled", False)
    r = await client.post(
        "/api/auth/signup",
        json={"email": "x@example.com", "password": "long-enough-password"},
    )
    assert r.status_code == 404


async def test_signup_creates_unverified_user(client: AsyncClient, db: AsyncSession) -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"email": "new@example.com", "password": "long-enough-password", "name": "New"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@example.com"
    assert body["name"] == "New"
    assert body["is_superadmin"] is False
    assert body["email_verified_at"] is None

    row = (
        await db.execute(text("select email, password_hash from public.users where email='new@example.com'"))
    ).mappings().one()
    assert row["password_hash"].startswith("$argon2id$")


async def test_signup_duplicate_email_returns_409(client: AsyncClient, seed_user: dict[str, str]) -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"email": seed_user["email"], "password": "another-long-password"},
    )
    assert r.status_code == 409


@pytest.mark.parametrize(
    "payload, expected_status",
    [
        ({"email": "not-an-email", "password": "long-enough-password"}, 422),
        ({"email": "ok@example.com", "password": "short"}, 422),  # min_length 8
        ({"password": "long-enough-password"}, 422),              # missing email
        ({"email": "ok@example.com"}, 422),                       # missing password
    ],
)
async def test_signup_validation_rejects_bad_input(
    client: AsyncClient, payload: dict, expected_status: int
) -> None:
    r = await client.post("/api/auth/signup", json=payload)
    assert r.status_code == expected_status


# ── login ─────────────────────────────────────────────────────────────────


async def test_login_with_verified_credentials_sets_cookie(
    client: AsyncClient, seed_user: dict[str, str]
) -> None:
    r = await client.post(
        "/api/auth/login",
        json={"email": seed_user["email"], "password": seed_user["password"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == seed_user["email"]
    cookie = r.cookies.get(settings.session_cookie_name)
    assert cookie, "login did not set session cookie"


async def test_login_with_wrong_password_returns_401(
    client: AsyncClient, seed_user: dict[str, str]
) -> None:
    r = await client.post(
        "/api/auth/login",
        json={"email": seed_user["email"], "password": "definitely-wrong"},
    )
    assert r.status_code == 401
    assert r.cookies.get(settings.session_cookie_name) is None


async def test_login_with_unknown_email_returns_401(client: AsyncClient) -> None:
    r = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "anything-long-enough"},
    )
    assert r.status_code == 401


async def test_login_blocked_for_unverified_email(
    client: AsyncClient, db: AsyncSession
) -> None:
    # Create a user with email_verified_at = NULL
    from pulse_api.auth.password import hash_password

    pw = "long-enough-password"
    await db.execute(
        text(
            "insert into public.users (email, password_hash, email_verified_at) "
            "values (:e, :h, null)"
        ),
        {"e": "unverified@example.com", "h": hash_password(pw)},
    )
    r = await client.post(
        "/api/auth/login",
        json={"email": "unverified@example.com", "password": pw},
    )
    assert r.status_code == 403
    assert "verified" in r.json()["detail"].lower()


async def test_login_for_oauth_only_user_rejects_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A user with no password_hash (created via OAuth) must not be loggable
    via password — would otherwise allow login with empty/any password."""
    await db.execute(
        text(
            "insert into public.users (email, password_hash, email_verified_at) "
            "values ('oauth@example.com', null, now())"
        )
    )
    r = await client.post(
        "/api/auth/login",
        json={"email": "oauth@example.com", "password": "anything-long-enough"},
    )
    assert r.status_code == 401


# ── me ────────────────────────────────────────────────────────────────────


async def test_me_without_cookie_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_me_with_valid_cookie_returns_user(
    admin_authed: AsyncClient, seed_admin_user: dict[str, str]
) -> None:
    r = await admin_authed.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == seed_admin_user["id"]
    assert body["email"] == seed_admin_user["email"]
    # The admin role moved off the user row onto org membership in PR 2;
    # ``/api/auth/me`` no longer surfaces it. Membership role + active
    # org are exposed in PR 3's ``/api/me/orgs`` instead.
    assert body["active_org_id"] == seed_admin_user["org_id"]


@pytest.mark.parametrize(
    "bad_value",
    ["not-a-session-token", "tampered.value.here", ""],
)
async def test_me_with_invalid_cookie_returns_401(
    client: AsyncClient, bad_value: str
) -> None:
    client.cookies.set(settings.session_cookie_name, bad_value)
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_me_with_cookie_for_nonexistent_user_returns_401(
    client: AsyncClient,
) -> None:
    """A signed but stale cookie (user was deleted) must not authenticate."""
    from pulse_api.auth.session import encode_session

    token = encode_session("00000000-0000-0000-0000-000000000000")
    client.cookies.set(settings.session_cookie_name, token)
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


# ── logout ────────────────────────────────────────────────────────────────


async def test_logout_clears_cookie(admin_authed: AsyncClient) -> None:
    r = await admin_authed.post("/api/auth/logout")
    assert r.status_code == 200
    # Assert the server sent the deletion directive. We inspect the response
    # header rather than the client cookie jar — httpx's jar key includes
    # domain, and the fixture-set cookie has a different domain than the
    # response cookie, so they don't collide in the jar. A real browser
    # would key on (name, path) and honor the Max-Age=0 expiration.
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert settings.session_cookie_name in set_cookie
    assert "max-age=0" in set_cookie


async def test_logout_works_without_auth(client: AsyncClient) -> None:
    """Logout is idempotent — calling it without a session is still 200."""
    r = await client.post("/api/auth/logout")
    assert r.status_code == 200


# ── full round-trip ───────────────────────────────────────────────────────


async def test_full_signup_verify_login_me_logout_flow(
    client: AsyncClient, captured_emails: list
) -> None:
    import re

    # 1. Signup — verification email goes out
    r = await client.post(
        "/api/auth/signup",
        json={"email": "tom@axiolo.example", "password": "long-enough-password"},
    )
    assert r.status_code == 201

    # 2. Login is blocked until verified
    r = await client.post(
        "/api/auth/login",
        json={"email": "tom@axiolo.example", "password": "long-enough-password"},
    )
    assert r.status_code == 403

    # 3. User clicks the verification link
    m = re.search(r"/verify-email\?token=([^\s]+)", captured_emails[0].body)
    assert m, "no verification link in email"
    r = await client.post("/api/auth/verify-email", json={"token": m.group(1)})
    assert r.status_code == 200

    # 4. Login now works
    r = await client.post(
        "/api/auth/login",
        json={"email": "tom@axiolo.example", "password": "long-enough-password"},
    )
    assert r.status_code == 200
    assert r.cookies.get(settings.session_cookie_name)

    # 5. /me reflects the session
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "tom@axiolo.example"

    # 6. Logout drops the session
    r = await client.post("/api/auth/logout")
    assert r.status_code == 200

    # 7. /me is back to 401 once the browser drops the cleared cookie
    client.cookies.delete(settings.session_cookie_name)
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


# ── profile update ────────────────────────────────────────────────────────


async def test_me_includes_has_password(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["has_password"] is True


async def test_update_profile_changes_name(
    admin_authed: AsyncClient, db: AsyncSession, seed_admin_user: dict[str, str]
) -> None:
    r = await admin_authed.patch("/api/auth/me", json={"name": "  New Name  "})
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"

    row = (
        await db.execute(
            text("select name from public.users where id = cast(:i as uuid)"),
            {"i": seed_admin_user["id"]},
        )
    ).mappings().one()
    assert row["name"] == "New Name"


async def test_update_profile_clears_name_when_blank(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.patch("/api/auth/me", json={"name": "   "})
    assert r.status_code == 200
    assert r.json()["name"] is None


async def test_update_profile_requires_auth(client: AsyncClient) -> None:
    r = await client.patch("/api/auth/me", json={"name": "Spoof"})
    assert r.status_code == 401


# ── change password ──────────────────────────────────────────────────────


async def test_change_password_with_correct_current_succeeds(
    admin_authed: AsyncClient,
    seed_admin_user: dict[str, str],
    db: AsyncSession,
) -> None:
    from pulse_api.auth.password import verify_password

    new_pw = "brand-new-password"
    r = await admin_authed.post(
        "/api/auth/change-password",
        json={
            "current_password": seed_admin_user["password"],
            "new_password": new_pw,
        },
    )
    assert r.status_code == 200

    row = (
        await db.execute(
            text("select password_hash from public.users where id = cast(:i as uuid)"),
            {"i": seed_admin_user["id"]},
        )
    ).mappings().one()
    assert verify_password(new_pw, row["password_hash"])


async def test_change_password_with_wrong_current_returns_400(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.post(
        "/api/auth/change-password",
        json={"current_password": "definitely-wrong", "new_password": "new-password-123"},
    )
    assert r.status_code == 400


async def test_change_password_without_current_when_user_has_one_returns_400(
    admin_authed: AsyncClient,
) -> None:
    """A user with a password set must supply current_password to change it.
    Otherwise a stolen session cookie could quietly rotate the password."""
    r = await admin_authed.post(
        "/api/auth/change-password",
        json={"new_password": "another-password-123"},
    )
    assert r.status_code == 400


async def test_oauth_only_user_can_set_initial_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    """OAuth-only users (password_hash=NULL) can set a first password
    without supplying current_password — the session cookie + OAuth
    completion is the auth gate."""
    from pulse_api.auth.password import verify_password
    from pulse_api.auth.session import encode_session

    row = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, email_verified_at) "
                "values ('oauth@example.com', null, now()) "
                "returning id::text"
            )
        )
    ).mappings().one()
    client.cookies.set(settings.session_cookie_name, encode_session(row["id"]))

    # /me reports has_password = false before
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["has_password"] is False

    new_pw = "initial-cli-password"
    r = await client.post(
        "/api/auth/change-password",
        json={"new_password": new_pw},
    )
    assert r.status_code == 200
    assert r.json()["has_password"] is True

    stored = (
        await db.execute(
            text(
                "select password_hash from public.users where id = cast(:i as uuid)"
            ),
            {"i": row["id"]},
        )
    ).mappings().one()
    assert verify_password(new_pw, stored["password_hash"])

    # And now password login works as well
    r = await client.post(
        "/api/auth/login",
        json={"email": "oauth@example.com", "password": new_pw},
    )
    assert r.status_code == 200


async def test_change_password_requires_min_length(
    admin_authed: AsyncClient, seed_admin_user: dict[str, str]
) -> None:
    r = await admin_authed.post(
        "/api/auth/change-password",
        json={
            "current_password": seed_admin_user["password"],
            "new_password": "short",
        },
    )
    assert r.status_code == 422


async def test_change_password_requires_auth(client: AsyncClient) -> None:
    r = await client.post(
        "/api/auth/change-password",
        json={"new_password": "long-enough-password"},
    )
    assert r.status_code == 401


# ── identities ────────────────────────────────────────────────────────────


async def test_list_identities_empty_for_email_signup(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.get("/api/auth/me/identities")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_identities_returns_linked_providers(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    await db.execute(
        text(
            "insert into public.oauth_identities (user_id, provider, provider_user_id) "
            "values (cast(:u as uuid), 'google', 'google-12345')"
        ),
        {"u": seed_admin_user["id"]},
    )
    r = await admin_authed.get("/api/auth/me/identities")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["provider"] == "google"
    assert "linked_at" in body[0]


async def test_list_identities_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/api/auth/me/identities")
    assert r.status_code == 401
