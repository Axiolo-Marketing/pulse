"""Unit tests for the auth email link builders. No DB, no FastAPI.

These lock in the routing contract between outbound emails and the
frontend: the admin shell (v1 ``src/scripts/admin.ts`` and v2
``src/components/admin/AdminApp.tsx``) serves the verify-email and
reset-password flows off ``/admin/?verify-email-token=…`` and
``/admin/?reset-password-token=…`` query params — there are no standalone
``/verify-email`` or ``/reset-password`` pages. A link built against any
other path 404s and the user can never finish the flow (this shipped once;
see the regression these tests pin down).
"""
from __future__ import annotations

import pytest

from pulse_api.auth.email_messages import password_reset_email, verification_email
from pulse_api.config import settings


@pytest.fixture
def base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin the frontend base URL (with a trailing slash to prove it's stripped)."""
    monkeypatch.setattr(settings, "frontend_base_url", "https://pulse.test/")
    return "https://pulse.test"


def test_password_reset_link_targets_admin_query_param(base_url: str) -> None:
    subject, body = password_reset_email("tok123", name="Tom")
    assert f"{base_url}/admin/?reset-password-token=tok123" in body
    assert "Hi Tom," in body
    assert "reset" in subject.lower()


def test_password_reset_link_never_uses_the_old_standalone_path(base_url: str) -> None:
    _, body = password_reset_email("tok123")
    assert "/reset-password?token=" not in body


def test_verification_link_targets_admin_query_param(base_url: str) -> None:
    subject, body = verification_email("tok456")
    assert f"{base_url}/admin/?verify-email-token=tok456" in body
    assert "Hi," in body
    assert "verify" in subject.lower()


def test_verification_link_never_uses_the_old_standalone_path(base_url: str) -> None:
    _, body = verification_email("tok456")
    assert "/verify-email?token=" not in body
