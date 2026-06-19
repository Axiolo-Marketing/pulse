"""End-to-end tests for the root-mounted OAuth 2.1 surface (PR 2).

Drives the full HTTP flow through ``ASGITransport(app)`` (the same harness
as ``test_mcp.py``):

  * discovery docs — PRM + AS metadata shapes;
  * Dynamic Client Registration (``POST /register``);
  * ``GET /authorize`` → 302 to the Pulse consent page;
  * consent GET without a session → 302 to ``/admin/?return_to=``;
  * consent GET with a seeded session → org-picker HTML;
  * consent POST approve → 302 to the client redirect URI with a ``code``;
  * ``POST /token`` exchanges that code (correct PKCE verifier) for an
    access + refresh token; a wrong verifier → 400;
  * ``GET /api/mcp/`` with NO Authorization → 401 + ``WWW-Authenticate``
    carrying ``resource_metadata=``;
  * a ``tools/call`` works with the issued OAuth access token AND with a
    legacy ``pulse_<key>`` (regression).

The AS routes + consent page open their own ``pulse_admin`` sessions
(``routes._admin_session`` / ``provider._admin_session`` /
``verifier._admin_session``); all three are monkeypatched through the
test's rolled-back connection so writes are visible to the test ``db``
session and wiped at teardown.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

from pulse_api.auth.api_keys import generate_key, hash_key, prefix_of
from pulse_api.auth.session import encode_session
from pulse_api.config import settings
from pulse_api.main import app

MCP_PATH = "/api/mcp/"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


# ── PKCE helpers ───────────────────────────────────────────────────────────


def _pkce_pair() -> tuple[str, str]:
    """Return a ``(code_verifier, code_challenge)`` S256 PKCE pair."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _pin_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin issuer/resource + session secret so audience checks are stable.

    Uses an https host so the issuer passes the SDK's HTTPS check and the
    resource URL matches what the provider binds grants to.
    """
    monkeypatch.setattr(settings, "mcp_issuer_url", "https://pulse.axiolo.com")
    monkeypatch.setattr(settings, "frontend_base_url", "https://pulse.axiolo.com")
    monkeypatch.setattr(settings, "session_secret", "test-secret-please-32-bytes-min")


@pytest.fixture
async def oauth_runtime(
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    mcp_session_manager: None,
) -> None:
    """Bind every OAuth admin-session factory through ``db_conn``.

    The AS routes, the provider, the verifier, the consent page, and the
    MCP tool layer each open their own short-lived admin/member sessions
    in production. Route them all through the rolled-back connection so
    the flow shares state with the test's ``db`` fixture.
    """
    @asynccontextmanager
    async def _override_admin() -> AsyncIterator[AsyncSession]:
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    @asynccontextmanager
    async def _override_member(org_id: str) -> AsyncIterator[AsyncSession]:
        await db_conn.execute(text("reset role"))
        await db_conn.execute(text("set local role pulse_member"))
        await db_conn.execute(
            text("select set_config('pulse.org_id', :o, true)"),
            {"o": str(org_id)},
        )
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    from pulse_api.mcp import server as mcp_server
    from pulse_api.mcp import tools as mcp_tools
    from pulse_api.mcp.oauth import provider as provider_mod
    from pulse_api.mcp.oauth import routes as routes_mod
    from pulse_api.mcp.oauth import verifier as verifier_mod

    monkeypatch.setattr(provider_mod, "_admin_session", _override_admin)
    monkeypatch.setattr(verifier_mod, "_admin_session", _override_admin)
    monkeypatch.setattr(routes_mod, "_admin_session", _override_admin)
    monkeypatch.setattr(mcp_server, "_open_admin_session", _override_admin)
    monkeypatch.setattr(mcp_server, "_open_member_session", _override_member)
    monkeypatch.setattr(mcp_tools, "_open_member_session", _override_member)

    from pulse_api.auth import api_keys as _api_keys_lib
    from pulse_api.repos import api_keys as _api_keys_repo

    async def _patched_touch_last_used(api_key_id) -> None:  # type: ignore[no-untyped-def]
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as touch_session:
            await _api_keys_repo.mark_used(touch_session, api_key_id)
            await touch_session.commit()

    monkeypatch.setattr(_api_keys_lib, "_touch_last_used", _patched_touch_last_used)


@pytest.fixture
async def oauth_client(
    db_conn: AsyncConnection, oauth_runtime: None
) -> AsyncIterator[AsyncClient]:
    """An httpx client over the ASGI app sharing the test transaction.

    We don't reuse conftest's ``client`` fixture here because these tests
    don't need its REST DI overrides, and a fresh client avoids leaking
    cookies between cases.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_client(c: AsyncClient) -> str:
    """Register a public DCR client and return its ``client_id``."""
    resp = await c.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "Claude",
            "scope": "mcp",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client_id"]


async def _insert_admin_key(
    db: AsyncSession, *, user_id: str, org_id: str
) -> str:
    """Insert an MCP API key for ``(user, org)`` and return its raw value."""
    raw = generate_key()
    await db.execute(
        text(
            "insert into public.api_keys "
            "(user_id, org_id, prefix, key_hash, label) "
            "values (cast(:u as uuid), cast(:o as uuid), :p, :h, :l)"
        ),
        {
            "u": user_id,
            "o": org_id,
            "p": prefix_of(raw),
            "h": hash_key(raw),
            "l": "oauth regression",
        },
    )
    return raw


# ── discovery ───────────────────────────────────────────────────────────────


async def test_protected_resource_metadata_shape(
    oauth_client: AsyncClient,
) -> None:
    resp = await oauth_client.get(
        "/.well-known/oauth-protected-resource/api/mcp"
    )
    assert resp.status_code == 200
    body = resp.json()
    # The PRM doc is built from the import-time issuer config (prod env
    # sets the real host before import). We assert structure, not the
    # monkeypatched value: resource ends at the MCP path, with one AS.
    assert body["resource"].rstrip("/").endswith("/api/mcp")
    assert len(body["authorization_servers"]) == 1
    assert body["scopes_supported"] == ["mcp"]


async def test_authorization_server_metadata_shape(
    oauth_client: AsyncClient,
) -> None:
    resp = await oauth_client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorization_endpoint"].endswith("/authorize")
    assert body["token_endpoint"].endswith("/token")
    assert body["registration_endpoint"].endswith("/register")
    assert body["code_challenge_methods_supported"] == ["S256"]


# ── DCR ─────────────────────────────────────────────────────────────────────


async def test_dynamic_client_registration_returns_client_id(
    oauth_client: AsyncClient,
) -> None:
    client_id = await _register_client(oauth_client)
    assert client_id


# ── authorize → consent ──────────────────────────────────────────────────────


async def test_authorize_redirects_to_consent(
    oauth_client: AsyncClient,
) -> None:
    client_id = await _register_client(oauth_client)
    _, challenge = _pkce_pair()
    resp = await oauth_client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "scope": "mcp",
            "resource": settings.mcp_resource_url,
        },
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "/authorize/consent?request=" in location


# ── consent GET ──────────────────────────────────────────────────────────────


async def _authorize_blob(
    oauth_client: AsyncClient, client_id: str, challenge: str
) -> str:
    """Run /authorize and return the consent ``request`` blob."""
    resp = await oauth_client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "scope": "mcp",
            "resource": settings.mcp_resource_url,
        },
    )
    location = resp.headers["location"]
    query = parse_qs(urlparse(location).query)
    return query["request"][0]


async def test_consent_get_without_session_redirects_to_login(
    oauth_client: AsyncClient,
) -> None:
    client_id = await _register_client(oauth_client)
    _, challenge = _pkce_pair()
    blob = await _authorize_blob(oauth_client, client_id, challenge)

    resp = await oauth_client.get("/authorize/consent", params={"request": blob})
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{settings.frontend_base_url}/admin/?return_to=")
    # The return_to round-trips the consent path (the `?request=` query is
    # percent-encoded; the path itself keeps its slashes).
    assert "return_to=/authorize/consent" in location
    assert quote("?request=", safe="") in location


async def test_consent_get_with_session_renders_org_picker(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    client_id = await _register_client(oauth_client)
    _, challenge = _pkce_pair()
    blob = await _authorize_blob(oauth_client, client_id, challenge)

    cookie = encode_session(seed_admin_user["id"], seed_admin_user["org_id"])
    oauth_client.cookies.set(settings.session_cookie_name, cookie)

    resp = await oauth_client.get("/authorize/consent", params={"request": blob})
    assert resp.status_code == 200
    html = resp.text
    assert "Approve" in html
    assert "Deny" in html
    # The org the admin belongs to is offered (single-org → hidden input).
    assert seed_admin_user["org_id"] in html


async def test_consent_get_bad_blob_returns_400(
    oauth_client: AsyncClient,
) -> None:
    resp = await oauth_client.get(
        "/authorize/consent", params={"request": "not-a-real-blob"}
    )
    assert resp.status_code == 400


# ── consent approve → token exchange ─────────────────────────────────────────


async def _approve_and_get_code(
    oauth_client: AsyncClient,
    *,
    seed_admin_user: dict[str, str],
    client_id: str,
    challenge: str,
) -> str:
    """Sign in, approve consent, and return the issued authorization code."""
    blob = await _authorize_blob(oauth_client, client_id, challenge)
    cookie = encode_session(seed_admin_user["id"], seed_admin_user["org_id"])
    oauth_client.cookies.set(settings.session_cookie_name, cookie)

    resp = await oauth_client.post(
        "/authorize/consent",
        data={
            "request": blob,
            "decision": "approve",
            "org_id": seed_admin_user["org_id"],
        },
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.startswith(REDIRECT_URI)
    query = parse_qs(urlparse(location).query)
    assert query["state"][0] == "xyz"
    return query["code"][0]


async def test_consent_approve_then_token_exchange(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    client_id = await _register_client(oauth_client)
    verifier, challenge = _pkce_pair()
    code = await _approve_and_get_code(
        oauth_client,
        seed_admin_user=seed_admin_user,
        client_id=client_id,
        challenge=challenge,
    )

    resp = await oauth_client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "resource": settings.mcp_resource_url,
        },
    )
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    assert tokens["token_type"].lower() == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]


async def test_token_exchange_wrong_verifier_400(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    client_id = await _register_client(oauth_client)
    _, challenge = _pkce_pair()
    code = await _approve_and_get_code(
        oauth_client,
        seed_admin_user=seed_admin_user,
        client_id=client_id,
        challenge=challenge,
    )

    wrong_verifier, _ = _pkce_pair()
    resp = await oauth_client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": wrong_verifier,
            "resource": settings.mcp_resource_url,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


async def test_consent_deny_redirects_with_error(
    oauth_client: AsyncClient,
    seed_admin_user: dict[str, str],
) -> None:
    client_id = await _register_client(oauth_client)
    _, challenge = _pkce_pair()
    blob = await _authorize_blob(oauth_client, client_id, challenge)
    cookie = encode_session(seed_admin_user["id"], seed_admin_user["org_id"])
    oauth_client.cookies.set(settings.session_cookie_name, cookie)

    resp = await oauth_client.post(
        "/authorize/consent",
        data={
            "request": blob,
            "decision": "deny",
            "org_id": seed_admin_user["org_id"],
        },
    )
    assert resp.status_code == 302
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query["error"][0] == "access_denied"
    assert query["state"][0] == "xyz"


async def test_consent_post_rejects_non_member_org(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A tampered ``org_id`` the user doesn't belong to is refused."""
    # An org the admin is NOT a member of.
    other_org = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values (:n, :s) returning id::text"
            ),
            {"n": "Outsider", "s": f"outsider-{secrets.token_hex(4)}"},
        )
    ).scalar_one()
    await db.flush()

    client_id = await _register_client(oauth_client)
    _, challenge = _pkce_pair()
    blob = await _authorize_blob(oauth_client, client_id, challenge)
    cookie = encode_session(seed_admin_user["id"], seed_admin_user["org_id"])
    oauth_client.cookies.set(settings.session_cookie_name, cookie)

    resp = await oauth_client.post(
        "/authorize/consent",
        data={"request": blob, "decision": "approve", "org_id": other_org},
    )
    assert resp.status_code == 403


# ── MCP endpoint protection + regression ─────────────────────────────────────


async def test_mcp_endpoint_unauthenticated_401(
    oauth_client: AsyncClient,
) -> None:
    resp = await oauth_client.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": "1", "method": "tools/list"},
        headers=MCP_HEADERS,
    )
    assert resp.status_code == 401
    www = resp.headers.get("www-authenticate", "")
    assert "resource_metadata=" in www


async def _tools_call(
    oauth_client: AsyncClient, *, bearer: str
) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": "pulse_list_engagements", "arguments": {}},
    }
    headers = dict(MCP_HEADERS)
    headers["Authorization"] = f"Bearer {bearer}"
    resp = await oauth_client.post(MCP_PATH, json=body, headers=headers)
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    return resp.json()


async def test_tool_call_with_oauth_access_token(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A full authorize→token→tool-call round-trip with an OAuth token."""
    client_id = await _register_client(oauth_client)
    verifier, challenge = _pkce_pair()
    code = await _approve_and_get_code(
        oauth_client,
        seed_admin_user=seed_admin_user,
        client_id=client_id,
        challenge=challenge,
    )
    token_resp = await oauth_client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "resource": settings.mcp_resource_url,
        },
    )
    access_token = token_resp.json()["access_token"]
    # Clear the session cookie so we prove the TOKEN authenticates, not it.
    oauth_client.cookies.clear()

    result = await _tools_call(oauth_client, bearer=access_token)
    assert result["result"].get("isError") is not True, result
    assert isinstance(
        result["result"]["structuredContent"].get("result"), list
    )


async def test_tool_call_with_legacy_api_key_regression(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """The legacy ``pulse_<key>`` path still works under RS mode."""
    raw = await _insert_admin_key(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    result = await _tools_call(oauth_client, bearer=raw)
    assert result["result"].get("isError") is not True, result
    assert isinstance(
        result["result"]["structuredContent"].get("result"), list
    )


# ── refresh / revoke / expiry / membership (plan-required) ───────────────────


async def _issue_tokens(
    oauth_client: AsyncClient, *, seed_admin_user: dict[str, str]
) -> tuple[dict[str, Any], str]:
    """Approve consent + exchange a code; return ``(token json, client_id)``.

    Clears the session cookie afterward so subsequent endpoint calls prove
    the TOKEN authenticates, not a lingering browser session.
    """
    client_id = await _register_client(oauth_client)
    verifier, challenge = _pkce_pair()
    code = await _approve_and_get_code(
        oauth_client,
        seed_admin_user=seed_admin_user,
        client_id=client_id,
        challenge=challenge,
    )
    resp = await oauth_client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "resource": settings.mcp_resource_url,
        },
    )
    assert resp.status_code == 200, resp.text
    oauth_client.cookies.clear()
    return resp.json(), client_id


async def _mcp_call_status(oauth_client: AsyncClient, *, bearer: str) -> int:
    """POST a ``tools/call`` and return the HTTP status (for 401 asserts)."""
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": "pulse_list_engagements", "arguments": {}},
    }
    headers = dict(MCP_HEADERS)
    headers["Authorization"] = f"Bearer {bearer}"
    resp = await oauth_client.post(MCP_PATH, json=body, headers=headers)
    return resp.status_code


async def test_token_refresh_rotates_and_authenticates(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """``grant_type=refresh_token`` rotates both tokens; the new one works
    and the old refresh token is invalidated."""
    tokens, client_id = await _issue_tokens(
        oauth_client, seed_admin_user=seed_admin_user
    )
    rotated = await oauth_client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": tokens["refresh_token"],
            "resource": settings.mcp_resource_url,
            "scope": "mcp",
        },
    )
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()
    assert new["access_token"] and new["access_token"] != tokens["access_token"]
    assert new["refresh_token"] != tokens["refresh_token"]
    # The rotated access token authenticates a tool call.
    result = await _tools_call(oauth_client, bearer=new["access_token"])
    assert result["result"].get("isError") is not True, result
    # The OLD refresh token no longer works (rotation invalidated it).
    stale = await oauth_client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": tokens["refresh_token"],
            "resource": settings.mcp_resource_url,
            "scope": "mcp",
        },
    )
    assert stale.status_code == 400


async def test_revoke_then_mcp_401(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """After ``POST /revoke`` the access token stops authenticating."""
    tokens, client_id = await _issue_tokens(
        oauth_client, seed_admin_user=seed_admin_user
    )
    access = tokens["access_token"]
    assert await _mcp_call_status(oauth_client, bearer=access) == 200

    # The SDK's RevocationRequest requires the client_secret field to be
    # present (it's `str | None` with no default); a public client sends it
    # empty. Client auth still passes via the "none" method.
    rv = await oauth_client.post(
        "/revoke",
        data={"token": access, "client_id": client_id, "client_secret": ""},
    )
    assert rv.status_code == 200, rv.text
    assert await _mcp_call_status(oauth_client, bearer=access) == 401


async def test_expired_access_token_mcp_401(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A grant whose access token has expired is rejected at the endpoint."""
    from datetime import timedelta

    from pulse_api.mcp.oauth import tokens as oauth_tokens
    from pulse_api.models._helpers import utcnow_naive
    from pulse_api.repos import oauth as oauth_repo

    access = oauth_tokens.new_opaque_token()
    await oauth_repo.create_grant(
        db,
        access_prefix=oauth_tokens.prefix_of(access),
        access_hash=oauth_tokens.hash_token(access),
        access_expires_at=utcnow_naive() - timedelta(seconds=5),
        refresh_prefix=None,
        refresh_hash=None,
        refresh_expires_at=None,
        user_id=uuid.UUID(seed_admin_user["id"]),
        org_id=uuid.UUID(seed_admin_user["org_id"]),
        client_id="expired-client",
        scopes=["mcp"],
        resource=settings.mcp_resource_url,
    )
    await db.flush()
    assert await _mcp_call_status(oauth_client, bearer=access) == 401


async def test_removed_membership_mcp_401(
    oauth_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A valid OAuth token stops working once the user loses org membership."""
    tokens, _ = await _issue_tokens(
        oauth_client, seed_admin_user=seed_admin_user
    )
    access = tokens["access_token"]
    assert await _mcp_call_status(oauth_client, bearer=access) == 200

    await db.execute(
        text(
            "delete from public.organization_memberships "
            "where user_id = cast(:u as uuid) and org_id = cast(:o as uuid)"
        ),
        {"u": seed_admin_user["id"], "o": seed_admin_user["org_id"]},
    )
    await db.flush()
    assert await _mcp_call_status(oauth_client, bearer=access) == 401
