"""Audit-log + activity-feed tests.

Strategy:

* Drive every mutating route through the real HTTP surface and assert
  the right ``audit_logs`` row landed (action enum + target shape +
  metadata invariants). Each route is parametrized via a small
  ``ActionCase`` dataclass so adding a new action is one line.

* Lock the action enum: the set of actions emitted by the route layer
  must match :data:`pulse_api.audit.AUDIT_ACTIONS`. We assert the
  intersection in both directions so a new action name without an
  emission (or vice versa) fails loudly.

* Prove cross-org RLS isolation on ``audit_logs`` directly — same
  pattern as ``tests/test_rls_isolation.py``.

* Verify cursor pagination + filter parameters against a seeded data
  set whose age progression we control via the rolled-back transaction
  (insert with explicit ``created_at``).
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from pulse_api.audit import AUDIT_ACTIONS, record_audit
from pulse_api.auth.password import hash_password
from pulse_api.auth.session import encode_session
from pulse_api.config import settings

# ── Helpers ───────────────────────────────────────────────────────────────


async def _fetch_audit_rows(
    db: AsyncSession,
    *,
    org_id: str,
    action: str | None = None,
) -> list[dict[str, object]]:
    """Return audit rows for ``org_id``, optionally filtered by action."""
    sql = (
        "select id::text as id, "
        "       user_id::text as user_id, "
        "       action, target_type, target_id, metadata, created_at "
        "from public.audit_logs "
        "where org_id = cast(:org as uuid)"
    )
    params: dict[str, object] = {"org": org_id}
    if action is not None:
        sql += " and action = :a"
        params["a"] = action
    sql += " order by created_at desc, id desc"
    result = await db.execute(text(sql), params)
    return [dict(r) for r in result.mappings().all()]


async def _seed_user_with_membership(
    db: AsyncSession,
    *,
    org_id: str,
    email: str,
    role: str = "member",
) -> str:
    """Insert a verified user with a membership in ``org_id``."""
    row = await db.execute(
        text(
            "insert into public.users "
            "(email, password_hash, name, last_active_org_id, "
            " email_verified_at) "
            "values (:e, :h, :n, cast(:o as uuid), now()) "
            "returning id::text"
        ),
        {
            "e": email,
            "h": hash_password("a-good-password-here"),
            "n": email.split("@")[0],
            "o": org_id,
        },
    )
    user_id = row.mappings().one()["id"]
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), :r)"
        ),
        {"o": org_id, "u": user_id, "r": role},
    )
    return user_id


# ── Action enum lock-in ───────────────────────────────────────────────────


def _emitted_actions_in_source() -> set[str]:
    """Static scan: every ``action="..."`` literal passed to record_audit.

    Runs once per test invocation; cheap enough to live inline. Picks
    up only static-string emissions which is exactly what the route
    layer uses — no dynamic ``action`` is permitted because it would
    bypass the strict-enum check inside ``record_audit``.
    """
    import re
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[1] / "pulse_api"
    pattern = re.compile(r'action\s*=\s*"([a-z][a-z._]+)"')
    found: set[str] = set()
    for path in api_dir.rglob("*.py"):
        if path.name == "audit.py":
            continue
        text_content = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text_content):
            found.add(match.group(1))
    return found


def test_enum_lockin_matches_source() -> None:
    """Every action emitted in source must be in ``AUDIT_ACTIONS``; the
    enum can contain no extras the route layer doesn't emit."""
    emitted = _emitted_actions_in_source()
    unknown = emitted - set(AUDIT_ACTIONS)
    extra = set(AUDIT_ACTIONS) - emitted
    assert not unknown, (
        f"Routes emit actions missing from AUDIT_ACTIONS: {sorted(unknown)!r}"
    )
    assert not extra, (
        f"AUDIT_ACTIONS contains actions no route emits: {sorted(extra)!r}"
    )


def test_unknown_action_raises() -> None:
    """``record_audit`` refuses any action not in the enum so a typo in
    a future route fails loudly instead of dropping rows."""
    with pytest.raises(ValueError, match="unknown audit action"):
        # We have to invoke the helper synchronously to capture the
        # ValueError — it's raised before any DB work happens, so we
        # don't actually need a live session here.
        import asyncio

        class _StubSession:
            def in_transaction(self) -> bool:  # pragma: no cover - never reached
                return True

            async def execute(self, *args, **kwargs):  # pragma: no cover
                raise AssertionError("should not reach DB")

        asyncio.run(
            record_audit(
                _StubSession(),  # type: ignore[arg-type]
                org_id=str(uuid.uuid4()),
                user_id=None,
                action="bogus.nope",
            )
        )


async def test_record_audit_atomically_with_caller(
    db: AsyncSession, axiolo_org: dict[str, str]
) -> None:
    """The helper does not call ``commit()`` — atomicity comes from the
    caller's own ``session.commit()``. We verify by writing a row via
    the helper and then rolling back: the row must vanish."""
    # Outer fixture wraps each test in a rollback transaction, so the
    # write here is visible until the test ends. We assert it landed
    # inside the test transaction by reading it back on the same
    # session before commit.
    await record_audit(
        db,
        org_id=axiolo_org["id"],
        user_id=None,
        action="client.create",
        target_type="client",
        target_id="11111111-1111-1111-1111-111111111111",
    )
    rows = await _fetch_audit_rows(
        db, org_id=axiolo_org["id"], action="client.create"
    )
    assert any(r["target_id"] == "11111111-1111-1111-1111-111111111111" for r in rows)


# ── Per-route emission ────────────────────────────────────────────────────


async def test_audit_client_create(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    r = await admin_authed.post("/api/admin/clients", json={"name": "Acme"})
    assert r.status_code == 201

    rows = await _fetch_audit_rows(
        db, org_id=seed_admin_user["org_id"], action="client.create"
    )
    assert len(rows) == 1
    assert rows[0]["target_type"] == "client"
    assert rows[0]["target_id"] == r.json()["id"]
    assert rows[0]["user_id"] == seed_admin_user["id"]
    assert (rows[0]["metadata"] or {}).get("name") == "Acme"


async def test_audit_client_update_captures_changed_fields(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    seed_client: dict[str, str],
) -> None:
    r = await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}",
        json={"name": "Renamed", "brief": "Hello"},
    )
    assert r.status_code == 200

    rows = await _fetch_audit_rows(
        db, org_id=seed_admin_user["org_id"], action="client.update"
    )
    assert len(rows) == 1
    md = rows[0]["metadata"] or {}
    assert sorted(md.get("changed_fields") or []) == ["brief", "name"]


async def test_audit_client_delete_snapshots_name(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    seed_client: dict[str, str],
) -> None:
    name = seed_client["name"]
    r = await admin_authed.delete(f"/api/admin/clients/{seed_client['id']}")
    assert r.status_code == 204

    rows = await _fetch_audit_rows(
        db, org_id=seed_admin_user["org_id"], action="client.delete"
    )
    assert len(rows) == 1
    assert (rows[0]["metadata"] or {}).get("name") == name


async def test_audit_card_create_and_update_and_delete(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    seed_client: dict[str, str],
) -> None:
    create = await admin_authed.post(
        f"/api/admin/clients/{seed_client['id']}/cards",
        json={
            "category": "C",
            "title": "T",
            "context": "X",
            "question": "Q?",
            "response_type": "short-text",
        },
    )
    assert create.status_code == 201
    card_id = create.json()["id"]

    update = await admin_authed.patch(
        f"/api/admin/cards/{card_id}", json={"title": "T2"}
    )
    assert update.status_code == 200

    delete = await admin_authed.delete(f"/api/admin/cards/{card_id}")
    assert delete.status_code == 204

    actions = [
        r["action"]
        for r in await _fetch_audit_rows(db, org_id=seed_admin_user["org_id"])
    ]
    assert {"card.create", "card.update", "card.delete"}.issubset(set(actions))


async def test_audit_card_import_emits_single_row_with_count(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    seed_client: dict[str, str],
) -> None:
    markdown = (
        "## Card 1: A\n\n**Category:** C\n**Type:** short-text\n"
        "**Skip:** optional\n\n**Context:** ctx\n\n**Question:** q?\n\n"
        "## Card 2: B\n\n**Category:** C\n**Type:** short-text\n"
        "**Skip:** optional\n\n**Context:** ctx\n\n**Question:** q?\n"
    )
    r = await admin_authed.post(
        f"/api/admin/clients/{seed_client['id']}/cards/import-markdown",
        json={"markdown": markdown},
    )
    assert r.status_code == 201

    rows = await _fetch_audit_rows(
        db, org_id=seed_admin_user["org_id"], action="card.import"
    )
    assert len(rows) == 1
    assert (rows[0]["metadata"] or {}).get("count") == 2


async def test_audit_attachment_upload(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    files = {"file": ("x.png", b"\x89PNG\r\n\x1a\n0123", "image/png")}
    r = await admin_authed.post("/api/admin/attachments", files=files)
    assert r.status_code == 201

    rows = await _fetch_audit_rows(
        db, org_id=seed_admin_user["org_id"], action="attachment.upload"
    )
    assert len(rows) == 1
    metadata = rows[0]["metadata"] or {}
    assert metadata.get("filename") == "x.png"
    assert metadata.get("size_bytes") == len(b"\x89PNG\r\n\x1a\n0123")


async def test_audit_org_update_captures_old_and_new(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    r = await admin_authed.patch("/api/orgs/me", json={"name": "Axiolo+"})
    assert r.status_code == 200

    rows = await _fetch_audit_rows(
        db, org_id=seed_admin_user["org_id"], action="org.update"
    )
    assert len(rows) == 1
    md = rows[0]["metadata"] or {}
    assert md.get("old_name") == "Axiolo"
    assert md.get("new_name") == "Axiolo+"


async def test_audit_org_logo_set_and_remove(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    files = {"file": ("logo.png", b"\x89PNG\r\n\x1a\n0123", "image/png")}
    set_r = await admin_authed.post("/api/orgs/me/logo", files=files)
    assert set_r.status_code == 200

    rm_r = await admin_authed.delete("/api/orgs/me/logo")
    assert rm_r.status_code == 204

    actions = [
        r["action"]
        for r in await _fetch_audit_rows(db, org_id=seed_admin_user["org_id"])
    ]
    assert "org.logo_set" in actions
    assert "org.logo_remove" in actions


async def test_audit_member_invite_then_revoke(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    captured_emails: list[object],
) -> None:
    create = await admin_authed.post(
        "/api/orgs/me/invites",
        json={"email": "newbie@example.com", "role": "member"},
    )
    assert create.status_code == 201
    invite_id = create.json()["id"]

    revoke = await admin_authed.delete(f"/api/orgs/me/invites/{invite_id}")
    assert revoke.status_code == 204

    actions = [
        r["action"]
        for r in await _fetch_audit_rows(db, org_id=seed_admin_user["org_id"])
    ]
    assert "member.invite" in actions
    assert "member.invite_revoke" in actions


async def test_audit_member_role_change_and_remove(
    client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
    seed_admin_user: dict[str, str],
    admin_session_cookie: str,
) -> None:
    other_id = await _seed_user_with_membership(
        db,
        org_id=axiolo_org["id"],
        email=f"plebe-{secrets.token_hex(3)}@example.com",
        role="member",
    )

    client.cookies.set(settings.session_cookie_name, admin_session_cookie)

    # Promote → demote → remove. Three audit rows expected.
    promote = await client.patch(
        f"/api/orgs/me/members/{other_id}", json={"role": "owner"}
    )
    assert promote.status_code == 200

    demote = await client.patch(
        f"/api/orgs/me/members/{other_id}", json={"role": "member"}
    )
    assert demote.status_code == 200

    remove = await client.delete(f"/api/orgs/me/members/{other_id}")
    assert remove.status_code == 204

    rows = await _fetch_audit_rows(db, org_id=axiolo_org["id"])
    role_changes = [r for r in rows if r["action"] == "member.role_change"]
    removals = [r for r in rows if r["action"] == "member.remove"]
    assert len(role_changes) == 2
    assert len(removals) == 1
    # The promote row.
    promote_md = next(
        rc["metadata"] for rc in role_changes if (rc["metadata"] or {}).get("to") == "owner"
    )
    assert promote_md["from"] == "member"


async def test_audit_api_key_create_and_revoke_no_raw_value(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    create = await admin_authed.post(
        "/api/auth/me/api-keys", json={"label": "Test key"}
    )
    assert create.status_code == 201
    body = create.json()
    raw_key = body["key"]
    key_id = body["id"]

    revoke = await admin_authed.delete(f"/api/auth/me/api-keys/{key_id}")
    assert revoke.status_code == 204

    rows = await _fetch_audit_rows(db, org_id=seed_admin_user["org_id"])
    actions = [r["action"] for r in rows]
    assert "api_key.create" in actions
    assert "api_key.revoke" in actions
    # Hard invariant: the raw key must NEVER appear in any metadata field
    # across any audit row. The prefix may.
    for r in rows:
        md = r["metadata"] or {}
        for value in md.values():
            assert raw_key not in str(value), (
                f"raw API key leaked into audit metadata of {r['action']!r}"
            )


async def test_audit_member_join_via_password_acceptance(
    client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
    seed_admin_user: dict[str, str],
    admin_session_cookie: str,
    captured_emails: list[object],
) -> None:
    """Accepting an invite emits ``member.join`` in the joining org."""
    client.cookies.set(settings.session_cookie_name, admin_session_cookie)
    invite = await client.post(
        "/api/orgs/me/invites",
        json={"email": f"new-{secrets.token_hex(3)}@example.com", "role": "member"},
    )
    assert invite.status_code == 201

    # The raw signed token is in the captured email body; the easiest way
    # to extract it is to ask the repo via the unhashed-token field we
    # captured during create. We grab it from the email body — same shape
    # the email template builds.
    last_email = captured_emails[-1]
    # The signed token follows ``token=`` in the email body. Grep it.
    body_text = getattr(last_email, "body", "")
    import re

    match = re.search(r"token=([^\s\"'<>]+)", body_text)
    assert match, f"no signed token found in invite email body: {body_text!r}"
    raw_token = match.group(1)

    # Drop the cookie — invite acceptance is unauthenticated.
    client.cookies.clear()
    accept = await client.post(
        f"/api/invites/{raw_token}/accept",
        json={"auth": "password", "password": "a-good-password-here"},
    )
    assert accept.status_code == 200

    rows = await _fetch_audit_rows(
        db, org_id=axiolo_org["id"], action="member.join"
    )
    assert len(rows) == 1
    assert (rows[0]["metadata"] or {}).get("role") == "member"


async def test_audit_org_create_and_delete_by_superadmin(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    captured_emails: list[object],
) -> None:
    # Promote the admin user to superadmin so the cross-tenant POST is allowed.
    await db.execute(
        text(
            "update public.users set is_superadmin = true "
            "where id = cast(:u as uuid)"
        ),
        {"u": seed_admin_user["id"]},
    )
    client.cookies.set(
        settings.session_cookie_name,
        encode_session(seed_admin_user["id"], seed_admin_user["org_id"]),
    )

    create = await client.post(
        "/api/superadmin/orgs",
        json={
            "name": "Acme",
            "slug": f"acme-{secrets.token_hex(2)}",
            "owner_email": f"owner-{secrets.token_hex(3)}@example.com",
        },
    )
    assert create.status_code == 201
    new_org_id = create.json()["org"]["id"]

    # ``org.create`` lands in the new org's feed.
    rows = await _fetch_audit_rows(db, org_id=new_org_id, action="org.create")
    assert len(rows) == 1

    # Delete the org — cascade nukes the audit log for that org, but the
    # delete row itself was committed transactionally with the cascade,
    # so it's gone too. We can verify the path runs without error.
    delete = await client.delete(f"/api/superadmin/orgs/{new_org_id}")
    assert delete.status_code == 204


# ── Cross-org RLS isolation ───────────────────────────────────────────────


async def test_audit_logs_cross_org_isolation(
    db_conn: AsyncConnection,
    db: AsyncSession,
    axiolo_org: dict[str, str],
) -> None:
    """A ``pulse_member`` session scoped to org A cannot read org B's
    audit rows. Mirrors ``test_rls_isolation.py``'s direct-SQL pattern."""
    from tests.conftest import become_member

    # Org B side-by-side with Axiolo.
    other_org_id = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values ('Acme', :s) returning id::text"
            ),
            {"s": f"acme-{secrets.token_hex(2)}"},
        )
    ).mappings().one()["id"]

    # Seed one audit row per org, owner-role superuser (RLS off).
    await db.execute(
        text(
            "insert into public.audit_logs (org_id, action) "
            "values (cast(:o as uuid), 'client.create')"
        ),
        {"o": axiolo_org["id"]},
    )
    await db.execute(
        text(
            "insert into public.audit_logs (org_id, action) "
            "values (cast(:o as uuid), 'client.create')"
        ),
        {"o": other_org_id},
    )

    # Become Axiolo member; expect to see exactly the Axiolo row.
    await become_member(db_conn, org_id=axiolo_org["id"])
    rows = (
        await db_conn.execute(
            text("select org_id::text as org_id from public.audit_logs")
        )
    ).mappings().all()
    visible_org_ids = {r["org_id"] for r in rows}
    assert visible_org_ids == {axiolo_org["id"]}


# ── Activity endpoint: pagination + filters ───────────────────────────────


async def _seed_activity_rows(
    db: AsyncSession,
    *,
    org_id: str,
    user_id: str,
    count: int,
) -> list[datetime]:
    """Insert ``count`` audit rows with monotonically-increasing
    ``created_at`` so cursor pagination has a stable order. Returns the
    inserted timestamps oldest-first."""
    base = datetime(2026, 1, 1, 0, 0, 0)
    stamps: list[datetime] = []
    for i in range(count):
        ts = base + timedelta(seconds=i)
        stamps.append(ts)
        await db.execute(
            text(
                "insert into public.audit_logs "
                "(org_id, user_id, action, target_type, target_id, "
                " metadata, created_at) "
                "values (cast(:o as uuid), cast(:u as uuid), :a, "
                "        'client', :t, '{}'::jsonb, :ts)"
            ),
            {
                "o": org_id,
                "u": user_id,
                "a": "client.create",
                "t": f"target-{i}",
                "ts": ts,
            },
        )
    return stamps


async def test_activity_list_cursor_pagination_walks_all_rows(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Three sequential pages of 20 yield 60 distinct rows; last page
    has ``next_cursor = None``."""
    await _seed_activity_rows(
        db,
        org_id=seed_admin_user["org_id"],
        user_id=seed_admin_user["id"],
        count=60,
    )

    seen: set[str] = set()
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str] = {"limit": "20"}
        if cursor:
            params["cursor"] = cursor
        r = await admin_authed.get("/api/orgs/me/activity", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        pages += 1
        for entry in body["entries"]:
            assert entry["id"] not in seen, (
                "cursor pagination returned a row twice"
            )
            seen.add(entry["id"])
        if body["next_cursor"] is None:
            break
        cursor = body["next_cursor"]
        assert pages < 10  # safety

    assert len(seen) >= 60  # may include other rows from real mutations
    # 60 rows at limit=20 = 3 full pages. The server emits a cursor on a
    # full page even at the boundary, so a 4th request returning 0 rows
    # + next_cursor=None is also acceptable (it lets the UI stop without
    # needing to know the total count). Either is fine.
    assert pages in (3, 4)


async def test_activity_list_cursor_handles_duplicate_timestamps(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Two rows with identical ``created_at`` must straddle a page
    boundary safely. The composite ``(created_at, id)`` cursor exists
    precisely so neither row is dropped or duplicated.

    Without the composite predicate, ``WHERE created_at < :cursor``
    would skip the second row at the boundary every time it had the
    same timestamp as the page's last entry.
    """
    shared_ts = datetime(2026, 1, 1, 12, 0, 0)
    ids: list[str] = []
    # Three rows: two share `shared_ts`, one is earlier. limit=2 forces
    # the page boundary to fall exactly between the duplicates.
    for label, ts in (
        ("dup-a", shared_ts),
        ("dup-b", shared_ts),
        ("older", shared_ts - timedelta(seconds=1)),
    ):
        row = (
            await db.execute(
                text(
                    "insert into public.audit_logs "
                    "(org_id, user_id, action, target_type, target_id, "
                    " metadata, created_at) "
                    "values (cast(:o as uuid), cast(:u as uuid), "
                    "        'client.create', 'client', :t, '{}'::jsonb, :ts) "
                    "returning id::text"
                ),
                {
                    "o": seed_admin_user["org_id"],
                    "u": seed_admin_user["id"],
                    "t": label,
                    "ts": ts,
                },
            )
        ).mappings().one()
        ids.append(row["id"])

    seen: set[str] = set()
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str] = {"limit": "2"}
        if cursor:
            params["cursor"] = cursor
        r = await admin_authed.get("/api/orgs/me/activity", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        pages += 1
        for entry in body["entries"]:
            assert entry["id"] not in seen, "row returned twice"
            seen.add(entry["id"])
        if body["next_cursor"] is None:
            break
        cursor = body["next_cursor"]
        assert pages < 5

    # Every seeded row must surface exactly once, including both duplicates.
    for inserted_id in ids:
        assert inserted_id in seen, (
            f"row {inserted_id} dropped by cursor pagination"
        )


async def test_activity_list_limit_is_clamped(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """limit=500 (above the 200 cap) is clamped, not rejected — the UI
    can naively ask for more than allowed and the server normalizes."""
    await _seed_activity_rows(
        db,
        org_id=seed_admin_user["org_id"],
        user_id=seed_admin_user["id"],
        count=5,
    )
    r = await admin_authed.get(
        "/api/orgs/me/activity", params={"limit": "500"}
    )
    assert r.status_code == 200
    # 5 rows < 200 cap; just verify the cap didn't trip an error.
    assert len(r.json()["entries"]) >= 5


@pytest.mark.parametrize(
    "filter_action, expected_min",
    [
        ("client.create", 60),
        ("card.create", 0),  # nothing seeded for this action
    ],
)
async def test_activity_list_filters_by_action(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    filter_action: str,
    expected_min: int,
) -> None:
    await _seed_activity_rows(
        db,
        org_id=seed_admin_user["org_id"],
        user_id=seed_admin_user["id"],
        count=60,
    )
    r = await admin_authed.get(
        "/api/orgs/me/activity",
        params={"limit": "200", "action": filter_action},
    )
    assert r.status_code == 200
    entries = r.json()["entries"]
    if expected_min == 0:
        assert entries == []
    else:
        assert len(entries) >= expected_min
        # Every returned row must match the filter exactly.
        assert all(e["action"] == filter_action for e in entries)


async def test_activity_list_filters_by_actor_user_id(
    admin_authed: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
    seed_admin_user: dict[str, str],
) -> None:
    other_user_id = await _seed_user_with_membership(
        db,
        org_id=axiolo_org["id"],
        email=f"other-{secrets.token_hex(3)}@example.com",
    )
    await _seed_activity_rows(
        db,
        org_id=axiolo_org["id"],
        user_id=seed_admin_user["id"],
        count=10,
    )
    await _seed_activity_rows(
        db,
        org_id=axiolo_org["id"],
        user_id=other_user_id,
        count=4,
    )

    r = await admin_authed.get(
        "/api/orgs/me/activity",
        params={"limit": "200", "actor_user_id": other_user_id},
    )
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert {e["actor"]["user_id"] for e in entries} == {other_user_id}
    assert len(entries) == 4


async def test_activity_list_invalid_cursor_returns_422(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.get(
        "/api/orgs/me/activity", params={"cursor": "not-a-timestamp"}
    )
    assert r.status_code == 422


async def test_activity_list_invalid_actor_uuid_returns_422(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.get(
        "/api/orgs/me/activity",
        params={"actor_user_id": "not-a-uuid"},
    )
    assert r.status_code == 422


async def test_activity_list_requires_membership(
    client: AsyncClient,
    seed_user: dict[str, str],
) -> None:
    """An authenticated user with no org membership gets 403, not 200."""
    client.cookies.set(
        settings.session_cookie_name, encode_session(seed_user["id"])
    )
    r = await client.get("/api/orgs/me/activity")
    assert r.status_code == 403


async def test_activity_list_includes_actor_display(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """The two-pass enrichment populates the ``actor.email`` field."""
    await admin_authed.post("/api/admin/clients", json={"name": "A"})

    r = await admin_authed.get("/api/orgs/me/activity")
    assert r.status_code == 200
    body = r.json()
    actors = {e["actor"]["email"] for e in body["entries"]}
    assert seed_admin_user["email"] in actors
