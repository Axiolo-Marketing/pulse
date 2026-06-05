"""Tests for ``POST /api/invites/{token}/accept``.

Covers the password and OAuth-redirect flows plus the lifecycle states
that must reject. Parametrized over ``(invite_state, auth_method,
expected_outcome)``.
"""
from __future__ import annotations

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.session import decode_session_payload
from pulse_api.auth.tokens import issue_token
from pulse_api.config import settings
from pulse_api.repos.invites import hash_invite_token


async def _seed_invite(
    db: AsyncSession,
    *,
    org_id: str,
    inviter_id: str,
    email: str,
    role: str = "member",
    lifecycle: str = "pending",
) -> str:
    """Insert an invite in the requested lifecycle state.

    Returns the raw signed token. Lifecycle:

    * ``"pending"``  — expires in 7 days, not accepted.
    * ``"expired"``  — expires 1 day ago, not accepted.
    * ``"accepted"`` — expires in 7 days, accepted now.
    """
    expires_clause = (
        "now() + interval '7 days'"
        if lifecycle != "expired"
        else "now() - interval '1 day'"
    )
    accepted_clause = "now()" if lifecycle == "accepted" else "null"
    invite_id = (
        await db.execute(
            text(
                f"insert into public.organization_invites "
                f"(org_id, email, role, token_hash, "
                f" invited_by_user_id, expires_at, accepted_at) "
                f"values "
                f"(cast(:o as uuid), :e, :r, :placeholder, "
                f" cast(:b as uuid), {expires_clause}, {accepted_clause}) "
                f"returning id::text"
            ),
            {
                "o": org_id,
                "e": email.lower(),
                "r": role,
                "placeholder": f"placeholder-{secrets.token_hex(8)}",
                "b": inviter_id,
            },
        )
    ).mappings().one()["id"]
    raw_token = issue_token("org-invite", {"invite_id": invite_id})
    await db.execute(
        text(
            "update public.organization_invites set token_hash = :h "
            "where id = cast(:i as uuid)"
        ),
        {"h": hash_invite_token(raw_token), "i": invite_id},
    )
    return raw_token


# ── Parametrized state × auth-method matrix ───────────────────────────────


@pytest.mark.parametrize(
    "lifecycle, auth_method, expected_status",
    [
        ("pending",  "password", 200),
        ("expired",  "password", 410),
        ("accepted", "password", 410),
        ("pending",  "google",   200),
        ("pending",  "microsoft", 200),
    ],
    ids=[
        "pending+password-200",
        "expired+password-410",
        "accepted+password-410",
        "pending+google-redirect",
        "pending+microsoft-redirect",
    ],
)
async def test_accept_matrix(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    lifecycle: str,
    auth_method: str,
    expected_status: int,
) -> None:
    """``POST /api/invites/{token}/accept`` matrix."""
    raw_token = await _seed_invite(
        db,
        org_id=seed_admin_user["org_id"],
        inviter_id=seed_admin_user["id"],
        email=f"target-{secrets.token_hex(3)}@example.com",
        lifecycle=lifecycle,
    )
    await db.flush()

    if auth_method == "password":
        body = {
            "auth": "password",
            "password": "a-good-password-here",
            "name": "Test",
        }
    else:
        body = {"auth": auth_method}

    r = await client.post(f"/api/invites/{raw_token}/accept", json=body)
    assert r.status_code == expected_status, r.text

    if expected_status == 200 and auth_method == "password":
        data = r.json()
        assert "user_id" in data
        assert data["org_id"] == seed_admin_user["org_id"]
        assert data["role"] == "member"
        # Session cookie issued.
        cookie = r.cookies.get(settings.session_cookie_name)
        assert cookie is not None
        payload = decode_session_payload(
            cookie, settings.session_max_age_seconds
        )
        assert payload["user_id"] == data["user_id"]
        assert payload["active_org_id"] == seed_admin_user["org_id"]
        # Membership written.
        row = (
            await db.execute(
                text(
                    "select role from public.organization_memberships "
                    "where org_id = cast(:o as uuid) "
                    "  and user_id = cast(:u as uuid)"
                ),
                {"o": seed_admin_user["org_id"], "u": data["user_id"]},
            )
        ).mappings().one()
        assert row["role"] == "member"

    if expected_status == 200 and auth_method in ("google", "microsoft"):
        data = r.json()
        # The redirect URL embeds the same raw token in the query.
        assert data["redirect_url"].startswith(
            f"/api/auth/{auth_method}/authorize?invite_token="
        )
        assert raw_token in data["redirect_url"]


# ── Public GET /api/invites/{token} basics ────────────────────────────────


async def test_get_invite_returns_status_pending(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A fresh invite resolves to ``status='pending'`` + redacted org_id."""
    raw_token = await _seed_invite(
        db,
        org_id=seed_admin_user["org_id"],
        inviter_id=seed_admin_user["id"],
        email="nicepending@example.com",
        role="owner",
    )
    await db.flush()

    r = await client.get(f"/api/invites/{raw_token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["email"] == "nicepending@example.com"
    assert body["role"] == "owner"
    assert body["org_name"] == "Axiolo"
    assert "org_id" not in body


async def test_get_invite_unknown_token_404(client: AsyncClient) -> None:
    """A bogus token → 404 (the route protects against hash-only lookups
    by requiring a valid signature)."""
    r = await client.get("/api/invites/this-is-not-a-real-token")
    assert r.status_code == 404


# ── Two-tab / double-accept race ──────────────────────────────────────────


async def test_double_accept_returns_410(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Two sequential POSTs against the same token: first succeeds,
    second returns 410. Proves the conditional ``accept_atomically``
    UPDATE catches the second attempt even after the first completed."""
    raw_token = await _seed_invite(
        db,
        org_id=seed_admin_user["org_id"],
        inviter_id=seed_admin_user["id"],
        email="double-accept@example.com",
    )
    await db.flush()

    body = {
        "auth": "password",
        "password": "a-good-password-here",
        "name": "First",
    }

    r1 = await client.post(f"/api/invites/{raw_token}/accept", json=body)
    assert r1.status_code == 200, r1.text

    # Drop the session cookie minted by the first accept so the second
    # call runs as an unauthenticated caller — otherwise the cookie
    # would be irrelevant but the test reads cleaner.
    client.cookies.clear()
    r2 = await client.post(f"/api/invites/{raw_token}/accept", json=body)
    assert r2.status_code == 410, r2.text


async def test_concurrent_acceptors_only_one_succeeds(
    db_conn,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fire two acceptance requests in parallel via ``asyncio.gather``;
    assert exactly one 200 and exactly one 410.

    Both clients hit the same FastAPI app and (via the conftest
    overrides) the same DB transaction connection — the FOR UPDATE
    lock on the invite row plus the conditional ``accept_atomically``
    UPDATE serialize the writers, and only the first claim survives.
    """
    import asyncio

    from fastapi import Depends, Header, HTTPException
    from httpx import ASGITransport, AsyncClient as _AsyncClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from pulse_api.auth.middleware import (
        get_current_org_member,
        get_org_scoped_session,
    )
    from pulse_api.db import get_admin_session, get_anon_session, get_session
    from pulse_api.main import app
    from pulse_api.observability import limiter

    limiter.reset()

    raw_token = await _seed_invite(
        db,
        org_id=seed_admin_user["org_id"],
        inviter_id=seed_admin_user["id"],
        email="concurrent-accept@example.com",
    )
    await db.flush()

    async def _override_session():
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=_AsyncSession
        )
        async with factory() as session:
            yield session

    async def _override_anon(
        x_pulse_token: str | None = Header(default=None, alias="X-Pulse-Token"),
    ):
        if not x_pulse_token:
            raise HTTPException(status_code=401, detail="missing token")
        await db_conn.execute(text("set local role pulse_anon"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=_AsyncSession
        )
        async with factory() as session:
            yield session

    async def _override_org_scoped(
        org_member=Depends(get_current_org_member),
    ):
        _, membership = org_member
        await db_conn.execute(text("reset role"))
        await db_conn.execute(text("set local role pulse_member"))
        await db_conn.execute(
            text("select set_config('pulse.org_id', :o, true)"),
            {"o": str(membership.org_id)},
        )
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=_AsyncSession
        )
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_admin_session] = _override_session
    app.dependency_overrides[get_anon_session] = _override_anon
    app.dependency_overrides[get_org_scoped_session] = _override_org_scoped

    body = {
        "auth": "password",
        "password": "a-good-password-here",
        "name": "Racer",
    }
    transport = ASGITransport(app=app)
    try:
        async with _AsyncClient(
            transport=transport, base_url="http://test"
        ) as client_a, _AsyncClient(
            transport=transport, base_url="http://test"
        ) as client_b:
            r1, r2 = await asyncio.gather(
                client_a.post(f"/api/invites/{raw_token}/accept", json=body),
                client_b.post(f"/api/invites/{raw_token}/accept", json=body),
            )
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_admin_session, None)
        app.dependency_overrides.pop(get_anon_session, None)
        app.dependency_overrides.pop(get_org_scoped_session, None)

    statuses = sorted([r1.status_code, r2.status_code])
    # Exactly one winner, one 410. (A passing 500 means the locking
    # path crashed — assert against the expected pair to surface it.)
    assert statuses == [200, 410], (r1.status_code, r1.text, r2.status_code, r2.text)
