"""Integration tests for the MCP OAuth provider + token verifier.

Exercises ``PulseOAuthProvider`` and ``PulseTokenVerifier`` directly
(no HTTP layer — that's PR 2). The provider/verifier open their own
short-lived ``pulse_admin`` sessions against ``admin_engine``; in tests
we monkeypatch those context managers to bind through the test's
rolled-back connection so writes are visible to the test's ``db`` session
and wiped at teardown — the same pattern ``test_mcp.py`` uses.

Coverage:
  • register_client → get_client roundtrip.
  • seed a code via the repo → exchange issues a grant; the code is
    single-use (second exchange fails with invalid_grant).
  • load_access_token returns the right org_id in claims and rejects
    expired / revoked / wrong-resource tokens.
  • refresh rotation invalidates the old refresh token.
  • PulseTokenVerifier accepts a legacy ``pulse_`` API key AND an OAuth
    access token, rejecting anything else.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from mcp.server.auth.provider import AccessToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

from pulse_api.auth.api_keys import generate_key, hash_key, prefix_of
from pulse_api.config import settings
from pulse_api.mcp.oauth import provider as provider_mod
from pulse_api.mcp.oauth import tokens as oauth_tokens
from pulse_api.mcp.oauth import verifier as verifier_mod
from pulse_api.mcp.oauth.provider import PulseAuthorizationCode, provider
from pulse_api.mcp.oauth.verifier import API_KEY_CLIENT_ID, verifier
from pulse_api.models._helpers import utcnow_naive
from pulse_api.repos import oauth as oauth_repo

MCP_RESOURCE = "https://pulse.axiolo.com/api/mcp"


@pytest.fixture(autouse=True)
def _mcp_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``mcp_resource_url`` so audience checks are deterministic."""
    monkeypatch.setattr(settings, "mcp_issuer_url", "https://pulse.axiolo.com")
    monkeypatch.setattr(settings, "session_secret", "test-secret-please")


@pytest.fixture
def patched_oauth_sessions(
    db_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route the provider + verifier admin sessions through ``db_conn``.

    Production opens fresh ``admin_engine`` sessions which (a) write to
    the dev DB and (b) are invisible to the test's rollback transaction.
    We bind them to the shared connection so commits stay inside the
    outer transaction and roll back at teardown.
    """

    @asynccontextmanager
    async def _override() -> AsyncIterator[AsyncSession]:
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    monkeypatch.setattr(provider_mod, "_admin_session", _override)
    monkeypatch.setattr(verifier_mod, "_admin_session", _override)

    # `verify_bearer` schedules a fresh-session last_used_at touch; route
    # it through db_conn too so it doesn't escape the transaction.
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

    monkeypatch.setattr(
        _api_keys_lib, "_touch_last_used", _patched_touch_last_used
    )


def _client_info(client_id: str = "test-client") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        client_name="Claude",
        scope="mcp",
    )


async def _seed_code(
    db: AsyncSession,
    *,
    user_id: str,
    org_id: str,
    client_id: str = "test-client",
    code_challenge: str = "test-challenge",
    resource: str | None = MCP_RESOURCE,
    expired: bool = False,
) -> str:
    """Insert a single authorization code via the repo, return the raw code."""
    raw_code = oauth_tokens.new_opaque_token()
    import uuid as _uuid

    expires = utcnow_naive() + (
        timedelta(seconds=-10) if expired else timedelta(seconds=300)
    )
    await oauth_repo.create_authorization_code(
        db,
        code_hash=oauth_tokens.hash_token(raw_code),
        client_id=client_id,
        user_id=_uuid.UUID(user_id),
        org_id=_uuid.UUID(org_id),
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_provided_explicitly=True,
        code_challenge=code_challenge,
        scopes=["mcp"],
        resource=resource,
        expires_at=expires,
    )
    await db.flush()
    return raw_code


# ── register/get client roundtrip ──────────────────────────────────────────


async def test_register_then_get_client_roundtrip(
    patched_oauth_sessions: None,
) -> None:
    info = _client_info("rt-client")
    await provider.register_client(info)

    loaded = await provider.get_client("rt-client")
    assert loaded is not None
    assert loaded.client_id == "rt-client"
    assert loaded.token_endpoint_auth_method == "none"
    assert "authorization_code" in loaded.grant_types
    assert loaded.response_types == ["code"]


async def test_get_unknown_client_returns_none(
    patched_oauth_sessions: None,
) -> None:
    assert await provider.get_client("does-not-exist") is None


# ── authorize redirect ─────────────────────────────────────────────────────


async def test_authorize_returns_signed_consent_redirect() -> None:
    from mcp.server.auth.provider import AuthorizationParams

    params = AuthorizationParams(
        state="state-123",
        scopes=["mcp"],
        code_challenge="chal",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=True,
        resource=MCP_RESOURCE,
    )
    url = await provider.authorize(_client_info(), params)
    assert url.startswith("https://pulse.axiolo.com/authorize/consent?request=")
    blob = url.split("request=", 1)[1]
    decoded = oauth_tokens.read_authz_request(blob)
    assert decoded["client_id"] == "test-client"
    assert decoded["code_challenge"] == "chal"
    assert decoded["state"] == "state-123"


# ── exchange code → grant, single use ──────────────────────────────────────


async def test_exchange_code_issues_grant_and_is_single_use(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw_code = await _seed_code(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )

    loaded = await provider.load_authorization_code(_client_info(), raw_code)
    assert isinstance(loaded, PulseAuthorizationCode)
    assert loaded.code_challenge == "test-challenge"
    assert loaded.org_id == seed_admin_user["org_id"]

    token = await provider.exchange_authorization_code(_client_info(), loaded)
    assert token.access_token
    assert token.refresh_token
    assert token.token_type == "Bearer"
    assert token.expires_in == provider_mod.ACCESS_TTL_SECONDS
    assert token.scope == "mcp"

    # The access token resolves with the right org.
    access = await provider.load_access_token(token.access_token)
    assert access is not None
    assert access.claims is not None
    assert access.claims["org_id"] == seed_admin_user["org_id"]

    # Second exchange of the same code fails — single use.
    with pytest.raises(TokenError) as exc:
        await provider.exchange_authorization_code(_client_info(), loaded)
    assert exc.value.error == "invalid_grant"


async def test_load_unknown_code_returns_none(
    patched_oauth_sessions: None,
) -> None:
    bogus = oauth_tokens.new_opaque_token()
    assert await provider.load_authorization_code(_client_info(), bogus) is None


# ── load_access_token rejections ───────────────────────────────────────────


async def test_load_access_token_rejects_revoked(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw_code = await _seed_code(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    loaded = await provider.load_authorization_code(_client_info(), raw_code)
    token = await provider.exchange_authorization_code(_client_info(), loaded)

    # Sanity: works first.
    assert await provider.load_access_token(token.access_token) is not None

    # Revoke via the access token object, then it must stop resolving.
    access = await provider.load_access_token(token.access_token)
    await provider.revoke_token(access)
    assert await provider.load_access_token(token.access_token) is None


async def test_load_access_token_rejects_expired(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    # Mint a grant directly with an already-past access expiry.
    import uuid as _uuid

    access = oauth_tokens.new_opaque_token()
    await oauth_repo.create_grant(
        db,
        access_prefix=oauth_tokens.prefix_of(access),
        access_hash=oauth_tokens.hash_token(access),
        access_expires_at=utcnow_naive() - timedelta(seconds=5),
        refresh_prefix=None,
        refresh_hash=None,
        refresh_expires_at=None,
        user_id=_uuid.UUID(seed_admin_user["id"]),
        org_id=_uuid.UUID(seed_admin_user["org_id"]),
        client_id="test-client",
        scopes=["mcp"],
        resource=MCP_RESOURCE,
    )
    await db.flush()
    assert await provider.load_access_token(access) is None


async def test_load_access_token_rejects_wrong_resource(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    import uuid as _uuid

    access = oauth_tokens.new_opaque_token()
    await oauth_repo.create_grant(
        db,
        access_prefix=oauth_tokens.prefix_of(access),
        access_hash=oauth_tokens.hash_token(access),
        access_expires_at=utcnow_naive() + timedelta(seconds=300),
        refresh_prefix=None,
        refresh_hash=None,
        refresh_expires_at=None,
        user_id=_uuid.UUID(seed_admin_user["id"]),
        org_id=_uuid.UUID(seed_admin_user["org_id"]),
        client_id="test-client",
        scopes=["mcp"],
        resource="https://evil.example.com/api/mcp",
    )
    await db.flush()
    assert await provider.load_access_token(access) is None


async def test_load_access_token_rejects_null_resource(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A grant with NULL resource is rejected — NULL is not a wildcard
    audience. Closes the RFC 8707 bypass where an unbound token passed."""
    import uuid as _uuid

    access = oauth_tokens.new_opaque_token()
    await oauth_repo.create_grant(
        db,
        access_prefix=oauth_tokens.prefix_of(access),
        access_hash=oauth_tokens.hash_token(access),
        access_expires_at=utcnow_naive() + timedelta(seconds=300),
        refresh_prefix=None,
        refresh_hash=None,
        refresh_expires_at=None,
        user_id=_uuid.UUID(seed_admin_user["id"]),
        org_id=_uuid.UUID(seed_admin_user["org_id"]),
        client_id="test-client",
        scopes=["mcp"],
        resource=None,
    )
    await db.flush()
    assert await provider.load_access_token(access) is None


async def test_short_token_rejected_everywhere(
    patched_oauth_sessions: None,
) -> None:
    """A token too short to carry a prefix is rejected, never crashes."""
    assert await provider.load_access_token("abc") is None
    assert await provider.load_refresh_token(_client_info(), "abc") is None
    short = AccessToken(token="abc", client_id="test-client", scopes=["mcp"])
    await provider.revoke_token(short)  # no raise
    assert await verifier.verify_token("abc") is None


async def test_load_access_token_unknown_returns_none(
    patched_oauth_sessions: None,
) -> None:
    assert await provider.load_access_token(oauth_tokens.new_opaque_token()) is None


# ── refresh rotation ───────────────────────────────────────────────────────


async def test_refresh_rotation_invalidates_old_refresh_token(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw_code = await _seed_code(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    loaded = await provider.load_authorization_code(_client_info(), raw_code)
    token = await provider.exchange_authorization_code(_client_info(), loaded)
    old_refresh = token.refresh_token
    old_access = token.access_token

    rt = await provider.load_refresh_token(_client_info(), old_refresh)
    assert rt is not None
    assert rt.org_id == seed_admin_user["org_id"]

    rotated = await provider.exchange_refresh_token(
        _client_info(), rt, ["mcp"]
    )
    assert rotated.access_token != old_access
    assert rotated.refresh_token != old_refresh

    # Old refresh token no longer loads.
    assert await provider.load_refresh_token(_client_info(), old_refresh) is None
    # Old access token no longer resolves (rotated out).
    assert await provider.load_access_token(old_access) is None
    # New access token resolves with the same org.
    new_access = await provider.load_access_token(rotated.access_token)
    assert new_access is not None
    assert new_access.claims["org_id"] == seed_admin_user["org_id"]


async def test_revoke_by_refresh_token_kills_grant(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Revoking via the refresh-token object kills both tokens (RFC 7009)."""
    raw_code = await _seed_code(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    loaded = await provider.load_authorization_code(_client_info(), raw_code)
    token = await provider.exchange_authorization_code(_client_info(), loaded)

    rt = await provider.load_refresh_token(_client_info(), token.refresh_token)
    assert rt is not None
    await provider.revoke_token(rt)

    # Both the access and the refresh side stop resolving.
    assert await provider.load_access_token(token.access_token) is None
    assert (
        await provider.load_refresh_token(_client_info(), token.refresh_token)
        is None
    )


async def test_revoke_unknown_token_is_noop(
    patched_oauth_sessions: None,
) -> None:
    """Revoking an unknown token must not raise (idempotent no-op)."""
    fake = AccessToken(
        token=oauth_tokens.new_opaque_token(),
        client_id="test-client",
        scopes=["mcp"],
    )
    await provider.revoke_token(fake)  # no exception


async def test_register_client_without_client_id_raises(
    patched_oauth_sessions: None,
) -> None:
    """DCR with a missing client_id is rejected as invalid metadata."""
    from mcp.server.auth.provider import RegistrationError

    info = OAuthClientInformationFull(
        client_id=None,
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    with pytest.raises(RegistrationError) as exc:
        await provider.register_client(info)
    assert exc.value.error == "invalid_client_metadata"


async def test_exchange_unknown_refresh_token_raises(
    patched_oauth_sessions: None,
    seed_admin_user: dict[str, str],
) -> None:
    from pulse_api.mcp.oauth.provider import PulseRefreshToken

    bogus = oauth_tokens.new_opaque_token()
    fake = PulseRefreshToken(
        token=bogus,
        client_id="test-client",
        scopes=["mcp"],
        org_id=seed_admin_user["org_id"],
    )
    with pytest.raises(TokenError) as exc:
        await provider.exchange_refresh_token(_client_info(), fake, ["mcp"])
    assert exc.value.error == "invalid_grant"


# ── token verifier: API key + OAuth token ───────────────────────────────────


async def _insert_api_key(
    db: AsyncSession, *, user_id: str, org_id: str
) -> str:
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
            "l": "verifier test",
        },
    )
    await db.flush()
    return raw


async def test_verifier_accepts_legacy_api_key(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw = await _insert_api_key(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    result = await verifier.verify_token(raw)
    assert isinstance(result, AccessToken)
    assert result.client_id == API_KEY_CLIENT_ID
    assert result.scopes == ["mcp"]
    assert result.resource == settings.mcp_resource_url
    assert result.subject == seed_admin_user["id"]
    assert result.claims["org_id"] == seed_admin_user["org_id"]


async def test_verifier_accepts_oauth_access_token(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw_code = await _seed_code(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    loaded = await provider.load_authorization_code(_client_info(), raw_code)
    token = await provider.exchange_authorization_code(_client_info(), loaded)

    result = await verifier.verify_token(token.access_token)
    assert isinstance(result, AccessToken)
    assert result.client_id == "test-client"
    assert result.claims["org_id"] == seed_admin_user["org_id"]


async def test_verifier_rejects_when_membership_removed(
    patched_oauth_sessions: None,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A still-valid OAuth token stops resolving once the user loses
    membership in the granting org (re-checked on every request)."""
    raw_code = await _seed_code(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    loaded = await provider.load_authorization_code(_client_info(), raw_code)
    token = await provider.exchange_authorization_code(_client_info(), loaded)

    # Resolves while membership exists.
    assert await verifier.verify_token(token.access_token) is not None

    # Drop the membership — the grant itself is untouched.
    await db.execute(
        text(
            "delete from public.organization_memberships "
            "where user_id = cast(:u as uuid) and org_id = cast(:o as uuid)"
        ),
        {"u": seed_admin_user["id"], "o": seed_admin_user["org_id"]},
    )
    await db.flush()

    assert await verifier.verify_token(token.access_token) is None


async def test_verifier_rejects_garbage(
    patched_oauth_sessions: None,
) -> None:
    assert await verifier.verify_token("not-a-real-token") is None
    # A pulse_-prefixed but unknown key also rejects.
    assert await verifier.verify_token(generate_key()) is None
