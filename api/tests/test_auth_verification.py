"""Endpoint tests for email verification + forgot/reset password.

These exercise the signed-token flow end-to-end: an endpoint issues a
token + sends an email, the captured_emails fixture records what would
have been sent, the test extracts the token from the email body and posts
it back to a redemption endpoint.
"""
from __future__ import annotations

import re

import pytest
from freezegun import freeze_time
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.tokens import issue_token
from pulse_api.config import settings
from pulse_api.email import OutboundEmail


def _extract_token(emails: list[OutboundEmail], rel_path: str) -> str:
    """Pull the `token=...` query value out of the first matching email body."""
    for e in emails:
        m = re.search(rf"{re.escape(rel_path)}\?token=([^\s]+)", e.body)
        if m:
            return m.group(1)
    raise AssertionError(f"no email containing {rel_path}?token=... in {len(emails)} captures")


# ── verify-email ──────────────────────────────────────────────────────────


async def test_signup_sends_verification_email(
    client: AsyncClient, captured_emails: list[OutboundEmail]
) -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"email": "verify-me@example.com", "password": "long-enough-password"},
    )
    assert r.status_code == 201

    assert len(captured_emails) == 1
    e = captured_emails[0]
    assert e.to == "verify-me@example.com"
    assert "verify" in e.subject.lower()
    assert "verify-email?token=" in e.body
    assert settings.frontend_base_url in e.body


async def test_verify_email_marks_user_verified(
    client: AsyncClient,
    captured_emails: list[OutboundEmail],
    db: AsyncSession,
) -> None:
    await client.post(
        "/api/auth/signup",
        json={"email": "to-verify@example.com", "password": "long-enough-password"},
    )
    token = _extract_token(captured_emails, "/verify-email")

    r = await client.post("/api/auth/verify-email", json={"token": token})
    assert r.status_code == 200
    assert r.json()["email"] == "to-verify@example.com"
    assert r.json()["email_verified_at"] is not None

    row = (
        await db.execute(
            text("select email_verified_at from public.users where email='to-verify@example.com'")
        )
    ).mappings().one()
    assert row["email_verified_at"] is not None


async def test_verify_email_is_idempotent(
    client: AsyncClient, captured_emails: list[OutboundEmail]
) -> None:
    """A user re-clicking the link after verification should not error and
    should not move the verified-at timestamp."""
    await client.post(
        "/api/auth/signup",
        json={"email": "click-twice@example.com", "password": "long-enough-password"},
    )
    token = _extract_token(captured_emails, "/verify-email")
    r1 = await client.post("/api/auth/verify-email", json={"token": token})
    assert r1.status_code == 200
    first_ts = r1.json()["email_verified_at"]

    r2 = await client.post("/api/auth/verify-email", json={"token": token})
    assert r2.status_code == 200
    # mark_email_verified is a no-op when already verified, so the
    # timestamp stays put.
    assert r2.json()["email_verified_at"] == first_ts


@pytest.mark.parametrize(
    "bad_token, expected_substring",
    [
        ("not-a-real-token", "invalid"),
        ("a.b.c", "invalid"),
        ("", "invalid"),
    ],
)
async def test_verify_email_rejects_garbage_token(
    client: AsyncClient, bad_token: str, expected_substring: str
) -> None:
    r = await client.post("/api/auth/verify-email", json={"token": bad_token})
    assert r.status_code == 400
    assert expected_substring in r.json()["detail"].lower()


async def test_verify_email_rejects_token_for_other_purpose(client: AsyncClient) -> None:
    """A token signed for a different purpose (e.g. password-reset) must
    not be redeemable as a verification token, even though the signing key
    is the same."""
    foreign = issue_token("password-reset", {"user_id": "00000000-0000-0000-0000-000000000001"})
    r = await client.post("/api/auth/verify-email", json={"token": foreign})
    assert r.status_code == 400


async def test_verify_email_rejects_expired_token(client: AsyncClient) -> None:
    with freeze_time("2026-05-01 12:00:00"):
        stale = issue_token("email-verify", {"user_id": "00000000-0000-0000-0000-000000000099"})
    # 8 days later — token's 7-day window has expired
    with freeze_time("2026-05-09 12:00:00"):
        r = await client.post("/api/auth/verify-email", json={"token": stale})
        assert r.status_code == 400
        assert "expired" in r.json()["detail"].lower()


# ── forgot-password ───────────────────────────────────────────────────────


async def test_forgot_password_sends_email_when_user_exists(
    client: AsyncClient,
    seed_user: dict[str, str],
    captured_emails: list[OutboundEmail],
) -> None:
    r = await client.post("/api/auth/forgot-password", json={"email": seed_user["email"]})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    assert len(captured_emails) == 1
    assert captured_emails[0].to == seed_user["email"]
    assert "/reset-password?token=" in captured_emails[0].body


async def test_forgot_password_silent_for_unknown_email(
    client: AsyncClient, captured_emails: list[OutboundEmail]
) -> None:
    """Must not leak whether an email is registered — same response either way."""
    r = await client.post(
        "/api/auth/forgot-password", json={"email": "ghost@example.com"}
    )
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert captured_emails == []


async def test_forgot_password_silent_for_oauth_only_user(
    client: AsyncClient,
    captured_emails: list[OutboundEmail],
    db: AsyncSession,
) -> None:
    await db.execute(
        text(
            "insert into public.users (email, password_hash, email_verified_at) "
            "values ('oauth@example.com', null, now())"
        )
    )
    r = await client.post(
        "/api/auth/forgot-password", json={"email": "oauth@example.com"}
    )
    assert r.status_code == 200
    assert captured_emails == []


# ── reset-password ────────────────────────────────────────────────────────


async def test_reset_password_full_flow(
    client: AsyncClient,
    seed_user: dict[str, str],
    captured_emails: list[OutboundEmail],
) -> None:
    # Request reset
    await client.post("/api/auth/forgot-password", json={"email": seed_user["email"]})
    token = _extract_token(captured_emails, "/reset-password")

    # Redeem
    r = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "a-brand-new-password"},
    )
    assert r.status_code == 200

    # Old password no longer works
    r = await client.post(
        "/api/auth/login",
        json={"email": seed_user["email"], "password": seed_user["password"]},
    )
    assert r.status_code == 401

    # New password works
    r = await client.post(
        "/api/auth/login",
        json={"email": seed_user["email"], "password": "a-brand-new-password"},
    )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "payload, expected_status",
    [
        ({"token": "not-real", "new_password": "long-enough-password"}, 400),
        ({"token": "x.y.z", "new_password": "long-enough-password"}, 400),
        ({"token": "", "new_password": "long-enough-password"}, 400),
        ({"token": "anything", "new_password": "short"}, 422),  # min_length 8
        ({"new_password": "long-enough-password"}, 422),  # missing token
    ],
)
async def test_reset_password_validation(
    client: AsyncClient, payload: dict, expected_status: int
) -> None:
    r = await client.post("/api/auth/reset-password", json=payload)
    assert r.status_code == expected_status


async def test_reset_password_rejects_expired_token(client: AsyncClient) -> None:
    with freeze_time("2026-05-13 12:00:00"):
        stale = issue_token("password-reset", {"user_id": "00000000-0000-0000-0000-000000000001"})
    # 2 hours later — token's 1-hour window has expired
    with freeze_time("2026-05-13 14:00:00"):
        r = await client.post(
            "/api/auth/reset-password",
            json={"token": stale, "new_password": "another-long-password"},
        )
        assert r.status_code == 400
        assert "expired" in r.json()["detail"].lower()


async def test_reset_password_rejects_verify_email_token(
    client: AsyncClient, seed_user: dict[str, str]
) -> None:
    """Salt isolation — a verify-email token must not redeem as a password reset."""
    foreign = issue_token("email-verify", {"user_id": seed_user["id"]})
    r = await client.post(
        "/api/auth/reset-password",
        json={"token": foreign, "new_password": "another-long-password"},
    )
    assert r.status_code == 400
