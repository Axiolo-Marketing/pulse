"""Unit tests for `pulse_api.email.send_email`. No DB, no FastAPI.

Resend is mocked via respx. These lock in two things: the HTTPS contract
(URL, Bearer auth, JSON payload shape) and the best-effort/never-raise
behavior — a Resend failure must not propagate, because two callers send
before their `session.commit()` and a raising send would roll back tenant
or account state.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from pulse_api.config import settings
from pulse_api.email import RESEND_API_URL, send_email


@pytest.fixture
def resend_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give `settings` a usable Resend key + from-address for the test."""
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "email_from", "Pulse <noreply@pulse.test>")


async def test_send_email_posts_to_resend(resend_configured: None) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.post(RESEND_API_URL).mock(
            return_value=httpx.Response(200, json={"id": "ec-123"})
        )
        await send_email("client@theirco.com", "Subject line", "Body text")

    assert route.called
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer re_test_key"
    assert json.loads(request.content) == {
        "from": "Pulse <noreply@pulse.test>",
        "to": ["client@theirco.com"],
        "subject": "Subject line",
        "text": "Body text",
    }


async def test_send_email_no_key_is_log_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "resend_api_key", "")
    with respx.mock(assert_all_called=False) as router:
        route = router.post(RESEND_API_URL).mock(return_value=httpx.Response(200))
        await send_email("a@b.com", "subject", "body")

    assert not route.called


async def test_send_email_non_2xx_does_not_raise(resend_configured: None) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(RESEND_API_URL).mock(
            return_value=httpx.Response(500, text="upstream error")
        )
        # Must return normally despite the 500 — never raises.
        await send_email("a@b.com", "subject", "body")


async def test_send_email_network_error_does_not_raise(
    resend_configured: None,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(RESEND_API_URL).mock(side_effect=httpx.ConnectError("boom"))
        # Connection failure is swallowed and logged, not propagated.
        await send_email("a@b.com", "subject", "body")
