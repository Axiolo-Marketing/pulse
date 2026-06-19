"""Opaque-token primitives for the MCP OAuth authorization server.

Mirrors ``auth.api_keys`` (prefix + SHA-256 hash + constant-time compare)
but for OAuth access/refresh tokens and authorization codes. These
tokens carry full entropy (``secrets.token_urlsafe(32)`` → 256 bits), so
a plain SHA-256 + ``hmac.compare_digest`` is the right pattern — argon2's
slow compare buys nothing on full-entropy material.

Deliberately NO ``pulse_`` prefix: that literal is reserved for API keys
so the unified ``PulseTokenVerifier`` can branch on it. OAuth tokens are
opaque URL-safe strings with no recognizable prefix.

``sign_authz_request`` / ``read_authz_request`` wrap the existing
``itsdangerous`` signed-token helpers (``auth.tokens``) with a dedicated
salt + short max-age. They carry the pending authorization request from
``/authorize`` to the Pulse consent page (PR 2) without a server-side
row — the blob is signed with ``SESSION_SECRET`` and tamper-evident.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from pulse_api.auth import tokens as signed_tokens

# Entropy (in bytes) for opaque tokens. 32 bytes → 256 bits, well above
# the 128-bit floor RFC 6749 §10.10 requires for authorization codes.
_TOKEN_ENTROPY_BYTES = 32

# First-N-chars slice stored in the indexed ``*_prefix`` columns so a
# single candidate grant row can be fetched before the constant-time
# hash compare. Matches the API-key prefix width.
PREFIX_LEN = 8

# itsdangerous salt + max-age for the signed authorization-request blob.
_AUTHZ_REQUEST_PURPOSE = "mcp-oauth-authz-request"
AUTHZ_REQUEST_MAX_AGE_SECONDS = 600

# 64-char dummy SHA-256-hex used on the miss path so the constant-time
# compare runs in both branches (length parity keeps ``compare_digest``
# from short-circuiting).
_DUMMY_HASH = "0" * 64


def new_opaque_token() -> str:
    """Return a fresh opaque token with 256 bits of entropy.

    URL-safe so it survives query-string + header transport untouched.
    No ``pulse_`` prefix — these are OAuth tokens, kept distinct from API
    keys at the wire level.

    Returns:
        A random URL-safe token string.
    """
    return secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)


def prefix_of(token: str) -> str:
    """Return the first ``PREFIX_LEN`` chars used for indexed lookup.

    Mirrors ``auth.api_keys.prefix_of``: rejects tokens too short to carry
    a full prefix so a truncated/garbage credential can never collapse to
    a partial-prefix lookup. Callers in the auth path catch ``ValueError``
    and treat it as an auth failure.

    Args:
        token: The raw opaque token.

    Returns:
        The leading 8 characters of ``token``.

    Raises:
        ValueError: If ``token`` is shorter than ``PREFIX_LEN``.
    """
    if len(token) < PREFIX_LEN:
        raise ValueError(f"token too short for a {PREFIX_LEN}-char prefix")
    return token[:PREFIX_LEN]


def hash_token(token: str) -> str:
    """Return the SHA-256 hex of a token — the only form stored on disk.

    Args:
        token: The raw opaque token (or authorization code).

    Returns:
        Lowercase 64-char SHA-256 hex digest.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify(token: str, stored_hash: str | None) -> bool:
    """Constant-time compare a raw token against a stored hash.

    Never raises — the auth path feeds attacker-controlled strings
    through here. On a missing stored hash (``None``) the compare still
    runs against a dummy of equal length so timing observers can't
    distinguish "no such grant" from "wrong token".

    Args:
        token: The raw opaque token presented by the caller.
        stored_hash: The persisted SHA-256 hex, or None on a lookup miss.

    Returns:
        True iff ``token`` hashes to ``stored_hash``.
    """
    try:
        candidate = hash_token(token)
    except (AttributeError, TypeError):
        return False
    target = stored_hash if stored_hash is not None else _DUMMY_HASH
    return hmac.compare_digest(candidate, target)


def sign_authz_request(payload: dict) -> str:
    """Sign a pending-authorization-request blob for the consent page.

    Args:
        payload: The authorization request fields (client_id,
            redirect_uri, redirect_uri_provided_explicitly,
            code_challenge, scopes, state, resource).

    Returns:
        A signed, URL-safe, tamper-evident blob string.
    """
    return signed_tokens.issue_token(_AUTHZ_REQUEST_PURPOSE, payload)


def read_authz_request(blob: str) -> dict:
    """Verify + decode a signed authorization-request blob.

    Args:
        blob: The signed blob produced by :func:`sign_authz_request`.

    Returns:
        The decoded payload dict.

    Raises:
        InvalidSessionError: If the blob is tampered, malformed, or
            older than ``AUTHZ_REQUEST_MAX_AGE_SECONDS``.
    """
    return signed_tokens.consume_token(
        _AUTHZ_REQUEST_PURPOSE, blob, AUTHZ_REQUEST_MAX_AGE_SECONDS
    )
