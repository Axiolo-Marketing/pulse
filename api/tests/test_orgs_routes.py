"""Tests for ``/api/orgs/me`` (read/update) and ``/api/me/{orgs,switch-org}``.

Two concerns share this module:

* Single-org read/update — the seeded admin user fixture is an owner
  of Axiolo, so the ``GET /api/orgs/me`` and ``PATCH /api/orgs/me``
  paths can run directly against it.
* Multi-org list/switch — built ad hoc per test by inserting a second
  org and a second membership row, then exercising the switch flow
  parametrically across (valid, foreign, malformed) targets.
"""
from __future__ import annotations

import secrets
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.session import (
    decode_session_payload,
)
from pulse_api.config import settings


async def _insert_org(db: AsyncSession, *, name: str) -> str:
    """Insert an org row and return its UUID as a string."""
    row = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values (:n, :s) returning id::text"
            ),
            {"n": name, "s": f"{name.lower()}-{secrets.token_hex(4)}"},
        )
    ).mappings().one()
    return row["id"]


async def _add_membership(
    db: AsyncSession, *, user_id: str, org_id: str, role: str = "owner"
) -> None:
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), :r)"
        ),
        {"o": org_id, "u": user_id, "r": role},
    )


# ── GET /api/orgs/me ──────────────────────────────────────────────────────


async def test_get_my_org_returns_active_org(
    admin_authed: AsyncClient,
    seed_admin_user: dict[str, str],
) -> None:
    """The Settings header endpoint returns name, slug, role, counts."""
    r = await admin_authed.get("/api/orgs/me")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"] == seed_admin_user["org_id"]
    assert data["slug"] == "axiolo"
    assert data["role"] == "owner"
    # The single seeded owner is the only member; no invites exist.
    assert data["member_count"] == 1
    assert data["pending_invite_count"] == 0


async def test_get_my_org_requires_auth(client: AsyncClient) -> None:
    """No cookie → 401."""
    r = await client.get("/api/orgs/me")
    assert r.status_code == 401


async def test_get_my_org_reflects_reactive_cards_allowed_after_toggle(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """`reactive_cards_allowed` defaults false and carries the
    superadmin-managed org flag once flipped — the admin-facing
    engagement toggle reads this field to decide whether it's
    selectable at all."""
    r0 = await admin_authed.get("/api/orgs/me")
    assert r0.status_code == 200, r0.text
    assert r0.json()["reactive_cards_allowed"] is False

    await db.execute(
        text(
            "update public.organizations set reactive_cards_allowed = true "
            "where id = cast(:o as uuid)"
        ),
        {"o": seed_admin_user["org_id"]},
    )
    await db.flush()

    r1 = await admin_authed.get("/api/orgs/me")
    assert r1.status_code == 200, r1.text
    assert r1.json()["reactive_cards_allowed"] is True


# ── PATCH /api/orgs/me ────────────────────────────────────────────────────


async def test_patch_my_org_renames(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Owner can rename the org. Slug stays as-is."""
    r = await admin_authed.patch(
        "/api/orgs/me", json={"name": "Axiolo Inc"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Axiolo Inc"
    assert r.json()["slug"] == "axiolo"  # unchanged


async def test_patch_my_org_requires_owner(
    client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
) -> None:
    """A member-role caller gets 403."""
    from pulse_api.auth.password import hash_password
    from pulse_api.auth.session import encode_session

    user_id = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, last_active_org_id, "
                " email_verified_at) "
                "values (:e, :h, :n, cast(:o as uuid), now()) "
                "returning id::text"
            ),
            {
                "e": "member@example.com",
                "h": hash_password("secret-pass-12345"),
                "n": "Member",
                "o": axiolo_org["id"],
            },
        )
    ).mappings().one()["id"]
    await _add_membership(
        db, user_id=user_id, org_id=axiolo_org["id"], role="member"
    )
    await db.flush()

    client.cookies.set(
        settings.session_cookie_name,
        encode_session(user_id, axiolo_org["id"]),
    )
    r = await client.patch("/api/orgs/me", json={"name": "Hax"})
    assert r.status_code == 403


# ── GET /api/me/orgs ──────────────────────────────────────────────────────


async def test_list_my_orgs_returns_all_memberships(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """The user is in Axiolo + a second org → both show up, sorted by name."""
    other_id = await _insert_org(db, name="Acme")
    await _add_membership(
        db, user_id=seed_admin_user["id"], org_id=other_id, role="owner"
    )
    await db.flush()

    r = await admin_authed.get("/api/me/orgs")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 2
    # Deterministic order: alphabetical by name → Acme, Axiolo.
    assert rows[0]["name"] == "Acme"
    assert rows[1]["name"] == "Axiolo"
    assert {r["role"] for r in rows} == {"owner"}


async def test_list_my_orgs_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/api/me/orgs")
    assert r.status_code == 401


# ── POST /api/me/switch-org ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "target_kind, expected_status",
    [
        ("valid_membership", 200),
        ("foreign_org", 403),
        ("malformed_uuid", 422),
        ("nonexistent_uuid", 403),
    ],
    ids=[
        "switch-to-member-org",
        "switch-to-non-member-org-403",
        "switch-to-malformed-uuid-422",
        "switch-to-unknown-uuid-403",
    ],
)
async def test_switch_org_matrix(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    target_kind: str,
    expected_status: int,
) -> None:
    """``POST /api/me/switch-org`` covers the 4 outcome shapes."""
    # Always seed a second org so the test can ask about both branches.
    other_id = await _insert_org(db, name="Acme")
    if target_kind == "valid_membership":
        await _add_membership(
            db, user_id=seed_admin_user["id"], org_id=other_id, role="member"
        )
        target = other_id
    elif target_kind == "foreign_org":
        # No membership added → 403.
        target = other_id
    elif target_kind == "malformed_uuid":
        target = "not-a-uuid"
    elif target_kind == "nonexistent_uuid":
        target = str(uuid.uuid4())
    else:  # pragma: no cover
        raise AssertionError(f"unknown target_kind {target_kind}")
    await db.flush()

    r = await admin_authed.post("/api/me/switch-org", json={"org_id": target})
    assert r.status_code == expected_status, r.text

    if expected_status == 200:
        # The cookie was re-issued; decode it and confirm the new
        # active_org_id sticks.
        cookie = r.cookies.get(settings.session_cookie_name)
        assert cookie is not None
        payload = decode_session_payload(
            cookie, settings.session_max_age_seconds
        )
        assert payload["active_org_id"] == str(target)
        # And the response body carries the new active org's metadata.
        assert r.json()["id"] == str(target)
        assert r.json()["role"] == "member"


async def test_switch_org_persists_last_active(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A successful switch updates ``users.last_active_org_id``."""
    other_id = await _insert_org(db, name="Acme")
    await _add_membership(
        db, user_id=seed_admin_user["id"], org_id=other_id, role="owner"
    )
    await db.flush()

    r = await admin_authed.post(
        "/api/me/switch-org", json={"org_id": other_id}
    )
    assert r.status_code == 200, r.text

    persisted = (
        await db.execute(
            text(
                "select last_active_org_id::text from public.users "
                "where id = cast(:u as uuid)"
            ),
            {"u": seed_admin_user["id"]},
        )
    ).scalar()
    assert persisted == other_id
