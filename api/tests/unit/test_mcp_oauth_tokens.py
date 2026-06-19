"""Unit tests for the MCP OAuth opaque-token primitives.

Pure functions — no DB, no FastAPI. The provider/verifier wiring +
persistence is covered by ``tests/test_oauth_provider.py``.
"""
from __future__ import annotations

import pytest

from pulse_api.auth.session import InvalidSessionError
from pulse_api.config import settings
from pulse_api.mcp.oauth.tokens import (
    PREFIX_LEN,
    hash_token,
    new_opaque_token,
    prefix_of,
    read_authz_request,
    sign_authz_request,
    verify,
)


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure a signing secret is set so authz-request blobs can sign."""
    monkeypatch.setattr(settings, "session_secret", "test-secret-please")


# ── opaque token roundtrip ─────────────────────────────────────────────────


def test_new_opaque_token_has_no_pulse_prefix() -> None:
    token = new_opaque_token()
    assert not token.startswith("pulse_"), "OAuth tokens must not look like keys"
    assert len(token) >= 32


def test_prefix_length_is_eight() -> None:
    token = new_opaque_token()
    assert len(prefix_of(token)) == PREFIX_LEN == 8
    assert prefix_of(token) == token[:8]


def test_verify_accepts_matching_token_and_hash() -> None:
    token = new_opaque_token()
    assert verify(token, hash_token(token)) is True


def test_verify_rejects_tampered_token() -> None:
    token = new_opaque_token()
    stored = hash_token(token)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert verify(tampered, stored) is False


def test_verify_rejects_none_hash_without_raising() -> None:
    # Miss path: a None stored hash (no candidate row) is always False.
    assert verify(new_opaque_token(), None) is False


# ── authz-request sign/read roundtrip ──────────────────────────────────────


def test_authz_request_roundtrip_preserves_payload() -> None:
    payload = {
        "client_id": "abc123",
        "redirect_uri": "https://claude.ai/callback",
        "redirect_uri_provided_explicitly": True,
        "code_challenge": "challenge-string",
        "scopes": ["mcp"],
        "state": "xyz",
        "resource": "https://pulse.axiolo.com/api/mcp",
    }
    blob = sign_authz_request(payload)
    assert read_authz_request(blob) == payload


def test_authz_request_rejects_tampered_blob() -> None:
    blob = sign_authz_request({"client_id": "abc"})
    tampered = blob[:-2] + ("xy" if blob[-2:] != "xy" else "za")
    with pytest.raises(InvalidSessionError):
        read_authz_request(tampered)
