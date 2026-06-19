"""``PulseOAuthProvider`` — the MCP OAuth 2.1 authorization-server core.

Implements Anthropic's ``OAuthAuthorizationServerProvider`` protocol
against Pulse's own accounts + orgs. Pulse is its own identity provider,
so there is no second-leg redirect to Google/Microsoft: ``authorize``
hands off to a Pulse consent page (built in PR 2) carrying a signed
authorization-request blob, and the consent page is what mints the
authorization code.

Storage mirrors the API-key pattern: opaque tokens + codes are stored as
``prefix`` + SHA-256 ``hash`` (never plaintext) so a grant is instantly
revocable. Tokens are audience-bound to ``settings.mcp_resource_url`` and
carry the granting org in ``claims["org_id"]``, which flows into each
tool's member-scoped session.

PKCE is NOT verified here. The SDK's token handler hashes the presented
``code_verifier`` and compares it to the stored ``code_challenge``, so
``load_authorization_code`` must surface ``code_challenge`` verbatim and
the provider must not re-verify it.

These methods run OUTSIDE FastAPI's DI graph (the SDK calls them from its
route handlers), so each opens its own short-lived ``pulse_admin``
session against ``admin_engine`` — the same pattern as
``mcp.server._open_admin_session``.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.config import settings
from pulse_api.db import admin_engine
from pulse_api.mcp.oauth import tokens as oauth_tokens
from pulse_api.models._helpers import as_naive_utc, utcnow_naive
from pulse_api.repos import oauth as oauth_repo

# Access tokens live 1h; refresh tokens 30d. Both are opaque + hashed, so
# "instant revocation via revoked_at" is the real safety net — the TTLs
# just bound the blast radius of a leaked-but-unrevoked token.
ACCESS_TTL_SECONDS = 3600
REFRESH_TTL_SECONDS = 60 * 60 * 24 * 30


def _epoch(value) -> int:  # type: ignore[no-untyped-def]
    """Unix epoch seconds for a DB datetime (naive-UTC or tz-aware).

    The SDK protocol carries ``expires_at`` as a Unix timestamp.
    ``datetime.timestamp()`` on a naive value assumes local time, so we
    normalize to aware-UTC first via ``as_naive_utc`` + the UTC tz.

    Args:
        value: A datetime read back from the DB.

    Returns:
        Integer Unix timestamp in UTC.
    """
    from datetime import UTC

    return int(as_naive_utc(value).replace(tzinfo=UTC).timestamp())


class PulseAuthorizationCode(AuthorizationCode):
    """``AuthorizationCode`` subclass carrying the resolved ``org_id``.

    The SDK protocol allows subclasses to add fields (FastMCP never
    serializes them externally). We carry ``org_id`` so
    ``exchange_authorization_code`` can bind the issued grant to the org
    the operator approved without a second lookup.
    """

    org_id: str


class PulseRefreshToken(RefreshToken):
    """``RefreshToken`` subclass carrying the resolved ``org_id``."""

    org_id: str


@asynccontextmanager
async def _admin_session() -> AsyncIterator[AsyncSession]:
    """Open a short-lived ``pulse_admin`` session.

    The provider runs outside FastAPI DI, so it owns its own session
    lifecycle. Tests monkeypatch this to bind through the rolled-back
    test connection.
    """
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


def _client_to_info(client) -> OAuthClientInformationFull:
    """Map an ``OAuthClient`` row to the SDK's client-info model.

    Args:
        client: The persisted ``OAuthClient`` row.

    Returns:
        An ``OAuthClientInformationFull`` the SDK handlers consume.
    """
    return OAuthClientInformationFull(
        client_id=client.client_id,
        client_secret=None,  # public client; secret never re-surfaced
        redirect_uris=list(client.redirect_uris),
        grant_types=list(client.grant_types),
        response_types=list(client.response_types),
        token_endpoint_auth_method=client.token_endpoint_auth_method,
        client_name=client.client_name,
        scope=client.scope,
    )


class PulseOAuthProvider(
    OAuthAuthorizationServerProvider[
        PulseAuthorizationCode, PulseRefreshToken, AccessToken
    ]
):
    """First-party OAuth 2.1 authorization server backed by Pulse."""

    # ── client registration (DCR) ───────────────────────────────────────

    async def get_client(
        self, client_id: str
    ) -> OAuthClientInformationFull | None:
        """Load a registered client by id, or None if unknown."""
        async with _admin_session() as session:
            client = await oauth_repo.get_by_client_id(session, client_id)
            if client is None:
                return None
            return _client_to_info(client)

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        """Persist a Dynamic Client Registration request.

        Claude Desktop registers a public client
        (``token_endpoint_auth_method = "none"``). If a confidential
        client ever registers with a secret, hash it before storing —
        the plaintext secret is never persisted.

        Args:
            client_info: The fully-populated client info the SDK
                register handler hands us (it has already assigned
                ``client_id`` and, for confidential clients, a secret).

        Raises:
            RegistrationError: If ``client_id`` is missing.
        """
        if not client_info.client_id:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="client_id is required",
            )
        secret_hash = (
            oauth_tokens.hash_token(client_info.client_secret)
            if client_info.client_secret
            else None
        )
        async with _admin_session() as session:
            await oauth_repo.create_client(
                session,
                client_id=client_info.client_id,
                client_secret_hash=secret_hash,
                redirect_uris=[str(u) for u in (client_info.redirect_uris or [])],
                grant_types=list(client_info.grant_types),
                response_types=list(client_info.response_types),
                token_endpoint_auth_method=(
                    client_info.token_endpoint_auth_method or "none"
                ),
                client_name=client_info.client_name,
                scope=client_info.scope,
            )
            await session.commit()

    # ── authorization ────────────────────────────────────────────────────

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Redirect the client to the Pulse consent page.

        We do NOT create the authorization code here — the consent page
        (PR 2) does that after the operator signs in and picks an org.
        Instead we sign the pending authorization request into a
        tamper-evident blob and carry it as a query param.

        Args:
            client: The registered client requesting authorization.
            params: The authorize-request parameters.

        Returns:
            The absolute consent-page URL to redirect the browser to.
        """
        blob = oauth_tokens.sign_authz_request(
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": (
                    params.redirect_uri_provided_explicitly
                ),
                "code_challenge": params.code_challenge,
                "scopes": params.scopes or [],
                "state": params.state,
                "resource": params.resource,
            }
        )
        return f"{settings.mcp_issuer_base}/authorize/consent?request={blob}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> PulseAuthorizationCode | None:
        """Load a stored authorization code by its raw value, or None.

        Returns the code with its ``code_challenge`` intact so the SDK
        token handler can run PKCE verification. The redirect-uri
        consistency + expiry checks also live in the handler, so we
        surface the row as-is (without deleting it — that's
        ``exchange_authorization_code``'s job).

        Args:
            client: The client presenting the code.
            authorization_code: The raw authorization-code string.

        Returns:
            A ``PulseAuthorizationCode``, or None if unknown.
        """
        code_hash = oauth_tokens.hash_token(authorization_code)
        async with _admin_session() as session:
            row = await oauth_repo.get_authorization_code(session, code_hash)
            if row is None:
                return None
            return PulseAuthorizationCode(
                code=authorization_code,
                scopes=list(row.scopes),
                expires_at=_epoch(row.expires_at),
                client_id=row.client_id,
                code_challenge=row.code_challenge,
                redirect_uri=row.redirect_uri,
                redirect_uri_provided_explicitly=(
                    row.redirect_uri_provided_explicitly
                ),
                resource=row.resource,
                subject=str(row.user_id),
                org_id=str(row.org_id),
            )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: PulseAuthorizationCode,
    ) -> OAuthToken:
        """Consume the code (single use) and mint access + refresh tokens.

        Args:
            client: The client exchanging the code.
            authorization_code: The loaded ``PulseAuthorizationCode``.

        Returns:
            An ``OAuthToken`` with fresh access + refresh tokens.

        Raises:
            TokenError: If the code is already consumed or expired
                (surfaced as ``invalid_grant`` by the SDK handler).
        """
        # NB: ``TokenError`` is a frozen dataclass-Exception. Raising it
        # *inside* an ``@asynccontextmanager`` block fails because the CM's
        # __aexit__ tries to reassign its (frozen) ``__traceback__``. So we
        # flag the failure inside the block and raise after it closes.
        from mcp.server.auth.provider import TokenError

        code_hash = oauth_tokens.hash_token(authorization_code.code)
        access_token: str | None = None
        refresh_token: str | None = None
        async with _admin_session() as session:
            consumed = await oauth_repo.consume_authorization_code(
                session, code_hash
            )
            if consumed is None:
                # Commit the delete (replay protection) before erroring.
                await session.commit()
            else:
                access_token = oauth_tokens.new_opaque_token()
                refresh_token = oauth_tokens.new_opaque_token()
                now = utcnow_naive()
                await oauth_repo.create_grant(
                    session,
                    access_prefix=oauth_tokens.prefix_of(access_token),
                    access_hash=oauth_tokens.hash_token(access_token),
                    access_expires_at=now
                    + timedelta(seconds=ACCESS_TTL_SECONDS),
                    refresh_prefix=oauth_tokens.prefix_of(refresh_token),
                    refresh_hash=oauth_tokens.hash_token(refresh_token),
                    refresh_expires_at=now
                    + timedelta(seconds=REFRESH_TTL_SECONDS),
                    user_id=consumed.user_id,
                    org_id=consumed.org_id,
                    client_id=consumed.client_id,
                    scopes=list(consumed.scopes),
                    # Bind the grant to THIS resource. We serve exactly one
                    # MCP resource, so a code that carried no (or a stale)
                    # resource still yields a correctly audience-bound token;
                    # load_access_token strict-checks this value.
                    resource=consumed.resource or settings.mcp_resource_url,
                )
                await session.commit()

        if access_token is None or refresh_token is None:
            raise TokenError(
                error="invalid_grant",
                error_description="authorization code is invalid or expired",
            )

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ACCESS_TTL_SECONDS,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_token,
        )

    # ── refresh ──────────────────────────────────────────────────────────

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> PulseRefreshToken | None:
        """Load an active refresh token by its raw value, or None.

        Args:
            client: The client presenting the refresh token.
            refresh_token: The raw refresh-token string.

        Returns:
            A ``PulseRefreshToken``, or None if unknown/expired/revoked.
        """
        try:
            prefix = oauth_tokens.prefix_of(refresh_token)
        except ValueError:
            return None
        async with _admin_session() as session:
            grant = await oauth_repo.get_grant_by_refresh_prefix(
                session, prefix
            )
            if grant is None or not oauth_tokens.verify(
                refresh_token, grant.refresh_hash
            ):
                return None
            if (
                grant.refresh_expires_at is not None
                and as_naive_utc(grant.refresh_expires_at) < utcnow_naive()
            ):
                return None
            return PulseRefreshToken(
                token=refresh_token,
                client_id=grant.client_id,
                scopes=list(grant.scopes),
                expires_at=(
                    _epoch(grant.refresh_expires_at)
                    if grant.refresh_expires_at is not None
                    else None
                ),
                subject=str(grant.user_id),
                org_id=str(grant.org_id),
            )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: PulseRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Rotate BOTH tokens and return a fresh pair.

        The old access + refresh tokens stop resolving the instant the
        grant row is rotated — their hashes no longer match.

        Args:
            client: The client exchanging the refresh token.
            refresh_token: The loaded ``PulseRefreshToken``.
            scopes: The (validated) scopes to grant on the new tokens.

        Returns:
            An ``OAuthToken`` with rotated access + refresh tokens.

        Raises:
            TokenError: If the underlying grant disappeared (revoked
                between load and exchange).
        """
        # See ``exchange_authorization_code`` — raise the frozen-dataclass
        # ``TokenError`` outside the ``@asynccontextmanager`` block.
        from mcp.server.auth.provider import TokenError

        prefix = oauth_tokens.prefix_of(refresh_token.token)
        new_access: str | None = None
        new_refresh: str | None = None
        async with _admin_session() as session:
            grant = await oauth_repo.get_grant_by_refresh_prefix(
                session, prefix
            )
            if grant is not None and oauth_tokens.verify(
                refresh_token.token, grant.refresh_hash
            ):
                new_access = oauth_tokens.new_opaque_token()
                new_refresh = oauth_tokens.new_opaque_token()
                now = utcnow_naive()
                await oauth_repo.rotate_grant(
                    session,
                    grant_id=grant.id,
                    access_prefix=oauth_tokens.prefix_of(new_access),
                    access_hash=oauth_tokens.hash_token(new_access),
                    access_expires_at=now
                    + timedelta(seconds=ACCESS_TTL_SECONDS),
                    refresh_prefix=oauth_tokens.prefix_of(new_refresh),
                    refresh_hash=oauth_tokens.hash_token(new_refresh),
                    refresh_expires_at=now
                    + timedelta(seconds=REFRESH_TTL_SECONDS),
                )
                await session.commit()

        if new_access is None or new_refresh is None:
            raise TokenError(
                error="invalid_grant",
                error_description="refresh token is invalid",
            )

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=ACCESS_TTL_SECONDS,
            scope=" ".join(scopes),
            refresh_token=new_refresh,
        )

    # ── access-token verification ────────────────────────────────────────

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Resolve an opaque access token to an ``AccessToken``, or None.

        Rejects tokens that are unknown, revoked, expired, or bound to a
        different resource than ``settings.mcp_resource_url``. On success
        bumps ``last_used_at`` best-effort and returns the token with the
        granting org in ``claims["org_id"]``.

        Args:
            token: The raw opaque access token.

        Returns:
            An ``AccessToken`` carrying ``claims["org_id"]``, or None.
        """
        try:
            prefix = oauth_tokens.prefix_of(token)
        except ValueError:
            return None
        async with _admin_session() as session:
            grant = await oauth_repo.get_grant_by_access_prefix(
                session, prefix
            )
            if grant is None or not oauth_tokens.verify(
                token, grant.access_hash
            ):
                return None
            if as_naive_utc(grant.access_expires_at) < utcnow_naive():
                return None
            # RFC 8707 audience binding: only grants bound to THIS resource
            # are accepted. A NULL resource is not a wildcard — it is
            # rejected (every grant is bound to mcp_resource_url at issue).
            if grant.resource != settings.mcp_resource_url:
                return None

            await oauth_repo.touch_last_used(session, grant.id)
            await session.commit()

            return AccessToken(
                token=token,
                client_id=grant.client_id,
                scopes=list(grant.scopes),
                expires_at=_epoch(grant.access_expires_at),
                resource=grant.resource,
                subject=str(grant.user_id),
                claims={"org_id": str(grant.org_id)},
            )

    # ── revocation ───────────────────────────────────────────────────────

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        """Revoke the grant behind an access OR refresh token.

        Looks the grant up by whichever prefix matches, then soft-revokes
        it — killing both tokens at once (RFC 7009). A no-op if the token
        is unknown or already revoked.

        Args:
            token: The ``AccessToken`` or ``RefreshToken`` to revoke.
        """
        raw = token.token
        try:
            prefix = oauth_tokens.prefix_of(raw)
        except ValueError:
            return
        async with _admin_session() as session:
            grant = await oauth_repo.get_grant_by_access_prefix(
                session, prefix
            )
            if grant is None or not oauth_tokens.verify(
                raw, grant.access_hash
            ):
                grant = await oauth_repo.get_grant_by_refresh_prefix(
                    session, prefix
                )
                if grant is None or not oauth_tokens.verify(
                    raw, grant.refresh_hash
                ):
                    return
            await oauth_repo.revoke_grant(session, grant.id)
            await session.commit()


# Module-level singleton — the routes (PR 2) and the verifier import this.
provider = PulseOAuthProvider()
