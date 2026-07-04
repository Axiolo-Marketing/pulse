"""Unit tests for the rate-limiter's client-IP resolution.

``_client_ip`` is the key function fed to slowapi's ``Limiter`` — if it
trusts an attacker-controlled header, every per-IP rate limit is
trivially bypassable (each request just picks a fresh fake IP). These
tests lock in the trust order: X-Real-IP (nginx-set) first, then the
LAST hop of X-Forwarded-For (the one nginx itself appended), then the
direct peer, with no DB/app wiring required.
"""
from unittest.mock import MagicMock

from pulse_api.observability import _client_ip


def _make_request(headers: dict[str, str], peer_host: str | None = "203.0.113.9") -> MagicMock:
    """Build a minimal stand-in for ``starlette.Request`` with just the
    attributes ``_client_ip`` reads."""
    request = MagicMock()
    request.headers = headers
    if peer_host is None:
        request.client = None
    else:
        request.client = MagicMock(host=peer_host)
    return request


def test_client_ip_prefers_x_real_ip_over_everything() -> None:
    """X-Real-IP is nginx-set (not client-appended), so it wins even when
    an attacker stuffs a bogus X-Forwarded-For alongside it."""
    request = _make_request(
        {
            "x-real-ip": "198.51.100.7",
            "x-forwarded-for": "1.2.3.4, 198.51.100.7",
        }
    )
    assert _client_ip(request) == "198.51.100.7"


def test_client_ip_uses_last_xff_hop_when_no_real_ip() -> None:
    """The leftmost X-Forwarded-For entry is attacker-controlled (a client
    can prepend anything before nginx appends its own hop via
    $proxy_add_x_forwarded_for) — only the rightmost, closest-to-us hop
    is trustworthy."""
    request = _make_request(
        {"x-forwarded-for": "9.9.9.9, 8.8.8.8, 198.51.100.7"}
    )
    assert _client_ip(request) == "198.51.100.7"


def test_client_ip_falls_back_to_direct_peer() -> None:
    """No proxy headers at all (e.g. hitting the app directly in a test
    or a misconfigured deploy) falls back to the raw peer address."""
    request = _make_request({}, peer_host="203.0.113.42")
    assert _client_ip(request) == "203.0.113.42"


def test_client_ip_falls_back_to_unknown_when_no_client() -> None:
    """Some ASGI transports (e.g. certain test clients) never populate
    ``request.client`` — don't crash, return a sentinel instead."""
    request = _make_request({}, peer_host=None)
    assert _client_ip(request) == "unknown"
