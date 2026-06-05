"""Rate-limit smoke tests.

slowapi's bucket is shared across requests in the same process, so each
test sees the limit applied per-IP (the test client's IP, 127.0.0.1).
The `client` conftest fixture calls `limiter.reset()` before yielding so
each test starts with a fresh budget.
"""
import pytest
from httpx import AsyncClient

from pulse_api.config import settings


@pytest.fixture(autouse=True)
def _enable_signup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The signup parametrize case below probes the rate limiter, not the
    invite-only gate; opt in to the open endpoint."""
    monkeypatch.setattr(settings, "signup_enabled", True)


def _parse_per_minute(rule: str) -> int:
    return int(rule.split("/")[0])


# ── login: 10/minute by default ───────────────────────────────────────────


async def test_login_rate_limit_triggers_429(client: AsyncClient) -> None:
    n = _parse_per_minute(settings.rate_limit_token_validation)

    # Burn through the budget with intentionally-invalid credentials.
    # All requests up to and including n should return 401 (slowapi
    # treats the rate-limit window inclusively of n).
    last_status = None
    for _ in range(n):
        r = await client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "longwrong-password"},
        )
        last_status = r.status_code
    assert last_status == 401

    # The (n+1)th call must be rate-limited.
    r = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "longwrong-password"},
    )
    assert r.status_code == 429
    assert "too many" in r.json()["detail"].lower()


# ── signup + forgot-password: 5/minute ────────────────────────────────────


@pytest.mark.parametrize(
    "path, payload",
    [
        ("/api/auth/signup", {"email": "a@example.com", "password": "long-enough"}),
        ("/api/auth/forgot-password", {"email": "a@example.com"}),
    ],
)
async def test_account_enumeration_endpoints_are_tightly_limited(
    client: AsyncClient, path: str, payload: dict
) -> None:
    n = _parse_per_minute(settings.rate_limit_account_enumeration)

    # Each iteration uses a unique email so signup doesn't 409 — we want
    # to hit the rate limiter, not the dedup check.
    for i in range(n):
        body = {**payload, "email": f"user{i}@example.com"}
        r = await client.post(path, json=body)
        assert r.status_code in {200, 201}, (
            f"unexpected pre-limit status on {path}: {r.status_code} {r.text}"
        )

    r = await client.post(path, json={**payload, "email": "overflow@example.com"})
    assert r.status_code == 429


# ── /api/me: 10/minute, per-IP token validation ──────────────────────────


async def test_me_rate_limit_triggers_429(
    client: AsyncClient, seed_client: dict[str, str]
) -> None:
    client.headers["X-Pulse-Token"] = seed_client["token"]
    n = _parse_per_minute(settings.rate_limit_token_validation)

    for _ in range(n):
        r = await client.get("/api/me")
        assert r.status_code == 200

    r = await client.get("/api/me")
    assert r.status_code == 429


# ── Healthz is NOT rate-limited (would defeat upstream probes) ─────────────


async def test_healthz_is_under_the_default_limit_only(client: AsyncClient) -> None:
    # 5 quick hits — well under the default 60/min — must all succeed.
    for _ in range(5):
        r = await client.get("/healthz")
        assert r.status_code == 200
