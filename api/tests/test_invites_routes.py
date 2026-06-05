"""Tests for ``/api/orgs/me/invites`` (create, list, revoke).

The flow that exercises the most code in this module:

1. Owner POSTs ``/api/orgs/me/invites`` → 201 + email captured.
2. The email body contains a signed link → the embedded token
   resolves via ``GET /api/invites/{token}`` to ``{org_name, ...,
   status: 'pending'}``.
3. Owner DELETEs the invite → 204; the same GET now returns
   ``status: 'revoked'`` (distinct from ``'accepted'`` per 0006).

Parametrized over ``(actor_role, action, expected_status)`` for the
quick coverage matrix.
"""
from __future__ import annotations

import re
import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.password import hash_password
from pulse_api.auth.session import encode_session
from pulse_api.config import settings
from pulse_api.email import OutboundEmail


def _set_cookie(client: AsyncClient, *, user_id: str, org_id: str) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        encode_session(user_id, org_id),
    )


async def _seed_owner(db: AsyncSession, org_id: str) -> str:
    """Insert an additional owner and return its user_id."""
    user_id = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, last_active_org_id, "
                " email_verified_at) "
                "values (:e, :h, :n, cast(:o as uuid), now()) returning id::text"
            ),
            {
                "e": f"owner-{secrets.token_hex(3)}@example.com",
                "h": hash_password("a-good-password-here"),
                "n": "Owner",
                "o": org_id,
            },
        )
    ).mappings().one()["id"]
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), 'owner')"
        ),
        {"o": org_id, "u": user_id},
    )
    return user_id


async def _seed_member(db: AsyncSession, org_id: str) -> str:
    """Insert a member-role user and return its user_id."""
    user_id = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, last_active_org_id, "
                " email_verified_at) "
                "values (:e, :h, :n, cast(:o as uuid), now()) returning id::text"
            ),
            {
                "e": f"member-{secrets.token_hex(3)}@example.com",
                "h": hash_password("a-good-password-here"),
                "n": "Member",
                "o": org_id,
            },
        )
    ).mappings().one()["id"]
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), 'member')"
        ),
        {"o": org_id, "u": user_id},
    )
    return user_id


# ── POST /api/orgs/me/invites: role gating ────────────────────────────────


async def test_owner_creates_invite_sends_email(
    admin_authed: AsyncClient,
    db: AsyncSession,
    captured_emails: list[OutboundEmail],
    seed_admin_user: dict[str, str],
) -> None:
    """Owner POSTs → 201 + email with a resolvable token in the body."""
    r = await admin_authed.post(
        "/api/orgs/me/invites",
        json={"email": "newhire@example.com", "role": "member"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "newhire@example.com"
    assert body["role"] == "member"
    assert "id" in body
    # No raw token in the response — it lives in the email only.
    assert "token" not in body

    # Email captured.
    assert len(captured_emails) == 1
    msg = captured_emails[0]
    assert msg.to == "newhire@example.com"
    assert "invite" in msg.subject.lower() or "invite" in msg.body.lower()

    # Extract the signed token from the body and resolve it via the
    # public ``GET /api/invites/{token}`` endpoint.
    match = re.search(r"/invite\?token=([^\s]+)", msg.body)
    assert match is not None, msg.body
    raw_token = match.group(1)

    fresh_client = admin_authed
    fresh_client.cookies.clear()
    r2 = await fresh_client.get(f"/api/invites/{raw_token}")
    assert r2.status_code == 200, r2.text
    meta = r2.json()
    assert meta["status"] == "pending"
    assert meta["email"] == "newhire@example.com"
    assert meta["role"] == "member"
    assert meta["org_name"] == "Axiolo"
    # Defensive: org_id never leaks in the metadata.
    assert "org_id" not in meta


async def test_member_cannot_create_invite(
    client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
    captured_emails: list[OutboundEmail],
) -> None:
    """Member-role caller → 403, no email sent."""
    member_id = await _seed_member(db, axiolo_org["id"])
    await db.flush()
    _set_cookie(client, user_id=member_id, org_id=axiolo_org["id"])

    r = await client.post(
        "/api/orgs/me/invites",
        json={"email": "another@example.com", "role": "member"},
    )
    assert r.status_code == 403
    assert captured_emails == []


@pytest.mark.parametrize(
    "fixture_state, second_email, expected_status",
    [
        # Same email twice → 409 on the second.
        ("pending_invite", "duplicate@example.com", 409),
        # Email already a member → 409.
        ("existing_member", None, 409),
    ],
    ids=["duplicate-pending-invite", "already-a-member"],
)
async def test_create_invite_duplicate_paths(
    admin_authed: AsyncClient,
    db: AsyncSession,
    captured_emails: list[OutboundEmail],
    seed_admin_user: dict[str, str],
    fixture_state: str,
    second_email: str | None,
    expected_status: int,
) -> None:
    """Two ways the create-invite endpoint refuses: pending+duplicate
    and target-is-already-a-member."""
    if fixture_state == "pending_invite":
        # Create one valid invite first, then create the same again.
        r = await admin_authed.post(
            "/api/orgs/me/invites",
            json={"email": second_email, "role": "member"},
        )
        assert r.status_code == 201, r.text
        captured_emails.clear()
        r2 = await admin_authed.post(
            "/api/orgs/me/invites",
            json={"email": second_email, "role": "member"},
        )
        assert r2.status_code == expected_status, r2.text
        assert captured_emails == []
    elif fixture_state == "existing_member":
        # Seed a member with the email we will attempt to invite.
        member_id = (
            await db.execute(
                text(
                    "insert into public.users "
                    "(email, password_hash, name, last_active_org_id, "
                    " email_verified_at) "
                    "values (:e, :h, :n, cast(:o as uuid), now()) "
                    "returning id::text"
                ),
                {
                    "e": "alreadyhere@example.com",
                    "h": hash_password("a-good-password-here"),
                    "n": "AlreadyHere",
                    "o": seed_admin_user["org_id"],
                },
            )
        ).mappings().one()["id"]
        await db.execute(
            text(
                "insert into public.organization_memberships "
                "(org_id, user_id, role) "
                "values (cast(:o as uuid), cast(:u as uuid), 'member')"
            ),
            {"o": seed_admin_user["org_id"], "u": member_id},
        )
        await db.flush()

        r = await admin_authed.post(
            "/api/orgs/me/invites",
            json={"email": "alreadyhere@example.com", "role": "member"},
        )
        assert r.status_code == expected_status, r.text
        assert captured_emails == []


# ── GET /api/orgs/me/invites ──────────────────────────────────────────────


async def test_list_invites_returns_only_pending(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """List endpoint omits accepted + expired invites."""
    # Seed: one pending, one accepted, one expired.
    await db.execute(
        text(
            "insert into public.organization_invites "
            "(org_id, email, role, token_hash, "
            " invited_by_user_id, expires_at, accepted_at) "
            "values "
            "(cast(:o as uuid), 'pending@example.com', 'member', :h1, "
            " cast(:u as uuid), now() + interval '7 days', null), "
            "(cast(:o as uuid), 'accepted@example.com', 'member', :h2, "
            " cast(:u as uuid), now() + interval '7 days', now()), "
            "(cast(:o as uuid), 'expired@example.com', 'member', :h3, "
            " cast(:u as uuid), now() - interval '1 day', null) "
        ),
        {
            "o": seed_admin_user["org_id"],
            "u": seed_admin_user["id"],
            "h1": f"hash-{secrets.token_hex(8)}",
            "h2": f"hash-{secrets.token_hex(8)}",
            "h3": f"hash-{secrets.token_hex(8)}",
        },
    )
    await db.flush()

    r = await admin_authed.get("/api/orgs/me/invites")
    assert r.status_code == 200, r.text
    emails = {row["email"] for row in r.json()}
    assert emails == {"pending@example.com"}


# ── DELETE /api/orgs/me/invites/{id} ──────────────────────────────────────


async def test_owner_revokes_invite_then_resolves_as_revoked(
    admin_authed: AsyncClient,
    captured_emails: list[OutboundEmail],
) -> None:
    """Revoke flips the public-resolve status to ``"revoked"``."""
    # Create.
    r = await admin_authed.post(
        "/api/orgs/me/invites",
        json={"email": "revokeme@example.com", "role": "member"},
    )
    assert r.status_code == 201, r.text
    invite_id = r.json()["id"]
    msg = captured_emails[0]
    match = re.search(r"/invite\?token=([^\s]+)", msg.body)
    assert match is not None
    raw_token = match.group(1)

    # Revoke.
    r2 = await admin_authed.delete(f"/api/orgs/me/invites/{invite_id}")
    assert r2.status_code == 204

    # Public resolve → status now "revoked" (a dedicated revoked_at
    # column distinguishes this from "accepted" as of 0006).
    public = admin_authed
    public.cookies.clear()
    r3 = await public.get(f"/api/invites/{raw_token}")
    assert r3.status_code == 200, r3.text
    assert r3.json()["status"] == "revoked"


async def test_revoke_then_accept_returns_410(
    admin_authed: AsyncClient,
    captured_emails: list[OutboundEmail],
    client: AsyncClient,
) -> None:
    """Once revoked, the accept endpoint returns 410 — no recovery
    via the token."""
    r = await admin_authed.post(
        "/api/orgs/me/invites",
        json={"email": "revoke-then-accept@example.com", "role": "member"},
    )
    assert r.status_code == 201, r.text
    invite_id = r.json()["id"]
    msg = captured_emails[0]
    match = re.search(r"/invite\?token=([^\s]+)", msg.body)
    assert match is not None
    raw_token = match.group(1)

    r2 = await admin_authed.delete(f"/api/orgs/me/invites/{invite_id}")
    assert r2.status_code == 204

    # Accept attempt now returns 410.
    public = client
    public.cookies.clear()
    r3 = await public.post(
        f"/api/invites/{raw_token}/accept",
        json={
            "auth": "password",
            "password": "a-good-password-here",
            "name": "Tries To Accept",
        },
    )
    assert r3.status_code == 410, r3.text
    assert "revoked" in r3.json()["detail"].lower()


async def test_revoke_unknown_invite_returns_404(
    admin_authed: AsyncClient,
) -> None:
    """Bad/unknown id → 404."""
    import uuid as _uuid

    r = await admin_authed.delete(
        f"/api/orgs/me/invites/{_uuid.uuid4()}"
    )
    assert r.status_code == 404
