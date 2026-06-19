"""``PulseTokenVerifier`` — unifies API keys + OAuth tokens for the RS.

The MCP endpoint runs in resource-server mode (PR 2). FastMCP's
``RequireAuthMiddleware`` calls ``verify_token`` on the bearer string and
admits the request iff it returns an ``AccessToken``. Two credential
shapes converge here:

* Legacy ``pulse_<key>`` API keys → resolved via the existing
  ``auth.api_keys.verify_bearer`` single source of truth. Yields an
  ``AccessToken`` with the key's org in ``claims["org_id"]`` and a
  sentinel ``client_id`` so the audit trail can tell key auth apart.
* OAuth access tokens → delegated to
  ``PulseOAuthProvider.load_access_token``, which checks
  revocation/expiry/audience and surfaces ``claims["org_id"]``.

Either way every tool keeps reading ``org_id`` off the token's claims and
opening a member-scoped session — the auth shape is uniform downstream.
Returns None on any failure so the middleware 401s.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.auth.provider import AccessToken, TokenVerifier
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth import api_keys as api_keys_lib
from pulse_api.config import settings
from pulse_api.db import admin_engine
from pulse_api.mcp.oauth.provider import provider as oauth_provider

# Marks an AccessToken minted from a legacy API key rather than an OAuth
# grant — lets downstream attribution distinguish the two credential
# shapes without re-parsing the bearer string.
API_KEY_CLIENT_ID = "pulse-api-key"


@asynccontextmanager
async def _admin_session() -> AsyncIterator[AsyncSession]:
    """Open a short-lived ``pulse_admin`` session for key resolution.

    The verifier runs outside FastAPI DI (FastMCP's middleware calls it),
    so it owns its own session. Tests monkeypatch this to bind through
    the rolled-back test connection.
    """
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


class PulseTokenVerifier(TokenVerifier):
    """Resource-server token verifier for the MCP endpoint."""

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token (API key OR OAuth) → ``AccessToken``.

        Args:
            token: The raw bearer-token string (no ``Bearer `` prefix).

        Returns:
            An ``AccessToken`` with ``claims["org_id"]`` on success, or
            None if the token is invalid by any path.
        """
        if token.startswith(api_keys_lib.KEY_PREFIX):
            access = await self._verify_api_key(token)
        else:
            access = await oauth_provider.load_access_token(token)
        if access is None:
            return None
        # Re-check membership on EVERY request: a credential (key or OAuth
        # grant) outlives the membership it was minted under, so a user
        # removed from the org must lose MCP access immediately rather than
        # at token expiry (up to 30d for a refresh-rotated grant).
        if not await self._has_membership(access):
            return None
        return access

    async def _has_membership(self, access: AccessToken) -> bool:
        """True iff ``access.subject`` still belongs to its claimed org.

        Args:
            access: The resolved access token (carries ``subject`` and
                ``claims["org_id"]``).

        Returns:
            True if an ``organization_memberships`` row links the user to
            the org, False otherwise (including on any error).
        """
        org_id = (access.claims or {}).get("org_id")
        if not org_id or access.subject is None:
            return False
        try:
            async with _admin_session() as session:
                result = await session.execute(
                    text(
                        "select 1 from public.organization_memberships "
                        "where user_id = cast(:u as uuid) "
                        "  and org_id  = cast(:o as uuid) limit 1"
                    ),
                    {"u": access.subject, "o": org_id},
                )
                return result.scalar() is not None
        except Exception:
            return False

    async def _verify_api_key(self, token: str) -> AccessToken | None:
        """Resolve a legacy ``pulse_<key>`` to an ``AccessToken``.

        Args:
            token: The raw ``pulse_<key>`` string.

        Returns:
            An ``AccessToken`` carrying the key's org, or None.
        """
        try:
            async with _admin_session() as session:
                resolved = await api_keys_lib.verify_bearer(
                    f"Bearer {token}", session
                )
                if resolved is None:
                    return None
                user, api_key = resolved
                return AccessToken(
                    token=token,
                    client_id=API_KEY_CLIENT_ID,
                    scopes=["mcp"],
                    resource=settings.mcp_resource_url,
                    subject=str(user.id),
                    claims={"org_id": str(api_key.org_id)},
                )
        except Exception:
            return None


# Module-level singleton — PR 2 passes this to FastMCP as token_verifier.
verifier = PulseTokenVerifier()
