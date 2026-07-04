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

Email-trust gate (audit H6): `_stub_provider` defaults to a *trusted*
identity for both providers (Google's `email_verified: True`, a
Microsoft `tid` on the allowlist set up by the autouse
`_microsoft_allowlist` fixture below) so the existing matrix of
sub-match / email-match / invite-acceptance tests keeps exercising
exactly what it did before this gate existed. The dedicated
`test_google_unverified_*` / `test_microsoft_non_allowlisted_*` tests
further down flip `verified=False` to exercise the gate itself.
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

# A Microsoft Entra ID tenant id the tests pin as "trusted" via
# `settings.microsoft_allowed_tenant_ids` — stands in for an operator
# having pinned a specific customer's workspace tenant.
TRUSTED_MS_TENANT_ID = "11111111-trusted-tenant"


@pytest.fixture(autouse=True)
def _microsoft_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trust `TRUSTED_MS_TENANT_ID` by default so the pre-existing test
    matrix (sub match, email match, invite acceptance) keeps passing for
    Microsoft without every test needing to know about the allowlist.
    Tests exercising the untrusted-tenant rejection override this.
    """
    monkeypatch.setattr(
        settings, "microsoft_allowed_tenant_ids", TRUSTED_MS_TENANT_ID
    )


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


def _stub_provider(
    respx_mock: respx.Router,
    provider: str,
    *,
    sub: str,
    email: str,
    name: str | None = "Test User",
    verified: bool = True,
) -> None:
    """Stub the token + userinfo endpoints for ``provider``.

    ``verified`` controls the email-trust signal: Google's
    ``email_verified`` claim, or whether the Microsoft ``tid`` claim is
    the one ``_microsoft_allowlist`` trusts vs. a different (untrusted)
    tenant — Microsoft never sends ``email_verified`` at all, so `tid`
    is the only lever there.
    """
    token_url, userinfo_url = PROVIDER_ENDPOINTS[provider]
    respx_mock.post(token_url).mock(
        return_value=httpx.Response(200, json={"access_token": "fake-access-token"})
    )
    userinfo: dict = {"sub": sub, "email": email, "name": name}
    if provider == "google":
        userinfo["email_verified"] = verified
    elif provider == "microsoft":
        userinfo["tid"] = (
            TRUSTED_MS_TENANT_ID if verified else "22222222-untrusted-tenant"
        )
    respx_mock.get(userinfo_url).mock(return_value=httpx.Response(200, json=userinfo))


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


# ── callback: unknown email with NO invite must NOT create a user ─────────


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_with_unknown_email_no_invite_redirects_with_error(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    provider: str,
) -> None:
    """Self-signup is disabled in PR 2: an OAuth callback for an
    unknown email with no pending invite must redirect to
    ``/admin/?error=invitation_required`` and create no user.
    """
    state, state_cookie = await _do_authorize(client, provider)
    _stub_provider(
        respx_mock,
        provider,
        sub="provider-sub-1",
        email="stranger@example.com",
        name="Stranger",
    )

    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=invitation_required" in r.headers["location"]
    # Session cookie must NOT be set — the user was never signed in.
    assert r.cookies.get(settings.session_cookie_name) is None

    # And the users table is unchanged.
    user_count = (
        await db.execute(
            text(
                "select count(*) from public.users where email = 'stranger@example.com'"
            )
        )
    ).scalar()
    assert user_count == 0


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_callback_with_pending_invite_creates_user_and_membership(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    axiolo_org: dict[str, str],
    provider: str,
) -> None:
    """Unknown email + pending invite → user created, identity linked,
    membership inserted, invite marked accepted, session cookie set."""
    # Seed an invite for the OAuth-verified email.
    invite_id = (
        await db.execute(
            text(
                "insert into public.organization_invites "
                "(org_id, email, role, token_hash, expires_at) "
                "values (cast(:o as uuid), :e, 'member', :h, now() + interval '7 days') "
                "returning id::text"
            ),
            {
                "o": axiolo_org["id"],
                "e": "invitee@example.com",
                "h": "test-hash-oauth-1",
            },
        )
    ).mappings().one()["id"]
    await db.flush()

    state, state_cookie = await _do_authorize(client, provider)
    _stub_provider(
        respx_mock,
        provider,
        sub="provider-sub-1",
        email="invitee@example.com",
        name="Invitee",
    )

    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=" not in r.headers["location"]
    assert r.cookies.get(settings.session_cookie_name)

    user_row = (
        await db.execute(
            text(
                "select id, email, email_verified_at, password_hash, name "
                "from public.users where email='invitee@example.com'"
            )
        )
    ).mappings().one_or_none()
    assert user_row is not None
    assert user_row["email_verified_at"] is not None
    assert user_row["password_hash"] is None
    assert user_row["name"] == "Invitee"

    membership = (
        await db.execute(
            text(
                "select role, org_id::text from public.organization_memberships "
                "where user_id = cast(:u as uuid)"
            ),
            {"u": str(user_row["id"])},
        )
    ).mappings().one_or_none()
    assert membership is not None
    assert membership["role"] == "member"
    assert membership["org_id"] == axiolo_org["id"]

    accepted = (
        await db.execute(
            text(
                "select accepted_at from public.organization_invites "
                "where id = cast(:i as uuid)"
            ),
            {"i": invite_id},
        )
    ).scalar()
    assert accepted is not None


# ── callback: email-trust gate on the implicit invite path (audit H6) ─────


async def test_google_unverified_email_implicit_invite_rejected(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    axiolo_org: dict[str, str],
) -> None:
    """A pending invite exists, but Google says the email is NOT verified.

    Must redirect with ?error=email_unverified and create no user/
    membership/identity — trusting an unverified email here is exactly
    what would let someone auto-accept an invite meant for someone else.
    """
    await db.execute(
        text(
            "insert into public.organization_invites "
            "(org_id, email, role, token_hash, expires_at) "
            "values (cast(:o as uuid), :e, 'member', :h, now() + interval '7 days')"
        ),
        {
            "o": axiolo_org["id"],
            "e": "unverified@example.com",
            "h": "test-hash-unverified-1",
        },
    )
    await db.flush()

    state, state_cookie = await _do_authorize(client, "google")
    _stub_provider(
        respx_mock,
        "google",
        sub="sub-unverified-1",
        email="unverified@example.com",
        verified=False,
    )
    client.cookies.set("oauth_state_google", state_cookie)
    r = await client.get(
        "/api/auth/google/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=email_unverified" in r.headers["location"]
    assert r.cookies.get(settings.session_cookie_name) is None

    user_count = (
        await db.execute(
            text(
                "select count(*) from public.users where email = 'unverified@example.com'"
            )
        )
    ).scalar()
    assert user_count == 0


async def test_microsoft_non_allowlisted_tenant_implicit_invite_rejected(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    axiolo_org: dict[str, str],
) -> None:
    """Microsoft never sends `email_verified`. With the account's `tid`
    NOT on the (per-operator) allowlist, the implicit email-lookup invite
    path must reject rather than trust the email claim — this is the
    nOAuth shape (personal MS account registered with someone else's
    email) that H6 closes.
    """
    await db.execute(
        text(
            "insert into public.organization_invites "
            "(org_id, email, role, token_hash, expires_at) "
            "values (cast(:o as uuid), :e, 'member', :h, now() + interval '7 days')"
        ),
        {
            "o": axiolo_org["id"],
            "e": "ms-untrusted@example.com",
            "h": "test-hash-ms-untrusted-1",
        },
    )
    await db.flush()

    state, state_cookie = await _do_authorize(client, "microsoft")
    _stub_provider(
        respx_mock,
        "microsoft",
        sub="sub-ms-untrusted-1",
        email="ms-untrusted@example.com",
        verified=False,  # untrusted tid
    )
    client.cookies.set("oauth_state_microsoft", state_cookie)
    r = await client.get(
        "/api/auth/microsoft/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=email_unverified" in r.headers["location"]
    assert r.cookies.get(settings.session_cookie_name) is None

    user_count = (
        await db.execute(
            text(
                "select count(*) from public.users where email = 'ms-untrusted@example.com'"
            )
        )
    ).scalar()
    assert user_count == 0


async def test_microsoft_non_allowlisted_tenant_explicit_invite_still_works(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    axiolo_org: dict[str, str],
) -> None:
    """The explicit invite-token-in-state path carries its own signed
    proof of intent, so it must keep working for Microsoft even when the
    account's tenant is nowhere near the allowlist."""
    from pulse_api.auth.tokens import issue_token
    from pulse_api.repos.invites import hash_invite_token

    invite_email = "ms-explicit@example.com"
    invite_id = (
        await db.execute(
            text(
                "insert into public.organization_invites "
                "(org_id, email, role, token_hash, expires_at, accepted_at) "
                "values (cast(:o as uuid), :e, 'member', :h, "
                "        now() + interval '7 days', null) "
                "returning id::text"
            ),
            {
                "o": axiolo_org["id"],
                "e": invite_email,
                "h": "placeholder-ms-explicit-1",
            },
        )
    ).mappings().one()["id"]
    raw_invite_token = issue_token("org-invite", {"invite_id": invite_id})
    await db.execute(
        text(
            "update public.organization_invites set token_hash = :h "
            "where id = cast(:i as uuid)"
        ),
        {"h": hash_invite_token(raw_invite_token), "i": invite_id},
    )
    await db.flush()

    r = await client.get(
        "/api/auth/microsoft/authorize",
        params={"invite_token": raw_invite_token},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    state_cookie = r.cookies.get("oauth_state_microsoft")

    # Untrusted tenant AND a different email than the invite — the
    # explicit token is what wins here, not the email match.
    _stub_provider(
        respx_mock,
        "microsoft",
        sub="sub-ms-explicit-1",
        email="different-oauth-email@example.com",
        verified=False,
    )
    client.cookies.set("oauth_state_microsoft", state_cookie)
    r2 = await client.get(
        "/api/auth/microsoft/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r2.status_code == 302, r2.text
    assert "error=" not in r2.headers["location"], r2.headers
    assert r2.cookies.get(settings.session_cookie_name)

    membership = (
        await db.execute(
            text(
                "select m.role, m.org_id::text from public.organization_memberships m "
                "join public.users u on u.id = m.user_id "
                "where lower(u.email) = lower(:e)"
            ),
            {"e": "different-oauth-email@example.com"},
        )
    ).mappings().one_or_none()
    assert membership is not None
    assert membership["role"] == "member"
    assert membership["org_id"] == axiolo_org["id"]


@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_untrusted_email_matching_existing_user_is_rejected(
    client: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    seed_user: dict[str, str],
    provider: str,
) -> None:
    """The existing-user-by-email match is itself an email-based path —
    an untrusted email must not get a brand-new provider identity linked
    to somebody's existing account (nOAuth account takeover)."""
    state, state_cookie = await _do_authorize(client, provider)
    _stub_provider(
        respx_mock,
        provider,
        sub="untrusted-sub-1",
        email=seed_user["email"],
        verified=False,
    )
    client.cookies.set(f"oauth_state_{provider}", state_cookie)
    r = await client.get(
        f"/api/auth/{provider}/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=email_unverified" in r.headers["location"]
    assert r.cookies.get(settings.session_cookie_name) is None

    identity_count = (
        await db.execute(
            text(
                "select count(*) from public.oauth_identities "
                "where provider=:p and provider_user_id='untrusted-sub-1'"
            ),
            {"p": provider},
        )
    ).scalar()
    assert identity_count == 0


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


# ── unit: id_token claim decoding + tenant allowlist parsing ───────────────


def _fake_jwt(payload: dict) -> str:
    """Build a syntactically-valid (unsigned) JWT for decode tests —
    header/signature content is irrelevant since we never verify it."""
    import base64
    import json

    def _b64(obj: dict | bytes) -> str:
        raw = json.dumps(obj).encode() if isinstance(obj, dict) else obj
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{_b64({'alg': 'none'})}.{_b64(payload)}.fake-signature"


def test_decode_id_token_claims_reads_tid() -> None:
    from pulse_api.auth.oauth import decode_id_token_claims

    token = _fake_jwt({"tid": "some-tenant-id", "email_verified": True})
    claims = decode_id_token_claims(token)
    assert claims["tid"] == "some-tenant-id"
    assert claims["email_verified"] is True


@pytest.mark.parametrize(
    "garbage",
    ["", "not-a-jwt", "only.two", "a.b.c.d", "not-base64!!.also-not!!.x"],
)
def test_decode_id_token_claims_returns_empty_dict_for_garbage(
    garbage: str,
) -> None:
    """Malformed input degrades to `{}` (untrusted) rather than raising —
    a missing/broken auxiliary claim must not 500 the callback."""
    from pulse_api.auth.oauth import decode_id_token_claims

    assert decode_id_token_claims(garbage) == {}


def test_microsoft_allowed_tenant_ids_parses_comma_and_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pulse_api.auth.oauth import microsoft_allowed_tenant_ids

    monkeypatch.setattr(
        settings, "microsoft_allowed_tenant_ids", " Tenant-A, tenant-b\ttenant-c "
    )
    assert microsoft_allowed_tenant_ids() == {"tenant-a", "tenant-b", "tenant-c"}


def test_microsoft_allowed_tenant_ids_empty_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pulse_api.auth.oauth import microsoft_allowed_tenant_ids

    monkeypatch.setattr(settings, "microsoft_allowed_tenant_ids", "")
    assert microsoft_allowed_tenant_ids() == set()


async def test_fetch_userinfo_merges_tid_from_id_token(
    respx_mock: respx.Router,
) -> None:
    """Microsoft's `/oidc/userinfo` never sends `tid` — `fetch_userinfo`
    must pull it from the (unverified) id_token when userinfo omits it,
    since that's the only signal `_email_trust_ok` has for Microsoft."""
    from pulse_api.auth.oauth import microsoft_provider

    provider = microsoft_provider()
    respx_mock.get(provider.userinfo_url).mock(
        return_value=httpx.Response(
            200, json={"sub": "s1", "email": "a@example.com"}
        )
    )
    id_token = _fake_jwt({"tid": "tenant-from-id-token"})
    userinfo = await provider.fetch_userinfo("access-tok", id_token=id_token)
    assert userinfo["tid"] == "tenant-from-id-token"


async def test_fetch_userinfo_userinfo_response_wins_over_id_token(
    respx_mock: respx.Router,
) -> None:
    """If userinfo itself already carries a claim, the id_token merge
    must not clobber it."""
    from pulse_api.auth.oauth import microsoft_provider

    provider = microsoft_provider()
    respx_mock.get(provider.userinfo_url).mock(
        return_value=httpx.Response(
            200,
            json={"sub": "s1", "email": "a@example.com", "tid": "from-userinfo"},
        )
    )
    id_token = _fake_jwt({"tid": "from-id-token"})
    userinfo = await provider.fetch_userinfo("access-tok", id_token=id_token)
    assert userinfo["tid"] == "from-userinfo"
