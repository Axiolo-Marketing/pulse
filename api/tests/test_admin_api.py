"""Admin API tests.

Two-axis coverage:
  • Auth gate: anonymous → 401, non-admin user → 403, admin → 2xx, across
    every endpoint × method.
  • Happy paths + key invariants: delete cascades to responses + uploads,
    response_type can't be patched.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.session import encode_session
from pulse_api.config import settings


ADMIN_ENDPOINTS = [
    ("GET",    "/api/admin/engagements",                                      None),
    ("GET",    "/api/admin/engagements/00000000-0000-0000-0000-000000000000", None),
    ("POST",   "/api/admin/engagements",                                      {"client_name": "x"}),
    ("PATCH",  "/api/admin/engagements/00000000-0000-0000-0000-000000000000", {"engagement_name": "y"}),
    ("DELETE", "/api/admin/engagements/00000000-0000-0000-0000-000000000000", None),
    ("POST",   "/api/admin/engagements/00000000-0000-0000-0000-000000000000/reset", None),
    ("POST",   "/api/admin/engagements/00000000-0000-0000-0000-000000000000/cards",
        {"category": "C", "title": "T", "context": "X", "question": "Q", "response_type": "short-text"}),
    ("POST",   "/api/admin/engagements/00000000-0000-0000-0000-000000000000/cards/import-markdown",
        {"markdown": "## Card 1: X\n\n**Category:** C\n**Type:** short-text\n**Skip:** optional\n\n**Context:** ctx\n\n**Question:** q?"}),
    ("PATCH",  "/api/admin/cards/00000000-0000-0000-0000-000000000000",   {"title": "y"}),
    ("DELETE", "/api/admin/cards/00000000-0000-0000-0000-000000000000",   None),
]


# ── Auth gate: anonymous → 401 ────────────────────────────────────────────


@pytest.mark.parametrize("method, path, body", ADMIN_ENDPOINTS)
async def test_admin_endpoint_rejects_anonymous(
    client: AsyncClient, method: str, path: str, body: dict | None
) -> None:
    r = await client.request(method, path, json=body)
    assert r.status_code == 401


# ── Auth gate: signed in but not admin → 403 ──────────────────────────────


@pytest.fixture
def non_admin_session_cookie(seed_user: dict[str, str]) -> str:
    return encode_session(seed_user["id"])


@pytest.mark.parametrize("method, path, body", ADMIN_ENDPOINTS)
async def test_admin_endpoint_rejects_non_admin(
    client: AsyncClient,
    non_admin_session_cookie: str,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    client.cookies.set(settings.session_cookie_name, non_admin_session_cookie)
    r = await client.request(method, path, json=body)
    assert r.status_code == 403


# ── GET /api/admin/engagements ────────────────────────────────────────────────


async def test_list_engagements_includes_aggregates(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    db: AsyncSession,
) -> None:
    # Mark one card answered, one skipped for the seeded recipient, leave
    # the rest untouched. Post-0015 progress is per-recipient, so the
    # summary reports recipient rollups rather than answered/skipped counts.
    answered_id, skipped_id = seed_cards[0]["id"], seed_cards[1]["id"]
    await db.execute(
        text(
            "insert into public.responses "
            "(card_id, engagement_id, recipient_id, state, answered_at) "
            "values (cast(:k as uuid), cast(:c as uuid), "
            "        (select id from public.recipients "
            "           where engagement_id = cast(:c as uuid) limit 1), "
            "        'answered', now())"
        ),
        {"k": answered_id, "c": seed_client["id"]},
    )
    await db.execute(
        text(
            "insert into public.responses "
            "(card_id, engagement_id, recipient_id, state, answered_at) "
            "values (cast(:k as uuid), cast(:c as uuid), "
            "        (select id from public.recipients "
            "           where engagement_id = cast(:c as uuid) limit 1), "
            "        'skipped', now())"
        ),
        {"k": skipped_id, "c": seed_client["id"]},
    )

    r = await admin_authed.get("/api/admin/engagements")
    assert r.status_code == 200
    row = next(c for c in r.json() if c["id"] == seed_client["id"])
    assert row["total_cards"] == 8
    assert row["recipients_count"] == 1
    # 2 of 8 cards done → the lone recipient is not yet complete.
    assert row["completed_recipients"] == 0
    # One answered + one skipped = 2 responses in, across all recipients.
    # The admin list shows this over the expected total (cards * recipients).
    assert row["answered_responses"] == 2


async def test_list_engagements_returns_all_clients(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
) -> None:
    """Both clients show up regardless of which was inserted first.
    Order is `created_at desc` but the two fixture inserts can land on
    the same microsecond — don't assert on their relative order here."""
    r = await admin_authed.get("/api/admin/engagements")
    ids = {c["id"] for c in r.json()}
    assert ids == {seed_client["id"], other_seeded_client["id"]}


# ── GET /api/admin/engagements/{id} ───────────────────────────────────────────


async def test_get_engagement_returns_full_detail(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    r = await admin_authed.get(f"/api/admin/engagements/{seed_client['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["engagement"]["id"] == seed_client["id"]
    assert len(body["cards"]) == 8
    assert body["responses"] == []
    assert body["uploads"] == []


async def test_get_engagement_unknown_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.get(f"/api/admin/engagements/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_get_engagement_malformed_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.get("/api/admin/engagements/not-a-uuid")
    assert r.status_code == 404


# ── POST /api/admin/engagements ───────────────────────────────────────────────


async def test_create_engagement_then_recipient_mints_token(
    admin_authed: AsyncClient, db: AsyncSession
) -> None:
    r = await admin_authed.post(
        "/api/admin/engagements",
        json={"client_name": "New Client", "engagement_name": "Q3 review"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "New Client"
    assert body["client_name"] == "New Client"
    assert body["engagement_name"] == "Q3 review"
    # The deck link is minted per recipient now — the engagement itself
    # carries no token.
    assert "token" not in body
    rec = await admin_authed.post(
        f"/api/admin/engagements/{body['id']}/recipients",
        json={"email": "client@example.com"},
    )
    assert rec.status_code == 201
    token = rec.json()["token"]
    assert len(token) == 16  # 16-hex-char token
    assert all(ch in "0123456789abcdef" for ch in token)


@pytest.mark.parametrize(
    "payload, expected_status",
    [
        ({"client_name": ""},                   422),  # min_length 1
        ({},                                    422),  # neither client_id nor name
        ({"client_name": "ok"},                 201),  # org/engagement optional
    ],
)
async def test_create_engagement_validation(
    admin_authed: AsyncClient, payload: dict, expected_status: int
) -> None:
    r = await admin_authed.post("/api/admin/engagements", json=payload)
    assert r.status_code == expected_status


async def test_recipients_have_distinct_tokens(
    admin_authed: AsyncClient,
) -> None:
    eng = await admin_authed.post("/api/admin/engagements", json={"client_name": "A"})
    eid = eng.json()["id"]
    a = await admin_authed.post(
        f"/api/admin/engagements/{eid}/recipients", json={"email": "a@example.com"}
    )
    b = await admin_authed.post(
        f"/api/admin/engagements/{eid}/recipients", json={"email": "b@example.com"}
    )
    assert a.json()["token"] != b.json()["token"]


# ── Auto-invite: adding a respondent mails them; the first card mails stragglers ─


async def _recipient_by_email(
    admin_authed: AsyncClient, engagement_id: str, email: str
) -> dict:
    recips = (
        await admin_authed.get(f"/api/admin/engagements/{engagement_id}/recipients")
    ).json()
    return next(x for x in recips if x["email"] == email)


async def test_add_respondent_emails_invite_when_deck_has_cards(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    captured_emails: list,
) -> None:
    eid = seed_client["id"]
    # seed_client's own recipient has no email (legacy) → never invited; the
    # one we add is the only invitee, and the deck already has cards.
    r = await admin_authed.post(
        f"/api/admin/engagements/{eid}/recipients",
        json={"email": "ask@example.com", "name": "Ask"},
    )
    assert r.status_code == 201
    # The invite goes out on add — no separate send step.
    assert len(captured_emails) == 1
    assert captured_emails[0].to == "ask@example.com"
    assert "?t=" in captured_emails[0].body  # the deck link
    assert (await _recipient_by_email(admin_authed, eid, "ask@example.com"))[
        "invited_at"
    ] is not None


async def test_empty_deck_defers_invite_until_first_card(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    captured_emails: list,
) -> None:
    eid = seed_client["id"]  # no cards yet
    await admin_authed.post(
        f"/api/admin/engagements/{eid}/recipients",
        json={"email": "wait@example.com"},
    )
    # Nothing sent yet — there are no questions to answer.
    assert captured_emails == []
    assert (await _recipient_by_email(admin_authed, eid, "wait@example.com"))[
        "invited_at"
    ] is None

    # Adding the first card invites the waiting respondent.
    card = await admin_authed.post(
        f"/api/admin/engagements/{eid}/cards",
        json={
            "category": "C", "title": "T", "context": "x",
            "question": "q?", "response_type": "short-text", "skip_allowed": True,
        },
    )
    assert card.status_code == 201
    assert len(captured_emails) == 1
    assert captured_emails[0].to == "wait@example.com"
    assert (await _recipient_by_email(admin_authed, eid, "wait@example.com"))[
        "invited_at"
    ] is not None


# ── PATCH /api/admin/engagements/{id} ─────────────────────────────────────────


async def test_patch_engagement_updates_brief(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.patch(
        f"/api/admin/engagements/{seed_client['id']}",
        json={"brief": "## Goals\n- ship the migration"},
    )
    assert r.status_code == 200
    assert r.json()["brief"].startswith("## Goals")


async def test_patch_engagement_partial_only_writes_provided_fields(
    admin_authed: AsyncClient, seed_client: dict[str, str], db: AsyncSession
) -> None:
    await admin_authed.patch(
        f"/api/admin/engagements/{seed_client['id']}", json={"brief": "v1"}
    )
    r = await admin_authed.patch(
        f"/api/admin/engagements/{seed_client['id']}",
        json={"engagement_name": "New Eng"},
    )
    assert r.json()["engagement_name"] == "New Eng"
    # brief was NOT overwritten by the second PATCH
    assert r.json()["brief"] == "v1"


async def test_patch_engagement_ignores_unknown_fields(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    """A stray field in the PATCH body (e.g. a ``token``) is ignored —
    only real columns update. Magic-link tokens live on recipients and
    are never editable through the engagement update path."""
    r = await admin_authed.patch(
        f"/api/admin/engagements/{seed_client['id']}",
        json={"token": "ffffffffffffffff", "engagement_name": "still updates"},
    )
    assert r.status_code == 200
    assert r.json()["engagement_name"] == "still updates"
    assert "token" not in r.json()


async def test_patch_engagement_unknown_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.patch(
        f"/api/admin/engagements/{uuid.uuid4()}", json={"engagement_name": "x"}
    )
    assert r.status_code == 404


async def test_get_engagement_voice_enabled_defaults_false(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.get(f"/api/admin/engagements/{seed_client['id']}")
    assert r.status_code == 200
    assert r.json()["engagement"]["voice_enabled"] is False


async def test_patch_engagement_toggles_voice_enabled(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.patch(
        f"/api/admin/engagements/{seed_client['id']}",
        json={"voice_enabled": True},
    )
    assert r.status_code == 200
    assert r.json()["voice_enabled"] is True
    # And it persists on a fresh read.
    detail = await admin_authed.get(f"/api/admin/engagements/{seed_client['id']}")
    assert detail.json()["engagement"]["voice_enabled"] is True


async def test_patch_engagement_omitting_voice_enabled_leaves_it(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    """A PATCH without `voice_enabled` must not reset the flag — it rides
    the same `model_dump(exclude_unset=True)` path as every other field."""
    await admin_authed.patch(
        f"/api/admin/engagements/{seed_client['id']}",
        json={"voice_enabled": True},
    )
    r = await admin_authed.patch(
        f"/api/admin/engagements/{seed_client['id']}",
        json={"engagement_name": "New Eng"},
    )
    assert r.status_code == 200
    assert r.json()["engagement_name"] == "New Eng"
    assert r.json()["voice_enabled"] is True  # untouched by the second PATCH


async def test_list_engagements_includes_voice_enabled(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.get("/api/admin/engagements")
    assert r.status_code == 200
    row = next(c for c in r.json() if c["id"] == seed_client["id"])
    assert row["voice_enabled"] is False


# ── DELETE /api/admin/engagements/{id} ────────────────────────────────────────


async def test_delete_engagement_removes_client_and_cascades(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    db: AsyncSession,
) -> None:
    # Seed a response too so we can prove cascade.
    await db.execute(
        text(
            "insert into public.responses "
            "(card_id, engagement_id, recipient_id, state, answered_at) "
            "values (cast(:k as uuid), cast(:c as uuid), "
            "        (select id from public.recipients "
            "           where engagement_id = cast(:c as uuid) limit 1), "
            "        'answered', now())"
        ),
        {"k": seed_cards[0]["id"], "c": seed_client["id"]},
    )

    r = await admin_authed.delete(f"/api/admin/engagements/{seed_client['id']}")
    assert r.status_code == 204

    # Client gone
    row = (
        await db.execute(
            text("select count(*) from public.engagements where id = cast(:c as uuid)"),
            {"c": seed_client["id"]},
        )
    ).scalar_one()
    assert row == 0

    # Cards + responses cascaded
    cards_remaining = (
        await db.execute(
            text("select count(*) from public.cards where engagement_id = cast(:c as uuid)"),
            {"c": seed_client["id"]},
        )
    ).scalar_one()
    responses_remaining = (
        await db.execute(
            text("select count(*) from public.responses where engagement_id = cast(:c as uuid)"),
            {"c": seed_client["id"]},
        )
    ).scalar_one()
    assert cards_remaining == 0
    assert responses_remaining == 0


async def test_delete_engagement_removes_upload_files_from_disk(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    db: AsyncSession,
    tmp_uploads_dir,
) -> None:
    """Files referenced by the cascaded uploads rows must be unlinked
    from disk; the cascade alone doesn't touch the filesystem."""
    card_id = seed_cards[0]["id"]
    client_id = seed_client["id"]
    rel_path = f"{client_id}/{card_id}/some-id-test.txt"

    full_path = tmp_uploads_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(b"hello")
    assert full_path.exists()

    await db.execute(
        text(
            "insert into public.uploads "
            "(card_id, engagement_id, recipient_id, file_name, "
            " file_size_bytes, storage_path) "
            "values (cast(:k as uuid), cast(:c as uuid), "
            "        (select id from public.recipients "
            "           where engagement_id = cast(:c as uuid) limit 1), "
            "        :fn, 5, :sp)"
        ),
        {"k": card_id, "c": client_id, "fn": "some.txt", "sp": rel_path},
    )

    r = await admin_authed.delete(f"/api/admin/engagements/{client_id}")
    assert r.status_code == 204
    assert not full_path.exists()


async def test_reset_engagement_clears_answers_keeps_cards(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    db: AsyncSession,
    tmp_uploads_dir,
) -> None:
    """Reset wipes responses + uploads (rows and on-disk files) but leaves
    the engagement and its cards intact for a clean re-run."""
    client_id = seed_client["id"]
    card_id = seed_cards[0]["id"]

    # A response and an upload (row + file on disk).
    await db.execute(
        text(
            "insert into public.responses "
            "(card_id, engagement_id, recipient_id, state, answered_at) "
            "values (cast(:k as uuid), cast(:c as uuid), "
            "        (select id from public.recipients "
            "           where engagement_id = cast(:c as uuid) limit 1), "
            "        'answered', now())"
        ),
        {"k": card_id, "c": client_id},
    )
    rel_path = f"{client_id}/{card_id}/reset-me.txt"
    full_path = tmp_uploads_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(b"data")
    await db.execute(
        text(
            "insert into public.uploads "
            "(card_id, engagement_id, recipient_id, file_name, "
            " file_size_bytes, storage_path) "
            "values (cast(:k as uuid), cast(:c as uuid), "
            "        (select id from public.recipients "
            "           where engagement_id = cast(:c as uuid) limit 1), "
            "        :fn, 4, :sp)"
        ),
        {"k": card_id, "c": client_id, "fn": "reset-me.txt", "sp": rel_path},
    )

    r = await admin_authed.post(f"/api/admin/engagements/{client_id}/reset")
    assert r.status_code == 200
    assert r.json() == {"responses_cleared": 1, "uploads_cleared": 1}

    # Answers gone, file unlinked.
    responses_left = (
        await db.execute(
            text("select count(*) from public.responses where engagement_id = cast(:c as uuid)"),
            {"c": client_id},
        )
    ).scalar_one()
    uploads_left = (
        await db.execute(
            text("select count(*) from public.uploads where engagement_id = cast(:c as uuid)"),
            {"c": client_id},
        )
    ).scalar_one()
    assert responses_left == 0
    assert uploads_left == 0
    assert not full_path.exists()

    # Cards + engagement preserved.
    cards_left = (
        await db.execute(
            text("select count(*) from public.cards where engagement_id = cast(:c as uuid)"),
            {"c": client_id},
        )
    ).scalar_one()
    assert cards_left == len(seed_cards)
    detail = await admin_authed.get(f"/api/admin/engagements/{client_id}")
    assert detail.status_code == 200


async def test_reset_engagement_unknown_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.post(f"/api/admin/engagements/{uuid.uuid4()}/reset")
    assert r.status_code == 404


async def test_delete_engagement_unknown_id_returns_404(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.delete(f"/api/admin/engagements/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_delete_engagement_only_affects_targeted_client(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
    db: AsyncSession,
) -> None:
    r = await admin_authed.delete(f"/api/admin/engagements/{seed_client['id']}")
    assert r.status_code == 204

    remaining = (
        await db.execute(
            text("select count(*) from public.engagements where id = cast(:c as uuid)"),
            {"c": other_seeded_client["id"]},
        )
    ).scalar_one()
    assert remaining == 1


# ── POST /api/admin/engagements/{id}/cards ────────────────────────────────────


async def test_add_card_assigns_next_order_index(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    # seed_cards inserted 8 cards (indices 1..8). New card should be 9.
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards",
        json={
            "category": "New",
            "title": "Ninth card",
            "context": "ctx",
            "question": "q?",
            "response_type": "short-text",
        },
    )
    assert r.status_code == 201
    assert r.json()["order_index"] == 9


@pytest.mark.parametrize(
    "response_type",
    [
        "confirm-edit", "single-select", "multi-select", "short-text",
        "long-text", "file-upload", "document-link", "contact-share",
    ],
)
async def test_add_card_accepts_every_response_type(
    admin_authed: AsyncClient, seed_client: dict[str, str], response_type: str
) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards",
        json={
            "category": "C", "title": "T", "context": "X", "question": "Q",
            "response_type": response_type,
            "options": ["A", "B"] if "select" in response_type else None,
        },
    )
    assert r.status_code == 201
    assert r.json()["response_type"] == response_type


async def test_add_card_rejects_invalid_response_type(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards",
        json={
            "category": "C", "title": "T", "context": "X", "question": "Q",
            "response_type": "free-form",  # not in the enum
        },
    )
    assert r.status_code == 422


async def test_add_card_unknown_engagement_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{uuid.uuid4()}/cards",
        json={
            "category": "C", "title": "T", "context": "X", "question": "Q",
            "response_type": "short-text",
        },
    )
    assert r.status_code == 404


# ── POST /api/admin/engagements/{id}/cards/import-markdown ────────────────────


IMPORT_MD_TWO_CARDS = """\
## Card 1: Buying Trigger

**Category:** Confirm What We Know
**Type:** single-select
**Skip:** required

**Context:**
We have you positioned around regulatory pressure as the primary buying trigger.

**Question:**
Which trigger should we lead with?

**Options:**
- Regulatory pressure
- Tech debt

---

## Card 2: SOW Template

**Category:** Documents and Access
**Type:** file-upload
**Skip:** optional
**Attachment:** deliverables/sow.html

**Context:**
We need the current SOW template.

**Question:**
Upload the SOW template you currently use.
"""


async def test_import_markdown_creates_cards_in_order(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": IMPORT_MD_TWO_CARDS},
    )
    assert r.status_code == 201
    created = r.json()["created"]
    assert len(created) == 2
    assert [c["order_index"] for c in created] == [1, 2]
    assert created[0]["response_type"] == "single-select"
    assert created[0]["options"] == ["Regulatory pressure", "Tech debt"]
    assert created[0]["skip_allowed"] is False
    assert created[1]["response_type"] == "file-upload"
    assert created[1]["attachment_path"] == "deliverables/sow.html"


async def test_import_markdown_appends_after_existing_cards(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    """seed_cards inserts 8 cards. Imported cards should start at order 9."""
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": IMPORT_MD_TWO_CARDS},
    )
    assert r.status_code == 201
    created = r.json()["created"]
    assert [c["order_index"] for c in created] == [9, 10]


async def test_import_markdown_unknown_engagement_returns_404(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{uuid.uuid4()}/cards/import-markdown",
        json={"markdown": IMPORT_MD_TWO_CARDS},
    )
    assert r.status_code == 404


async def test_import_markdown_returns_parse_errors(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    bad = """\
## Card 1: Test

**Category:** Cat
**Type:** rambling-essay
**Skip:** optional

**Context:** ctx

**Question:** q?
"""
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": bad},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "invalid Type" in detail


async def test_import_markdown_rejects_empty_body(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": ""},
    )
    assert r.status_code == 422


async def test_import_markdown_no_partial_writes_on_error(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    db: AsyncSession,
) -> None:
    """If any card in the deck fails validation, none should be inserted."""
    mixed = IMPORT_MD_TWO_CARDS + """\

---

## Card 3: Bad One

**Category:** Cat
**Type:** rambling-essay
**Skip:** optional

**Context:** ctx

**Question:** q?
"""
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": mixed},
    )
    assert r.status_code == 400

    count = (
        await db.execute(
            text(
                "select count(*) from public.cards where engagement_id = cast(:c as uuid)"
            ),
            {"c": seed_client["id"]},
        )
    ).scalar_one()
    assert count == 0


# ── PATCH /api/admin/cards/{id} ───────────────────────────────────────────


async def test_patch_card_updates_title_and_question(
    admin_authed: AsyncClient, seed_cards: list[dict[str, str]]
) -> None:
    r = await admin_authed.patch(
        f"/api/admin/cards/{seed_cards[0]['id']}",
        json={"title": "Updated title", "question": "Updated question?"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Updated title"
    assert r.json()["question"] == "Updated question?"


async def test_patch_card_ignores_response_type_in_body(
    admin_authed: AsyncClient, seed_cards: list[dict[str, str]]
) -> None:
    """response_type isn't in UpdateCardRequest, so pydantic drops it
    silently. The card's type must NOT change — this is the data-integrity
    invariant that keeps response_value shapes consistent."""
    card = next(c for c in seed_cards if c["response_type"] == "short-text")
    r = await admin_authed.patch(
        f"/api/admin/cards/{card['id']}",
        json={"title": "still works", "response_type": "long-text"},
    )
    assert r.status_code == 200
    assert r.json()["response_type"] == "short-text"  # unchanged
    assert r.json()["title"] == "still works"


async def test_patch_card_options_jsonb(
    admin_authed: AsyncClient, seed_cards: list[dict[str, str]]
) -> None:
    card = next(c for c in seed_cards if c["response_type"] == "single-select")
    r = await admin_authed.patch(
        f"/api/admin/cards/{card['id']}",
        json={"options": ["new-A", "new-B", "new-C"]},
    )
    assert r.status_code == 200
    assert r.json()["options"] == ["new-A", "new-B", "new-C"]


async def test_patch_card_unknown_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.patch(
        f"/api/admin/cards/{uuid.uuid4()}", json={"title": "x"}
    )
    assert r.status_code == 404


# ── DELETE /api/admin/cards/{id} ──────────────────────────────────────────


async def test_delete_card_cascades_responses(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    db: AsyncSession,
) -> None:
    card_id = seed_cards[0]["id"]
    # Attach a response to the card
    await db.execute(
        text(
            "insert into public.responses "
            "(card_id, engagement_id, recipient_id, state, answered_at) "
            "values (cast(:k as uuid), cast(:c as uuid), "
            "        (select id from public.recipients "
            "           where engagement_id = cast(:c as uuid) limit 1), "
            "        'answered', now())"
        ),
        {"k": card_id, "c": seed_client["id"]},
    )

    r = await admin_authed.delete(f"/api/admin/cards/{card_id}")
    assert r.status_code == 204

    remaining = (
        await db.execute(
            text("select count(*) from public.responses where card_id = cast(:k as uuid)"),
            {"k": card_id},
        )
    ).scalar()
    assert remaining == 0


async def test_delete_card_unknown_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.delete(f"/api/admin/cards/{uuid.uuid4()}")
    assert r.status_code == 404
