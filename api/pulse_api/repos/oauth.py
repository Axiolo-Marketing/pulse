"""Repository functions for the MCP OAuth authorization-server tables.

All paths run on a ``pulse_admin`` (BYPASSRLS) session — there is no RLS
on ``oauth_clients`` / ``oauth_authorization_codes`` / ``oauth_grants``.
The OAuth/DCR/token flows must resolve clients and grants before any
tenant context exists, so the application layer (the OAuth provider's own
signature checks + PKCE in the SDK token handler) is the gate, exactly
like ``repos/api_keys``.

Tokens + codes are stored hashed (SHA-256 hex) with an indexed prefix;
the raw values never touch disk. ``consume_authorization_code`` is
load-and-delete (single use); ``revoke_grant`` is a soft tombstone via
``revoked_at`` that the active-only partial indexes exclude.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.models import OAuthAuthorizationCode, OAuthClient, OAuthGrant
from pulse_api.models._helpers import as_naive_utc, utcnow_naive


# ── clients ───────────────────────────────────────────────────────────────


async def get_by_client_id(
    session: AsyncSession, client_id: str
) -> OAuthClient | None:
    """Return the registered client for ``client_id``, or None.

    Args:
        session: A ``pulse_admin`` session.
        client_id: The public client identifier from the DCR response.

    Returns:
        The ``OAuthClient`` row, or None if no such client exists.
    """
    result = await session.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    return result.scalar_one_or_none()


async def create_client(
    session: AsyncSession,
    *,
    client_id: str,
    client_secret_hash: str | None,
    redirect_uris: list[str],
    grant_types: list[str],
    response_types: list[str],
    token_endpoint_auth_method: str,
    client_name: str | None,
    scope: str | None,
) -> OAuthClient:
    """Insert a DCR client record and return it.

    Args:
        session: A ``pulse_admin`` session.
        client_id: The public client identifier to issue.
        client_secret_hash: SHA-256 of the client secret, or None for a
            public client.
        redirect_uris: Registered redirect URIs.
        grant_types: Supported grant types.
        response_types: Supported response types.
        token_endpoint_auth_method: Token-endpoint auth method.
        client_name: Optional client name.
        scope: Optional registered scope string.

    Returns:
        The persisted ``OAuthClient`` row, refreshed.
    """
    client = OAuthClient(
        client_id=client_id,
        client_secret_hash=client_secret_hash,
        redirect_uris=list(redirect_uris),
        grant_types=list(grant_types),
        response_types=list(response_types),
        token_endpoint_auth_method=token_endpoint_auth_method,
        client_name=client_name,
        scope=scope,
    )
    session.add(client)
    await session.flush()
    await session.refresh(client)
    return client


# ── authorization codes ─────────────────────────────────────────────────────


async def create_authorization_code(
    session: AsyncSession,
    *,
    code_hash: str,
    client_id: str,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    code_challenge: str,
    scopes: list[str],
    resource: str | None,
    expires_at: datetime,
) -> OAuthAuthorizationCode:
    """Insert a single-use authorization code and return it.

    Args:
        session: A ``pulse_admin`` session.
        code_hash: SHA-256 hex of the raw code.
        client_id: The client the code is issued to.
        user_id: Resource owner who approved consent.
        org_id: Organization the resulting tokens are scoped to.
        redirect_uri: Redirect URI from the authorize request.
        redirect_uri_provided_explicitly: Whether the client supplied
            ``redirect_uri`` explicitly.
        code_challenge: PKCE S256 challenge string.
        scopes: Requested scopes.
        resource: RFC 8707 resource indicator, or None.
        expires_at: Hard expiry timestamp (naive UTC).

    Returns:
        The persisted ``OAuthAuthorizationCode`` row, refreshed.
    """
    code = OAuthAuthorizationCode(
        code_hash=code_hash,
        client_id=client_id,
        user_id=user_id,
        org_id=org_id,
        redirect_uri=redirect_uri,
        redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
        code_challenge=code_challenge,
        scopes=list(scopes),
        resource=resource,
        expires_at=expires_at,
    )
    session.add(code)
    await session.flush()
    await session.refresh(code)
    return code


async def get_authorization_code(
    session: AsyncSession, code_hash: str
) -> OAuthAuthorizationCode | None:
    """Return the authorization code matching ``code_hash``, or None.

    Does NOT delete or expiry-check — used by ``load_authorization_code``
    so the SDK token handler can run its own redirect-uri + PKCE checks
    and produce the spec-correct error. The expiry-aware single-use
    consume is :func:`consume_authorization_code`.

    Args:
        session: A ``pulse_admin`` session.
        code_hash: SHA-256 hex of the raw code.

    Returns:
        The row, or None if no such code exists.
    """
    result = await session.execute(
        select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code_hash == code_hash
        )
    )
    return result.scalar_one_or_none()


async def consume_authorization_code(
    session: AsyncSession, code_hash: str
) -> OAuthAuthorizationCode | None:
    """Load + delete an authorization code atomically (single use).

    Returns None if the code is unknown or already expired — in both
    cases the row (if any) is removed so a replayed code can never be
    redeemed. The caller (``exchange_authorization_code``) must treat a
    None as ``invalid_grant``.

    Args:
        session: A ``pulse_admin`` session.
        code_hash: SHA-256 hex of the raw code.

    Returns:
        The consumed row, or None if it was missing or expired.
    """
    code = await get_authorization_code(session, code_hash)
    if code is None:
        return None
    # Single-use guard against concurrent exchange (double-spend): the
    # DELETE row-locks the code, so only the caller whose DELETE actually
    # removes the row (rowcount 1) wins. A racing exchange in a separate
    # transaction blocks on the lock, then re-reads under READ COMMITTED
    # after the winner commits, finds the row gone, and gets rowcount 0 —
    # so it is rejected here rather than issuing a second grant.
    result = await session.execute(
        delete(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.id == code.id
        )
    )
    if result.rowcount == 0:
        return None
    if as_naive_utc(code.expires_at) < utcnow_naive():
        return None
    return code


# ── grants ──────────────────────────────────────────────────────────────────


async def create_grant(
    session: AsyncSession,
    *,
    access_prefix: str,
    access_hash: str,
    access_expires_at: datetime,
    refresh_prefix: str | None,
    refresh_hash: str | None,
    refresh_expires_at: datetime | None,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    client_id: str,
    scopes: list[str],
    resource: str | None,
) -> OAuthGrant:
    """Insert an access + refresh token grant and return it.

    Args:
        session: A ``pulse_admin`` session.
        access_prefix: First 8 chars of the access token.
        access_hash: SHA-256 hex of the access token.
        access_expires_at: Access-token expiry (naive UTC).
        refresh_prefix: First 8 chars of the refresh token, or None.
        refresh_hash: SHA-256 hex of the refresh token, or None.
        refresh_expires_at: Refresh-token expiry, or None.
        user_id: Resource owner.
        org_id: Organization the tokens are scoped to.
        client_id: The client the grant is issued to.
        scopes: Granted scopes.
        resource: RFC 8707 resource indicator, or None.

    Returns:
        The persisted ``OAuthGrant`` row, refreshed.
    """
    grant = OAuthGrant(
        access_prefix=access_prefix,
        access_hash=access_hash,
        access_expires_at=access_expires_at,
        refresh_prefix=refresh_prefix,
        refresh_hash=refresh_hash,
        refresh_expires_at=refresh_expires_at,
        user_id=user_id,
        org_id=org_id,
        client_id=client_id,
        scopes=list(scopes),
        resource=resource,
    )
    session.add(grant)
    await session.flush()
    await session.refresh(grant)
    return grant


async def get_grant_by_access_prefix(
    session: AsyncSession, access_prefix: str
) -> OAuthGrant | None:
    """Return the active grant whose access token has this prefix.

    Filters out revoked grants at the data layer (hits the active-only
    partial index). The caller constant-time-compares the candidate's
    ``access_hash`` against the presented token.

    Args:
        session: A ``pulse_admin`` session.
        access_prefix: First 8 chars of the presented access token.

    Returns:
        The candidate grant, or None.
    """
    result = await session.execute(
        select(OAuthGrant).where(
            OAuthGrant.access_prefix == access_prefix,
            OAuthGrant.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_grant_by_refresh_prefix(
    session: AsyncSession, refresh_prefix: str
) -> OAuthGrant | None:
    """Return the active grant whose refresh token has this prefix.

    Args:
        session: A ``pulse_admin`` session.
        refresh_prefix: First 8 chars of the presented refresh token.

    Returns:
        The candidate grant, or None.
    """
    result = await session.execute(
        select(OAuthGrant).where(
            OAuthGrant.refresh_prefix == refresh_prefix,
            OAuthGrant.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def rotate_grant(
    session: AsyncSession,
    *,
    grant_id: uuid.UUID,
    access_prefix: str,
    access_hash: str,
    access_expires_at: datetime,
    refresh_prefix: str | None,
    refresh_hash: str | None,
    refresh_expires_at: datetime | None,
) -> None:
    """Replace a grant's access + refresh material in place (refresh).

    The old tokens stop resolving immediately because their hashes no
    longer match any row. Membership, org, scopes, and resource are
    unchanged.

    Args:
        session: A ``pulse_admin`` session.
        grant_id: The grant row to rotate.
        access_prefix: New access-token prefix.
        access_hash: New access-token SHA-256 hex.
        access_expires_at: New access-token expiry.
        refresh_prefix: New refresh-token prefix, or None.
        refresh_hash: New refresh-token SHA-256 hex, or None.
        refresh_expires_at: New refresh-token expiry, or None.
    """
    await session.execute(
        update(OAuthGrant)
        .where(OAuthGrant.id == grant_id)
        .values(
            access_prefix=access_prefix,
            access_hash=access_hash,
            access_expires_at=access_expires_at,
            refresh_prefix=refresh_prefix,
            refresh_hash=refresh_hash,
            refresh_expires_at=refresh_expires_at,
        )
    )


async def touch_last_used(
    session: AsyncSession, grant_id: uuid.UUID
) -> None:
    """Best-effort ``last_used_at`` bump on a grant.

    Args:
        session: A ``pulse_admin`` session.
        grant_id: The grant row to touch.
    """
    await session.execute(
        update(OAuthGrant)
        .where(OAuthGrant.id == grant_id)
        .values(last_used_at=utcnow_naive())
    )


async def revoke_grant(
    session: AsyncSession, grant_id: uuid.UUID
) -> bool:
    """Set ``revoked_at`` on a grant — instant, idempotent revocation.

    Already-revoked grants return False (no change). Revoking the grant
    kills both the access and refresh tokens at once (RFC 7009 — revoking
    either side revokes the pair).

    Args:
        session: A ``pulse_admin`` session.
        grant_id: The grant row to revoke.

    Returns:
        True if a row transitioned to revoked, False otherwise.
    """
    result = await session.execute(
        update(OAuthGrant)
        .where(
            OAuthGrant.id == grant_id,
            OAuthGrant.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow_naive())
    )
    return result.rowcount > 0
