"""Tests for the per-org branding/theme backend.

Surface under test:

* ``PATCH /api/orgs/me/branding`` — owner-gated; validates colors +
  font; empty body resets to SQL NULL; writes an ``org.branding`` audit
  row atomically; returns the refreshed :class:`OrgDetails`.
* ``GET /api/orgs/me`` — ``OrgDetails.branding`` round-trips what was
  saved.

Mirrors the style of ``test_orgs_routes.py`` / ``test_org_logo_upload.py``:
httpx + ASGITransport, transaction-rollback per test, the seeded admin
owner fixture for the happy path and an ad-hoc member-role caller for the
owner-gate test.
"""
from __future__ import annotations

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.audit import AUDIT_ACTIONS
from pulse_api.auth.password import hash_password
from pulse_api.auth.session import encode_session
from pulse_api.config import settings


# ── Helpers ───────────────────────────────────────────────────────────────


async def _seed_member_caller(
    db: AsyncSession, *, org_id: str, role: str = "member"
) -> str:
    """Insert a verified user with a ``role`` membership in ``org_id``.

    Returns the user id. Mirrors the ad-hoc member construction in
    ``test_orgs_routes.test_patch_my_org_requires_owner`` /
    ``test_org_logo_upload._seed_member``.
    """
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
            "values (cast(:o as uuid), cast(:u as uuid), :r)"
        ),
        {"o": org_id, "u": user_id, "r": role},
    )
    return user_id


async def _fetch_branding_column(db: AsyncSession, *, org_id: str) -> object:
    """Return the raw ``branding`` JSONB column for ``org_id`` (dict|None)."""
    return (
        await db.execute(
            text(
                "select branding from public.organizations "
                "where id = cast(:o as uuid)"
            ),
            {"o": org_id},
        )
    ).scalar_one()


# ── Happy path: update + round-trip ───────────────────────────────────────


async def test_update_branding_persists_and_round_trips(
    admin_authed: AsyncClient,
    seed_admin_user: dict[str, str],
) -> None:
    """Owner PATCHes valid colors + font → 200, body matches; then
    ``GET /api/orgs/me`` returns the same branding object."""
    payload = {
        "brand_color": "#2960F6",
        "background_color": "#FFFFFF",
        "text_color": "#0a0a0a",
        "font": "inter",
    }
    r = await admin_authed.patch("/api/orgs/me/branding", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["branding"] == payload

    # Round-trip through the read endpoint.
    r2 = await admin_authed.get("/api/orgs/me")
    assert r2.status_code == 200, r2.text
    assert r2.json()["branding"] == payload


async def test_rename_org_preserves_branding_in_response(
    admin_authed: AsyncClient,
    seed_admin_user: dict[str, str],
) -> None:
    """Renaming via ``PATCH /api/orgs/me`` must not blank out branding.

    Regression: ``update_name``'s RETURNING clause omitted ``branding``,
    so a rename echoed ``branding: null`` even when branding was set,
    which the settings UI would then mirror into its local state.
    """
    branding = {"brand_color": "#2960F6", "font": "inter"}
    r = await admin_authed.patch("/api/orgs/me/branding", json=branding)
    assert r.status_code == 200, r.text

    r2 = await admin_authed.patch(
        "/api/orgs/me", json={"name": "Renamed Org"}
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["name"] == "Renamed Org"
    # Branding survives the rename (response is null-padded to all keys).
    assert body["branding"] == {
        "brand_color": "#2960F6",
        "background_color": None,
        "text_color": None,
        "font": "inter",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"brand_color": "#abcdef"},
        {"background_color": "#000000"},
        {"text_color": "#FFFFFF"},
        {"font": "plus-jakarta-sans"},
        {"font": "lora", "brand_color": "#123abc"},
    ],
    ids=[
        "brand-color-only",
        "background-color-only",
        "text-color-only",
        "font-only",
        "font-plus-color",
    ],
)
async def test_update_branding_partial_fields(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    payload: dict[str, str],
) -> None:
    """Each individual field (and a combo) persists.

    The response model (:class:`OrgDetails.branding`) always carries all
    four keys, padding unset ones with ``null``; the stored JSONB column
    keeps only the non-null keys. Assert both shapes.
    """
    r = await admin_authed.patch("/api/orgs/me/branding", json=payload)
    assert r.status_code == 200, r.text
    branding = r.json()["branding"]
    # Supplied keys echo back; the rest are null-padded.
    all_keys = {"brand_color", "background_color", "text_color", "font"}
    for key in all_keys:
        assert branding[key] == payload.get(key)

    # Stored column is compact — only the supplied keys.
    stored = await _fetch_branding_column(
        db, org_id=seed_admin_user["org_id"]
    )
    assert stored == payload


@pytest.mark.parametrize("font", sorted(
    {
        "plus-jakarta-sans",
        "inter",
        "roboto",
        "lora",
        "source-serif",
        "system-ui",
    }
))
async def test_update_branding_accepts_every_allowed_font(
    admin_authed: AsyncClient,
    font: str,
) -> None:
    """Every slug in ALLOWED_FONTS is accepted."""
    r = await admin_authed.patch(
        "/api/orgs/me/branding", json={"font": font}
    )
    assert r.status_code == 200, r.text
    assert r.json()["branding"]["font"] == font


# ── Validation: malformed colors + bad font slug → 422 ────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"brand_color": "blue"},
        {"brand_color": "#12"},
        {"brand_color": "#1234ZZ"},
        {"brand_color": "2960F6"},          # missing leading '#'
        {"brand_color": "#2960F6F"},        # 7 hex digits
        {"background_color": "rgb(0,0,0)"},
        {"text_color": "#xyzxyz"},
        {"font": "comic-sans"},
        {"font": "Inter"},                  # case-sensitive slug
    ],
    ids=[
        "color-word",
        "color-too-short",
        "color-non-hex",
        "color-missing-hash",
        "color-too-long",
        "color-rgb-form",
        "color-non-hex-text",
        "font-unknown-slug",
        "font-wrong-case",
    ],
)
async def test_update_branding_rejects_invalid_input(
    admin_authed: AsyncClient,
    payload: dict[str, str],
) -> None:
    """Malformed hex or an unknown font slug → 422 and nothing persists."""
    r = await admin_authed.patch("/api/orgs/me/branding", json=payload)
    assert r.status_code == 422, r.text


async def test_invalid_branding_does_not_persist(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A rejected PATCH leaves the column untouched (still NULL)."""
    r = await admin_authed.patch(
        "/api/orgs/me/branding", json={"brand_color": "nope"}
    )
    assert r.status_code == 422

    stored = await _fetch_branding_column(
        db, org_id=seed_admin_user["org_id"]
    )
    assert stored is None


# ── Reset: empty body clears the column ───────────────────────────────────


async def test_update_branding_empty_body_resets_to_null(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """PATCH valid branding, then PATCH ``{}`` → column is SQL NULL again
    and the response omits the branding object."""
    set_r = await admin_authed.patch(
        "/api/orgs/me/branding",
        json={"brand_color": "#2960F6", "font": "roboto"},
    )
    assert set_r.status_code == 200, set_r.text
    assert set_r.json()["branding"] is not None

    reset_r = await admin_authed.patch("/api/orgs/me/branding", json={})
    assert reset_r.status_code == 200, reset_r.text
    assert reset_r.json()["branding"] is None

    stored = await _fetch_branding_column(
        db, org_id=seed_admin_user["org_id"]
    )
    assert stored is None

    # And the read endpoint agrees.
    get_r = await admin_authed.get("/api/orgs/me")
    assert get_r.json()["branding"] is None


# ── Owner gate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "role, expected_status",
    [("owner", 200), ("member", 403)],
    ids=["owner-allowed", "member-forbidden"],
)
async def test_update_branding_owner_gate(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    axiolo_org: dict[str, str],
    role: str,
    expected_status: int,
) -> None:
    """``require_owner`` lets owners through, rejects members with 403."""
    if role == "owner":
        user_id = seed_admin_user["id"]
    else:
        user_id = await _seed_member_caller(
            db, org_id=axiolo_org["id"], role="member"
        )
        await db.flush()

    client.cookies.set(
        settings.session_cookie_name,
        encode_session(user_id, axiolo_org["id"]),
    )
    r = await client.patch(
        "/api/orgs/me/branding", json={"brand_color": "#2960F6"}
    )
    assert r.status_code == expected_status, r.text


async def test_update_branding_requires_auth(client: AsyncClient) -> None:
    """No session cookie → 401."""
    r = await client.patch(
        "/api/orgs/me/branding", json={"brand_color": "#2960F6"}
    )
    assert r.status_code == 401


# ── Audit ─────────────────────────────────────────────────────────────────


def test_org_branding_is_a_known_audit_action() -> None:
    """The action string the route emits is in the canonical enum."""
    assert "org.branding" in AUDIT_ACTIONS


async def test_update_branding_writes_audit_row(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A successful PATCH writes an ``org.branding`` audit row for the
    org, carrying old/new metadata and attributed to the operator."""
    new_branding = {"brand_color": "#2960F6", "font": "inter"}
    r = await admin_authed.patch("/api/orgs/me/branding", json=new_branding)
    assert r.status_code == 200, r.text

    rows = (
        await db.execute(
            text(
                "select user_id::text as user_id, action, target_type, "
                "       target_id, metadata "
                "from public.audit_logs "
                "where org_id = cast(:o as uuid) and action = 'org.branding'"
            ),
            {"o": seed_admin_user["org_id"]},
        )
    ).mappings().all()
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "org.branding"
    assert row["target_type"] == "org"
    assert row["target_id"] == seed_admin_user["org_id"]
    assert row["user_id"] == seed_admin_user["id"]
    # metadata.old was NULL before; metadata.new is what we just sent.
    assert row["metadata"]["old"] is None
    assert row["metadata"]["new"] == new_branding


async def test_reset_branding_audit_metadata_carries_old_value(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Resetting records the prior branding as ``old`` and ``None`` as new."""
    first = {"brand_color": "#2960F6", "font": "lora"}
    await admin_authed.patch("/api/orgs/me/branding", json=first)
    await admin_authed.patch("/api/orgs/me/branding", json={})

    metas = [
        r["metadata"]
        for r in (
            await db.execute(
                text(
                    "select metadata from public.audit_logs "
                    "where org_id = cast(:o as uuid) "
                    "and action = 'org.branding'"
                ),
                {"o": seed_admin_user["org_id"]},
            )
        ).mappings().all()
    ]
    assert len(metas) == 2
    # Both audit inserts share a transaction timestamp, so we identify the
    # set vs reset rows by content rather than by ordering.
    set_row = next(m for m in metas if m["old"] is None)
    reset_row = next(m for m in metas if m["new"] is None)
    assert set_row["new"] == first
    assert reset_row["old"] == first
