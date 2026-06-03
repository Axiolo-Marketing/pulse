"""Unit tests for `auth.api_keys` primitives.

These are pure functions — no DB, no FastAPI. The endpoint-layer tests
in `tests/test_api_keys.py` cover wiring + persistence + auth.
"""
from __future__ import annotations

import re

from pulse_api.auth.api_keys import (
    KEY_PREFIX,
    PREFIX_LEN,
    generate_key,
    hash_key,
    prefix_of,
    verify_key,
)


# 1. `generate_key()` produces a `pulse_` + 32-hex string with prefix length 8.
def test_generate_key_shape_and_prefix_length() -> None:
    raw = generate_key()
    assert raw.startswith(KEY_PREFIX)
    body = raw[len(KEY_PREFIX):]
    assert len(body) == 32
    assert re.fullmatch(r"[0-9a-f]{32}", body), "body must be lowercase hex"

    prefix = prefix_of(raw)
    assert len(prefix) == PREFIX_LEN == 8
    assert prefix == body[:8]


# 2a. `verify_key(raw, hash_key(raw))` is True.
def test_verify_key_accepts_matching_raw_and_hash() -> None:
    raw = generate_key()
    assert verify_key(raw, hash_key(raw)) is True


# 2b. Tampered raw is False.
def test_verify_key_rejects_tampered_raw() -> None:
    raw = generate_key()
    stored = hash_key(raw)
    # Flip the last hex char so the SHA-256 changes but the prefix doesn't.
    last = raw[-1]
    tampered = raw[:-1] + ("0" if last != "0" else "1")
    assert verify_key(tampered, stored) is False
