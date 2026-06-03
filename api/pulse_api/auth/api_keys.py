"""Per-user API-key primitives — generate, prefix-extract, hash, verify.

Stdlib only. Keys carry 128 bits of entropy (`secrets.token_hex(16)`), so a
plain SHA-256 + constant-time compare is the right pattern — argon2's slow
comparison is wasted on full-entropy material.

Format: `pulse_<32-hex>`. The `pulse_` literal makes leaked keys greppable
in logs and source repos. The first 8 hex chars after the underscore go in
an indexed `prefix` column so auth can fetch a single candidate row before
running the constant-time hash compare.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

KEY_PREFIX = "pulse_"
RAW_HEX_LEN = 32
PREFIX_LEN = 8


def generate_key() -> str:
    """Return a fresh `pulse_<32-hex>` key. 128 bits of entropy."""
    return f"{KEY_PREFIX}{secrets.token_hex(RAW_HEX_LEN // 2)}"


def prefix_of(raw: str) -> str:
    """Return the 8-char prefix used for indexed lookup.

    Raises ValueError if `raw` doesn't match the expected `pulse_<32-hex>`
    shape — callers in the auth path catch this and return 401 rather than
    leaking the bug as a 500.
    """
    if not raw.startswith(KEY_PREFIX):
        raise ValueError("key missing 'pulse_' prefix")
    body = raw[len(KEY_PREFIX):]
    if len(body) != RAW_HEX_LEN:
        raise ValueError("key body must be 32 hex chars")
    return body[:PREFIX_LEN]


def hash_key(raw: str) -> str:
    """SHA-256 hex of the raw key. The only form we store on disk."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_key(raw: str, stored_hash: str) -> bool:
    """Constant-time compare against a stored hash.

    Returns False (never raises) for any malformed input — the auth path
    feeds attacker-controlled strings through here.
    """
    try:
        candidate = hash_key(raw)
    except (AttributeError, TypeError):
        return False
    return hmac.compare_digest(candidate, stored_hash)
