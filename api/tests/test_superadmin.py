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
            "with c as ("
            "  insert into public.clients (org_id, name) "
            "  values (cast(:o as uuid), :n) "
            "  on conflict (org_id, name) do update set name = excluded.name "
            "  returning id"
            ") "
            "insert into public.engagements (client_id, org_id) "
            "select c.id, cast(:o as uuid) from c"
        ),
        {"n": "Stuck Client", "o": target},
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


# ── Reactive cards: org allow-flag + usage/cost report (Phase 4) ──────────


async def test_patch_org_reactive_flag_flips_and_audits(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Flipping the flag persists, returns the refreshed row, writes an
    `org.update` audit row with old/new values, and round-trips through
    both the org-details endpoint and the superadmin listing."""
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()
    org_id = seed_admin_user["org_id"]

    r = await admin_authed.patch(
        f"/api/superadmin/orgs/{org_id}",
        json={"reactive_cards_allowed": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == org_id
    assert body["reactive_cards_allowed"] is True

    persisted = (
        await db.execute(
            text(
                "select reactive_cards_allowed from public.organizations "
                "where id = cast(:o as uuid)"
            ),
            {"o": org_id},
        )
    ).scalar_one()
    assert persisted is True

    audit_row = (
        await db.execute(
            text(
                "select metadata from public.audit_logs "
                "where org_id = cast(:o as uuid) and action = 'org.update' "
                "order by created_at desc limit 1"
            ),
            {"o": org_id},
        )
    ).mappings().one()
    md = audit_row["metadata"] or {}
    assert md.get("changed_fields") == ["reactive_cards_allowed"]
    assert md.get("old_reactive_cards_allowed") is False
    assert md.get("new_reactive_cards_allowed") is True

    # Flipping back off round-trips too, including in the org listing the
    # superadmin UI's per-org toggle reads its `checked` state from.
    r2 = await admin_authed.patch(
        f"/api/superadmin/orgs/{org_id}",
        json={"reactive_cards_allowed": False},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["reactive_cards_allowed"] is False

    listing = await admin_authed.get("/api/superadmin/orgs?limit=100")
    assert listing.status_code == 200, listing.text
    row = next(o for o in listing.json() if o["id"] == org_id)
    assert row["reactive_cards_allowed"] is False


async def test_patch_org_flags_non_superadmin_403(
    admin_authed: AsyncClient,
    seed_admin_user: dict[str, str],
) -> None:
    """`seed_admin_user` is an owner of Axiolo but NOT a superadmin."""
    r = await admin_authed.patch(
        f"/api/superadmin/orgs/{seed_admin_user['org_id']}",
        json={"reactive_cards_allowed": True},
    )
    assert r.status_code == 403, r.text


async def test_patch_org_flags_unknown_id_returns_404(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Unknown UUID and malformed UUID both 404 (same shape)."""
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()

    r1 = await admin_authed.patch(
        f"/api/superadmin/orgs/{uuid.uuid4()}",
        json={"reactive_cards_allowed": True},
    )
    assert r1.status_code == 404, r1.text

    r2 = await admin_authed.patch(
        "/api/superadmin/orgs/not-a-uuid",
        json={"reactive_cards_allowed": True},
    )
    assert r2.status_code == 404, r2.text


async def _seed_engagement_with_card(
    db: AsyncSession, *, org_id: str, name: str
) -> dict[str, str]:
    """Minimal engagement + one recipient + one card + one answered
    response — just enough real, FK-linked rows for a `card_generations`
    row (org -> engagement -> recipient/response/card) to reference."""
    client = (
        await db.execute(
            text(
                "insert into public.clients (org_id, name) "
                "values (cast(:o as uuid), :n) "
                "on conflict (org_id, name) do update set name = excluded.name "
                "returning id::text"
            ),
            {"o": org_id, "n": name},
        )
    ).mappings().one()
    eng = (
        await db.execute(
            text(
                "insert into public.engagements (client_id, org_id) "
                "values (cast(:c as uuid), cast(:o as uuid)) returning id::text"
            ),
            {"c": client["id"], "o": org_id},
        )
    ).mappings().one()
    recipient = (
        await db.execute(
            text(
                "insert into public.recipients (engagement_id, org_id, token) "
                "values (cast(:e as uuid), cast(:o as uuid), :t) returning id::text"
            ),
            {"e": eng["id"], "o": org_id, "t": secrets.token_hex(8)},
        )
    ).mappings().one()
    card = (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, "
                " question, response_type, org_id) "
                "values (cast(:e as uuid), 1, 'C', 'T', 'ctx', 'q?', "
                "'confirm-edit', cast(:o as uuid)) returning id::text"
            ),
            {"e": eng["id"], "o": org_id},
        )
    ).mappings().one()
    response = (
        await db.execute(
            text(
                "insert into public.responses "
                "(card_id, engagement_id, recipient_id, org_id, state, "
                " response_value) "
                "values (cast(:c as uuid), cast(:e as uuid), cast(:r as uuid), "
                "cast(:o as uuid), 'answered', '{}'::jsonb) returning id::text"
            ),
            {"c": card["id"], "e": eng["id"], "r": recipient["id"], "o": org_id},
        )
    ).mappings().one()
    return {
        "org_id": org_id,
        "engagement_id": eng["id"],
        "recipient_id": recipient["id"],
        "card_id": card["id"],
        "response_id": response["id"],
    }


async def _seed_generation(
    db: AsyncSession,
    *,
    ctx: dict[str, str],
    status: str,
    trigger_hash: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: str | None = None,
    days_ago: int = 0,
) -> None:
    """Insert one `card_generations` row directly, bypassing the
    generation engine entirely — the usage-report tests only need the
    ledger rows to exist with the right status/tokens/cost/age, not a
    real (mocked) generation run."""
    await db.execute(
        text(
            """
            insert into public.card_generations
              (org_id, engagement_id, recipient_id, response_id, card_id,
               trigger_hash, status, input_tokens, output_tokens, cost_usd,
               created_at)
            values
              (cast(:org as uuid), cast(:eng as uuid), cast(:rid as uuid),
               cast(:resp as uuid), cast(:card as uuid), :hash, :status,
               :itok, :otok, cast(:cost as numeric),
               now() - make_interval(days => :days_ago))
            """
        ),
        {
            "org": ctx["org_id"],
            "eng": ctx["engagement_id"],
            "rid": ctx["recipient_id"],
            "resp": ctx["response_id"],
            "card": ctx["card_id"],
            "hash": trigger_hash,
            "status": status,
            "itok": input_tokens,
            "otok": output_tokens,
            "cost": cost_usd,
            "days_ago": days_ago,
        },
    )


async def test_reactive_usage_returns_per_org_sums_and_totals(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Two seeded orgs, each with mixed-status `card_generations` rows —
    the per-org sums and the all-orgs totals row must both be correct."""
    await _become_superadmin(db, seed_admin_user["id"])
    org_a = seed_admin_user["org_id"]
    org_b = await _make_empty_org(db)

    ctx_a = await _seed_engagement_with_card(db, org_id=org_a, name="UsageOrgA")
    ctx_b = await _seed_engagement_with_card(db, org_id=org_b, name="UsageOrgB")

    await _seed_generation(
        db, ctx=ctx_a, status="completed", trigger_hash="a1",
        input_tokens=100, output_tokens=50, cost_usd="0.010000",
    )
    await _seed_generation(
        db, ctx=ctx_a, status="skipped", trigger_hash="a2",
        input_tokens=20, output_tokens=5,
    )
    await _seed_generation(db, ctx=ctx_a, status="failed", trigger_hash="a3")
    await _seed_generation(
        db, ctx=ctx_b, status="completed", trigger_hash="b1",
        input_tokens=200, output_tokens=100, cost_usd="0.020000",
    )
    await db.flush()

    r = await admin_authed.get("/api/superadmin/reactive-usage?days=30")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days"] == 30

    by_org = {row["org_id"]: row for row in body["orgs"]}

    row_a = by_org[org_a]
    assert row_a["generations"] == 3
    assert row_a["completed"] == 1
    assert row_a["skipped"] == 1
    assert row_a["failed"] == 1
    assert row_a["input_tokens"] == 120
    assert row_a["output_tokens"] == 55
    assert row_a["cost_usd"] == pytest.approx(0.01)

    row_b = by_org[org_b]
    assert row_b["generations"] == 1
    assert row_b["completed"] == 1
    assert row_b["skipped"] == 0
    assert row_b["failed"] == 0
    assert row_b["input_tokens"] == 200
    assert row_b["output_tokens"] == 100
    assert row_b["cost_usd"] == pytest.approx(0.02)

    totals = body["totals"]
    assert totals["generations"] == 4
    assert totals["completed"] == 2
    assert totals["skipped"] == 1
    assert totals["failed"] == 1
    assert totals["input_tokens"] == 320
    assert totals["output_tokens"] == 155
    assert totals["cost_usd"] == pytest.approx(0.03)


async def test_reactive_usage_days_window_filters_by_created_at(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A generation older than the requested window is excluded; widening
    the window picks it back up."""
    await _become_superadmin(db, seed_admin_user["id"])
    org_id = seed_admin_user["org_id"]
    ctx = await _seed_engagement_with_card(db, org_id=org_id, name="Windowed")

    await _seed_generation(
        db, ctx=ctx, status="completed", trigger_hash="recent",
        input_tokens=10, output_tokens=5, cost_usd="0.001000", days_ago=1,
    )
    await _seed_generation(
        db, ctx=ctx, status="completed", trigger_hash="old",
        input_tokens=999, output_tokens=999, cost_usd="9.990000", days_ago=40,
    )
    await db.flush()

    r30 = await admin_authed.get("/api/superadmin/reactive-usage?days=30")
    assert r30.status_code == 200, r30.text
    row30 = next(o for o in r30.json()["orgs"] if o["org_id"] == org_id)
    assert row30["generations"] == 1
    assert row30["input_tokens"] == 10

    r90 = await admin_authed.get("/api/superadmin/reactive-usage?days=90")
    assert r90.status_code == 200, r90.text
    row90 = next(o for o in r90.json()["orgs"] if o["org_id"] == org_id)
    assert row90["generations"] == 2
    assert row90["input_tokens"] == 1009


@pytest.mark.parametrize(
    "raw_days, expected_days",
    [(0, 1), (-5, 1), (1000, 365), (365, 365), (30, 30)],
    ids=["zero", "negative", "over-max", "at-max", "default-ish"],
)
async def test_reactive_usage_days_clamped_to_bounds(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    raw_days: int,
    expected_days: int,
) -> None:
    await _become_superadmin(db, seed_admin_user["id"])
    await db.flush()

    r = await admin_authed.get(f"/api/superadmin/reactive-usage?days={raw_days}")
    assert r.status_code == 200, r.text
    assert r.json()["days"] == expected_days


async def test_reactive_usage_non_superadmin_403(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.get("/api/superadmin/reactive-usage")
    assert r.status_code == 403, r.text
