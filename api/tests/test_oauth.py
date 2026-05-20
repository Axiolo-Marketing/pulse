"""OAuth (Google + Microsoft 365) flow tests.

Both providers are tested by the same parametrized functions — the
provider URLs are the only thing that differs, and the route handler is
already provider-agnostic.

Provider HTTP is mocked via `respx`. ASGI requests through `client` go
through ASGITransport (not intercepted by respx). The route's outbound
`httpx.AsyncClient()` calls hit the real HTTPX transport which respx
patches. So mocking just works.

Important invariant tested: a valid state cookie is REQUIRED at callback.
This is the CSRF defense — without the cookie, an attacker who tricks a
user into hitting a forged callback URL can't drive the flow.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.config import settings


# Map provider → (token_url, userinfo_url) so the parametrized tests can
# register the right respx routes.
PROVIDER_ENDPOINTS = {
    "google": (
        "https://oauth2.googleapis.com/token",
        "https://www.googleapis.com/oauth2/v3/userinfo",
    ),
    "microsoft": (
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "https://graph.microsoft.com/oidc/userinfo",
    ),
}


async def _do_authorize(client: AsyncClient, provider: str) -> tuple[str, str]:
    """Hit /authorize and return (state_param_in_redirect, state_cookie_value)."""
    r = await client.get(f"/api/auth/{provider}/authorize", follow_redirects=False)
    assert r.status_code == 302
    state_q = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    cookie = r.cookies.get(f"oauth_state_{provider}")
    assert cookie, "authorize did not set the oauth_state cookie"
    return state_q, cookie


def _stub_provider(respx_mock: respx.Router, provider: str, *, sub: str, email: str, name: str | None = "Test User") -> None:
    token_url, userinfo_url = PROVIDER_ENDPOINTS[provider]
    respx_mock.post(token_url).mock(
        return_value=httpx.Response(200, json={"access_token": "fake-access-token"})
    )
    respx_mock.get(userinfo_url).mock(
        return_value=httpx.Response(200, json={"sub": sub, "email": email, "name": name})
    )


# ── authorize endpoint ────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_authorize_redirects_to_provider_with_state(
    client: AsyncClient, provider: str
) -> None:
    r = await client.get(f"/api/auth/{provider}/authorize", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    q = parse_qs(urlparse(loc).query)

    assert "state" in q and q["state"][0]
    assert q["response_type"][0] == "code"
    assert q["client_id"][0]
    assert q["scope"][0] == "openid email profile"
    assert r.cookies.get(f"oauth_state_{provider}")


async def test_authorize_unknown_provider_returns_404(client: AsyncClient) -> None:
    r = await client.get("/api/auth/facebook/authorize", follow_redirects=False)
    assert r.status_code == 404


# ── callback: new user creation ───────────────────────────────────────────


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_creates_new_user(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    provider: str,
) -> None:
    state, state_cookie = await _do_authorize(client, provider)
    _stub_provider(respx_mock, provider, sub="provider-sub-1", email="new@example.com", name="New User")

    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"].rstrip("/").endswith("/admin")
    assert r.cookies.get(settings.session_cookie_name)

    user_row = (
        await db.execute(
            text(
                "select id, email, email_verified_at, password_hash, name from public.users "
                "where email='new@example.com'"
            )
        )
    ).mappings().one_or_none()
    assert user_row is not None
    assert user_row["email_verified_at"] is not None  # provider verified
    assert user_row["password_hash"] is None          # OAuth-only
    assert user_row["name"] == "New User"

    identity_row = (
        await db.execute(
            text(
                "select user_id, provider, provider_user_id from public.oauth_identities "
                "where provider=:p and provider_user_id='provider-sub-1'"
            ),
            {"p": provider},
        )
    ).mappings().one_or_none()
    assert identity_row is not None
    assert str(identity_row["user_id"]) == str(user_row["id"])


# ── callback: link to existing email ──────────────────────────────────────


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_links_to_existing_email_user(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    seed_user: dict[str, str],
    provider: str,
) -> None:
    """A user who signed up with email + password who then signs in via
    OAuth (same email) gets the OAuth identity linked to their existing row."""
    state, state_cookie = await _do_authorize(client, provider)
    _stub_provider(respx_mock, provider, sub="provider-sub-2", email=seed_user["email"])

    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302

    # No NEW user — same id as seed_user
    user_count = (
        await db.execute(
            text("select count(*) from public.users where email=:e"), {"e": seed_user["email"]}
        )
    ).scalar()
    assert user_count == 1

    identity_row = (
        await db.execute(
            text(
                "select user_id from public.oauth_identities "
                "where provider=:p and provider_user_id='provider-sub-2'"
            ),
            {"p": provider},
        )
    ).mappings().one_or_none()
    assert identity_row is not None
    assert str(identity_row["user_id"]) == seed_user["id"]


# ── callback: existing identity → log that user in ────────────────────────


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_with_existing_identity_logs_in_same_user(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    seed_user: dict[str, str],
    provider: str,
) -> None:
    """Pre-link an identity to seed_user, then complete OAuth. Expect the
    same user logged in, no new user, no second identity row."""
    await db.execute(
        text(
            "insert into public.oauth_identities (user_id, provider, provider_user_id) "
            "values (cast(:uid as uuid), :p, 'returning-sub')"
        ),
        {"uid": seed_user["id"], "p": provider},
    )

    state, state_cookie = await _do_authorize(client, provider)
    _stub_provider(respx_mock, provider, sub="returning-sub", email=seed_user["email"])

    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302

    identity_count = (
        await db.execute(
            text(
                "select count(*) from public.oauth_identities "
                "where provider=:p and provider_user_id='returning-sub'"
            ),
            {"p": provider},
        )
    ).scalar()
    assert identity_count == 1  # no duplicate identity created


# ── callback: CSRF / state validation ─────────────────────────────────────


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_without_state_cookie_returns_400(
    client: AsyncClient, provider: str
) -> None:
    # Don't call authorize first — no state cookie present.
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": "anything"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "state" in r.json()["detail"].lower()


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_with_mismatched_state_returns_400(
    client: AsyncClient, provider: str
) -> None:
    state, state_cookie = await _do_authorize(client, provider)
    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    # Send a DIFFERENT state in the query — simulates a forged callback URL.
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": "attacker-controlled-state"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "state" in r.json()["detail"].lower()


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_with_garbage_state_cookie_returns_400(
    client: AsyncClient, provider: str
) -> None:
    client.cookies.set(f"oauth_state_{provider}", "not-a-signed-token")
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": "anything"},
        follow_redirects=False,
    )
    assert r.status_code == 400


# ── callback: provider error handling ─────────────────────────────────────


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_when_provider_returns_error(
    client: AsyncClient, respx_mock: respx.Router, provider: str
) -> None:
    state, state_cookie = await _do_authorize(client, provider)
    token_url, _ = PROVIDER_ENDPOINTS[provider]
    respx_mock.post(token_url).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 502


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_when_userinfo_lacks_email(
    client: AsyncClient, respx_mock: respx.Router, provider: str
) -> None:
    state, state_cookie = await _do_authorize(client, provider)
    token_url, userinfo_url = PROVIDER_ENDPOINTS[provider]
    respx_mock.post(token_url).mock(return_value=httpx.Response(200, json={"access_token": "x"}))
    respx_mock.get(userinfo_url).mock(
        return_value=httpx.Response(200, json={"sub": "no-email-sub"})
    )

    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "userinfo" in r.json()["detail"].lower()


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_when_token_response_lacks_access_token(
    client: AsyncClient, respx_mock: respx.Router, provider: str
) -> None:
    state, state_cookie = await _do_authorize(client, provider)
    token_url, _ = PROVIDER_ENDPOINTS[provider]
    respx_mock.post(token_url).mock(
        return_value=httpx.Response(200, json={"oops": "no access token here"})
    )

    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 502


# ── unknown provider ──────────────────────────────────────────────────────


async def test_callback_unknown_provider_returns_404(client: AsyncClient) -> None:
    r = await client.get(
        "/api/auth/twitter/callback",
        params={"code": "x", "state": "y"},
        follow_redirects=False,
    )
    assert r.status_code == 404
