"""Tests for ``/api/superadmin/*``.

The auth matrix is the bulk of the surface — for each of the four
routes we check superadmin/owner/member/unauthenticated and lock the
expected status. Org-creation, listing, and delete each get a
parametrized happy-path + failure-path block on top of that.

All tests share two helpers (:func:`_become_superadmin`,
:func:`_seed_member_in_org`) that promote the seeded admin to
superadmin or add a non-superadmin member to an org.
"""
from __future__ import annotations

import re
import secrets
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.password import hash_password
from pulse_api.auth.session import encode_session
from pulse_api.config import settings
from pulse_api.email import OutboundEmail


# ── Helpers ───────────────────────────────────────────────────────────────


async def _become_superadmin(db: AsyncSession, user_id: str) -> None:
    """Flip ``users.is_superadmin`` to true for ``user_id``."""
    await db.execute(
        text(
            "update public.users set is_superadmin = true "
            "where id = cast(:u as uuid)"
        ),
        {"u": user_id},
    )
    await db.flush()


async def _seed_member_in_org(
    db: AsyncSession,
    *,
    org_id: str,
    role: str = "member",
    email_prefix: str = "member",
) -> dict[str, str]:
    """Insert a verified user with a membership row in ``org_id``.

    Returns ``{id, email, role, password}``. The user is NOT a
    superadmin — pass to :func:`_become_superadmin` afterwards if the
    test needs that.
    """
    pw = "correct-horse-battery-staple"
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
                "e": f"{email_prefix}-{secrets.token_hex(3)}@example.com",
                "h": hash_password(pw),
                "n": email_prefix.capitalize(),
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
    await db.flush()
    email_row = (
        await db.execute(
            text("select email from public.users where id = cast(:u as uuid)"),
            {"u": user_id},
        )
    ).scalar_one()
    return {"id": user_id, "email": str(email_row), "role": role, "password": pw}


def _set_cookie(client: AsyncClient, *, user_id: str, org_id: str) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        encode_session(user_id, org_id),
    )


# ── Auth matrix ───────────────────────────────────────────────────────────

# Each route is exercised four times: superadmin, owner-not-super,
# member-not-super, unauthenticated. The same parametrized function
# walks every (route, method, body, expected_per_actor) tuple — keeps
# the auth coverage in one place and adds another route by adding one
# entry rather than four test functions.
_AUTH_ROUTES: list[tuple[str, str, str, dict[str, int]]] = [
    (
        "GET",
        "/api/superadmin/orgs",
        "list",
        {"superadmin": 200, "owner": 403, "member": 403, "anon": 401},
    ),
    (
        "POST",
        "/api/superadmin/orgs",
        "create",
        {
            # 201 is the success status; we only check ≥ 400 categories
            # for the non-super actors. Slug uniqueness uses a per-test
            # random suffix so each call to this row stays clean.
            "superadmin": 201,
            "owner": 403,
            "member": 403,
            "anon": 401,
        },
    ),
    (
        "DELETE",
        "/api/superadmin/orgs/{org_id}",
        "delete",
        {
            # Delete uses a fresh empty org per call; 204 on success.
            "superadmin": 204,
            "owner": 403,
            "member": 403,
            "anon": 401,
        },
    ),
    (
        "GET",
        "/api/superadmin/orgs/{org_id}/members",
        "members",
        {"superadmin": 200, "owner": 403, "member": 403, "anon": 401},
    ),
]


async def _make_empty_org(db: AsyncSession) -> str:
    """Insert a fresh org with NO memberships, NO clients. Returns its id."""
    return (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values (:n, :s) returning id::text"
            ),
            {
                "n": f"DelTarget-{secrets.token_hex(2)}",
                "s": f"deltarget-{secrets.token_hex(3)}",
            },
        )
    ).mappings().one()["id"]


@pytest.mark.parametrize("actor", ["superadmin", "owner", "member", "anon"])
@pytest.mark.parametrize(
    "method, route_template, route_name, status_per_actor",
    _AUTH_ROUTES,
    ids=["list", "create", "delete", "members"],
)
async def test_superadmin_auth_matrix(
    client: AsyncClient,
    db: AsyncSession,
    captured_emails: list[OutboundEmail],
    seed_admin_user: dict[str, str],
    actor: str,
    method: str,
    route_template: str,
    route_name: str,
    status_per_actor: dict[str, int],
) -> None:
    """Each route refuses owner/member/anon and accepts superadmin."""
    # ── Pick the calling user + cookie based on the actor role ──
    if actor == "superadmin":
        await _become_superadmin(db, seed_admin_user["id"])
        _set_cookie(
            client,
            user_id=seed_admin_user["id"],
            org_id=seed_admin_user["org_id"],
        )
    elif actor == "owner":
        # seed_admin_user is already an owner of Axiolo; just use them
        # without flipping the superadmin bit.
        _set_cookie(
            client,
            user_id=seed_admin_user["id"],
            org_id=seed_admin_user["org_id"],
        )
    elif actor == "member":
        member = await _seed_member_in_org(
            db, org_id=seed_admin_user["org_id"], role="member"
        )
        _set_cookie(
            client,
            user_id=member["id"],
            org_id=seed_admin_user["org_id"],
        )
    elif actor == "anon":
        client.cookies.clear()
    else:  # pragma: no cover
        raise AssertionError(f"unknown actor {actor!r}")

    # ── Build the URL (resolve {org_id} placeholder) + body ──
    org_id_for_url: str | None = None
    if "{org_id}" in route_template:
        if route_name == "delete" and actor == "superadmin":
            org_id_for_url = await _make_empty_org(db)
        else:
            # For "members" / non-super delete, point at Axiolo so the
            # superadmin case still has a valid org to read.
            org_id_for_url = seed_admin_user["org_id"]
    url = route_template.format(org_id=org_id_for_url or "")
    await db.flush()

    json_body: dict[str, Any] | None = None
    if method == "POST":
        # Use unique slug per call so the success path doesn't collide.
        json_body = {
            "name": f"Acme {secrets.token_hex(2)}",
            "slug": f"acme-{secrets.token_hex(3)}",
            "owner_email": f"founder-{secrets.token_hex(3)}@acme.example",
        }

    # ── Issue + assert ──
    r = await client.request(method, url, json=json_body)
    assert r.status_code == status_per_actor[actor], (
        f"{actor} on {method} {url} → {r.status_code} (body={r.text})"
    )

    # Side-effect assertions for the success rows so they're not just
    # status checks: create should have sent an email, delete should
    # have removed the row.
    if actor == "superadmin":
        if route_name == "create":
            assert len(captured_emails) == 1
            assert captured_emails[0].to == json_body["owner_email"]
        elif route_name == "delete":
            still = (
                await db.execute(
                    text(
                        "select 1 from public.organizations "
                        "where id = cast(:o as uuid)"
                    ),
                    {"o": org_id_for_url},
                )
            ).scalar()
            assert still is None


# ── Org creation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name, slug, owner_email, expected_status, expected_detail_substr",
    [
        # Valid happy path.
        (
            "Acme Inc",
            "acme",
            "jane@acme.example",
            201,
            None,
        ),
        # Slug with uppercase — rejected at the route validator.
        ("Acme", "Acme", "j@acme.example", 422, "slug"),
        # Slug with special character.
        ("Acme", "acme!", "j@acme.example", 422, "slug"),
        # Empty slug → falls below min length (pydantic).
        ("Acme", "", "j@acme.example", 422, None),
        # Slug too long (41 chars).
        (
            "Acme",
            "a" * 41,
            "j@acme.example",
            422,
            None,
        ),
        # Malformed owner email.
        ("Acme", "acme", "not-an-email", 422, None),
        # Slug with leading hyphen.
        ("Acme", "-acme", "j@acme.example", 422, "slug"),
    ],
    ids=[
        "happy-path",
        "uppercase-slug",
        "special-char-slug",
        "empty-slug",
        "slug-too-long",
        "bad-email",
        "leading-hyphen-slug",
    ],
)
async def test_create_org_validation_matrix(
    admin_authed: AsyncClient,
    db: AsyncSession,
    captured_emails: list[OutboundEmail],
    seed_admin_user: dict[str, str],
    name: str,
    slug: str,
    owner_email: str,
    expected_status: int,
    expected_detail_substr: str | None,
) -> None:
    """Slug/email validation, plus the happy path's invite-email assertions."""
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()

    r = await admin_authed.post(
        "/api/superadmin/orgs",
        json={"name": name, "slug": slug, "owner_email": owner_email},
    )
    assert r.status_code == expected_status, r.text
    if expected_detail_substr is not None and expected_status >= 400:
        detail = r.json().get("detail", "")
        if isinstance(detail, str):
            assert expected_detail_substr in detail.lower()

    if expected_status == 201:
        body = r.json()
        assert body["org"]["slug"] == slug
        assert body["org"]["name"] == name
        # Invite block carries the recipient but not the raw token.
        assert body["invite"]["email"] == owner_email.lower()
        flat = str(body)
        assert "token" not in body["invite"]
        assert not re.search(r"pulse-org-invite|signed-token", flat)

        # DB-side: the org + invite rows exist.
        row = (
            await db.execute(
                text(
                    "select id::text from public.organizations where slug = :s"
                ),
                {"s": slug},
            )
        ).scalar()
        assert row is not None
        invite_row = (
            await db.execute(
                text(
                    "select email from public.organization_invites "
                    "where org_id = cast(:o as uuid)"
                ),
                {"o": row},
            )
        ).scalar()
        assert invite_row == owner_email.lower()

        # The email captured has a /invite?token=... link.
        assert len(captured_emails) == 1
        msg = captured_emails[0]
        assert msg.to == owner_email.lower()
        match = re.search(r"/invite\?token=([^\s]+)", msg.body)
        assert match is not None, msg.body


async def test_create_org_duplicate_slug_409(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """The seeded ``axiolo`` slug exists already → second POST → 409."""
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()

    r = await admin_authed.post(
        "/api/superadmin/orgs",
        json={"name": "Axiolo 2", "slug": "axiolo", "owner_email": "x@y.example"},
    )
    assert r.status_code == 409, r.text
    assert "slug" in r.json()["detail"].lower()


# ── Org list ──────────────────────────────────────────────────────────────


async def test_list_orgs_contains_axiolo_with_owner(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Default list returns Axiolo with its seeded owner's email."""
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()

    r = await admin_authed.get("/api/superadmin/orgs")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1
    axiolo = next((r2 for r2 in rows if r2["slug"] == "axiolo"), None)
    assert axiolo is not None
    assert axiolo["member_count"] == 1
    assert axiolo["pending_invite_count"] == 0
    assert seed_admin_user["email"] in axiolo["owner_emails"]


@pytest.mark.parametrize(
    "limit_param, max_expected",
    [
        ("1", 1),
        ("200", 200),  # cap
        ("500", 200),  # over-cap clamps to MAX
    ],
    ids=["limit-1", "limit-200", "limit-over-cap-clamps"],
)
async def test_list_orgs_pagination(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    limit_param: str,
    max_expected: int,
) -> None:
    """``?limit=`` is honored and capped to 200."""
    await _become_superadmin(db, seed_admin_user["id"])

    # Seed a handful of extra orgs so the small-limit case can clip.
    for _ in range(3):
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values (:n, :s)"
            ),
            {
                "n": f"Org {secrets.token_hex(2)}",
                "s": f"org-{secrets.token_hex(3)}",
            },
        )
    await db.flush()

    r = await admin_authed.get(f"/api/superadmin/orgs?limit={limit_param}")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) <= max_expected


# ── Org delete ────────────────────────────────────────────────────────────


async def test_delete_empty_org_succeeds(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """An org with no members and no clients → 204 + row gone."""
    await _become_superadmin(db, seed_admin_user["id"])
    target = await _make_empty_org(db)
    await db.flush()

    r = await admin_authed.delete(f"/api/superadmin/orgs/{target}")
    assert r.status_code == 204, r.text

    still = (
        await db.execute(
            text("select 1 from public.organizations where id = cast(:o as uuid)"),
            {"o": target},
        )
    ).scalar()
    assert still is None


async def test_delete_org_with_clients_returns_409(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Even a single client blocks delete with an informative 409."""
    await _become_superadmin(db, seed_admin_user["id"])
    target = await _make_empty_org(db)
    # Drop a client row in.
    await db.execute(
        text(
            "insert into public.clients (name, token, org_id) "
            "values (:n, :t, cast(:o as uuid))"
        ),
        {"n": "Stuck Client", "t": secrets.token_hex(8), "o": target},
    )
    await db.flush()

    r = await admin_authed.delete(f"/api/superadmin/orgs/{target}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"].lower()
    assert "client" in detail


async def test_delete_org_unknown_returns_404(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Unknown UUID → 404; malformed UUID → also 404 (same shape)."""
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()

    r1 = await admin_authed.delete(f"/api/superadmin/orgs/{uuid.uuid4()}")
    assert r1.status_code == 404, r1.text

    r2 = await admin_authed.delete("/api/superadmin/orgs/not-a-uuid")
    assert r2.status_code == 404, r2.text


async def test_delete_axiolo_with_no_clients_is_allowed(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """The product safety is "no clients", not "not your active org".

    A superadmin who happens to be a member of Axiolo can delete it
    after first removing the membership (1-member rule) and confirming
    no clients exist. This guard sits at the routing layer, so the
    test exercises it by removing the seeded owner first.
    """
    await _become_superadmin(db, seed_admin_user["id"])
    # Drop the only membership so member_count = 0 (well below the "<= 1"
    # gate; the gate refuses ``> 1`` so 0 and 1 both pass).
    await db.execute(
        text(
            "delete from public.organization_memberships "
            "where org_id = cast(:o as uuid)"
        ),
        {"o": seed_admin_user["org_id"]},
    )
    await db.flush()

    # No clients seeded → ok.
    r = await admin_authed.delete(
        f"/api/superadmin/orgs/{seed_admin_user['org_id']}"
    )
    assert r.status_code == 204, r.text


async def test_delete_org_with_multiple_members_returns_409(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """member_count > 1 is the soft guard — refuse with 409."""
    await _become_superadmin(db, seed_admin_user["id"])
    target = await _make_empty_org(db)
    # Seed two non-superadmin members.
    await _seed_member_in_org(db, org_id=target, role="owner", email_prefix="owner")
    await _seed_member_in_org(
        db, org_id=target, role="member", email_prefix="member"
    )
    await db.flush()

    r = await admin_authed.delete(f"/api/superadmin/orgs/{target}")
    assert r.status_code == 409, r.text
    assert "member" in r.json()["detail"].lower()


# ── Org members (support workflow) ────────────────────────────────────────


async def test_list_org_members_returns_seeded_owner(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """The seeded Axiolo owner shows up on the support endpoint."""
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()

    r = await admin_authed.get(
        f"/api/superadmin/orgs/{seed_admin_user['org_id']}/members"
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    emails = {row["email"] for row in rows}
    assert seed_admin_user["email"] in emails
    # Role for the seed user is owner.
    seeded_row = next(
        (r for r in rows if r["email"] == seed_admin_user["email"]), None
    )
    assert seeded_row is not None
    assert seeded_row["role"] == "owner"


async def test_list_org_members_unknown_org_returns_404(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Listing members of a non-existent org → 404."""
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()

    r = await admin_authed.get(f"/api/superadmin/orgs/{uuid.uuid4()}/members")
    assert r.status_code == 404, r.text


async def test_create_org_response_omits_raw_token(
    admin_authed: AsyncClient,
    db: AsyncSession,
    captured_emails: list[OutboundEmail],
    seed_admin_user: dict[str, str],
) -> None:
    """The raw signed token is delivered only via email — never in JSON."""
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()

    r = await admin_authed.post(
        "/api/superadmin/orgs",
        json={
            "name": "TokenCheck",
            "slug": f"tokencheck-{secrets.token_hex(3)}",
            "owner_email": "secret-handshake@acme.example",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()

    # Extract the token from the email so we can defensively assert it
    # does not appear anywhere in the JSON response.
    assert len(captured_emails) == 1
    match = re.search(r"/invite\?token=([^\s]+)", captured_emails[0].body)
    assert match is not None
    raw_token = match.group(1)
    serialized = str(body)
    assert raw_token not in serialized
