"""Admin API tests.

Two-axis coverage:
  • Auth gate: anonymous → 401, non-admin user → 403, admin → 2xx, across
    every endpoint × method.
  • Happy paths + key invariants: rotate-token invalidates the old token,
    delete cascades to responses + uploads, response_type can't be patched.
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
    ("GET",    "/api/admin/clients",                                      None),
    ("GET",    "/api/admin/clients/00000000-0000-0000-0000-000000000000", None),
    ("POST",   "/api/admin/clients",                                      {"name": "x"}),
    ("PATCH",  "/api/admin/clients/00000000-0000-0000-0000-000000000000", {"name": "y"}),
    ("POST",   "/api/admin/clients/00000000-0000-0000-0000-000000000000/rotate-token", None),
    ("POST",   "/api/admin/clients/00000000-0000-0000-0000-000000000000/cards",
        {"category": "C", "title": "T", "context": "X", "question": "Q", "response_type": "short-text"}),
    ("POST",   "/api/admin/clients/00000000-0000-0000-0000-000000000000/cards/import-markdown",
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


# ── GET /api/admin/clients ────────────────────────────────────────────────


async def test_list_engagements_includes_aggregates(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    db: AsyncSession,
) -> None:
    # Mark one card answered, one skipped, leave the rest untouched.
    answered_id, skipped_id = seed_cards[0]["id"], seed_cards[1]["id"]
    await db.execute(
        text(
            "insert into public.responses (card_id, client_id, state, answered_at) "
            "values (cast(:k as uuid), cast(:c as uuid), 'answered', now())"
        ),
        {"k": answered_id, "c": seed_client["id"]},
    )
    await db.execute(
        text(
            "insert into public.responses (card_id, client_id, state, answered_at) "
            "values (cast(:k as uuid), cast(:c as uuid), 'skipped', now())"
        ),
        {"k": skipped_id, "c": seed_client["id"]},
    )

    r = await admin_authed.get("/api/admin/clients")
    assert r.status_code == 200
    row = next(c for c in r.json() if c["id"] == seed_client["id"])
    assert row["total_cards"] == 8
    assert row["answered_count"] == 1
    assert row["skipped_count"] == 1


async def test_list_engagements_returns_all_clients(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
) -> None:
    """Both clients show up regardless of which was inserted first.
    Order is `created_at desc` but the two fixture inserts can land on
    the same microsecond — don't assert on their relative order here."""
    r = await admin_authed.get("/api/admin/clients")
    ids = {c["id"] for c in r.json()}
    assert ids == {seed_client["id"], other_seeded_client["id"]}


# ── GET /api/admin/clients/{id} ───────────────────────────────────────────


async def test_get_engagement_returns_full_detail(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    r = await admin_authed.get(f"/api/admin/clients/{seed_client['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["client"]["id"] == seed_client["id"]
    assert len(body["cards"]) == 8
    assert body["responses"] == []
    assert body["uploads"] == []


async def test_get_engagement_unknown_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.get(f"/api/admin/clients/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_get_engagement_malformed_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.get("/api/admin/clients/not-a-uuid")
    assert r.status_code == 404


# ── POST /api/admin/clients ───────────────────────────────────────────────


async def test_create_engagement_generates_token(
    admin_authed: AsyncClient, db: AsyncSession
) -> None:
    r = await admin_authed.post(
        "/api/admin/clients",
        json={"name": "New Client", "org_name": "Acme", "engagement_name": "Q3 review"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "New Client"
    assert body["org_name"] == "Acme"
    # 16-hex-char token
    assert len(body["token"]) == 16
    assert all(ch in "0123456789abcdef" for ch in body["token"])


@pytest.mark.parametrize(
    "payload, expected_status",
    [
        ({"name": ""},                          422),  # min_length 1
        ({},                                    422),  # missing name
        ({"name": "ok"},                        201),  # org/engagement optional
    ],
)
async def test_create_engagement_validation(
    admin_authed: AsyncClient, payload: dict, expected_status: int
) -> None:
    r = await admin_authed.post("/api/admin/clients", json=payload)
    assert r.status_code == expected_status


async def test_create_two_engagements_have_distinct_tokens(
    admin_authed: AsyncClient,
) -> None:
    r1 = await admin_authed.post("/api/admin/clients", json={"name": "A"})
    r2 = await admin_authed.post("/api/admin/clients", json={"name": "B"})
    assert r1.json()["token"] != r2.json()["token"]


# ── PATCH /api/admin/clients/{id} ─────────────────────────────────────────


async def test_patch_engagement_updates_brief(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}",
        json={"brief": "## Goals\n- ship the migration"},
    )
    assert r.status_code == 200
    assert r.json()["brief"].startswith("## Goals")


async def test_patch_engagement_partial_only_writes_provided_fields(
    admin_authed: AsyncClient, seed_client: dict[str, str], db: AsyncSession
) -> None:
    await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}", json={"brief": "v1"}
    )
    r = await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}", json={"org_name": "New Org"}
    )
    assert r.json()["org_name"] == "New Org"
    # brief was NOT overwritten by the second PATCH
    assert r.json()["brief"] == "v1"


async def test_patch_engagement_does_not_accept_token_field(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    """Rotating the token must require the dedicated endpoint, not a
    sneaky PATCH body field."""
    original = seed_client["token"]
    r = await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}",
        json={"token": "ffffffffffffffff", "name": "still updates name"},
    )
    assert r.status_code == 200
    assert r.json()["token"] == original  # unchanged
    assert r.json()["name"] == "still updates name"


async def test_patch_engagement_unknown_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.patch(
        f"/api/admin/clients/{uuid.uuid4()}", json={"name": "x"}
    )
    assert r.status_code == 404


# ── POST /api/admin/clients/{id}/rotate-token ─────────────────────────────


async def test_rotate_token_invalidates_old_token(
    admin_authed: AsyncClient, client: AsyncClient, seed_client: dict[str, str]
) -> None:
    # Old token works (client side)
    client.headers["X-Pulse-Token"] = seed_client["token"]
    pre = await client.get("/api/me")
    assert pre.status_code == 200

    # Rotate
    r = await admin_authed.post(f"/api/admin/clients/{seed_client['id']}/rotate-token")
    assert r.status_code == 200
    new_token = r.json()["token"]
    assert new_token != seed_client["token"]
    assert len(new_token) == 16

    # Old token no longer matches anything
    client.headers["X-Pulse-Token"] = seed_client["token"]
    post_old = await client.get("/api/me")
    assert post_old.status_code == 404

    # New token works
    client.headers["X-Pulse-Token"] = new_token
    post_new = await client.get("/api/me")
    assert post_new.status_code == 200


async def test_rotate_token_unknown_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.post(f"/api/admin/clients/{uuid.uuid4()}/rotate-token")
    assert r.status_code == 404


# ── POST /api/admin/clients/{id}/cards ────────────────────────────────────


async def test_add_card_assigns_next_order_index(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    # seed_cards inserted 8 cards (indices 1..8). New card should be 9.
    r = await admin_authed.post(
        f"/api/admin/clients/{seed_client['id']}/cards",
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
        f"/api/admin/clients/{seed_client['id']}/cards",
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
        f"/api/admin/clients/{seed_client['id']}/cards",
        json={
            "category": "C", "title": "T", "context": "X", "question": "Q",
            "response_type": "free-form",  # not in the enum
        },
    )
    assert r.status_code == 422


async def test_add_card_unknown_engagement_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.post(
        f"/api/admin/clients/{uuid.uuid4()}/cards",
        json={
            "category": "C", "title": "T", "context": "X", "question": "Q",
            "response_type": "short-text",
        },
    )
    assert r.status_code == 404


# ── POST /api/admin/clients/{id}/cards/import-markdown ────────────────────


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
        f"/api/admin/clients/{seed_client['id']}/cards/import-markdown",
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
        f"/api/admin/clients/{seed_client['id']}/cards/import-markdown",
        json={"markdown": IMPORT_MD_TWO_CARDS},
    )
    assert r.status_code == 201
    created = r.json()["created"]
    assert [c["order_index"] for c in created] == [9, 10]


async def test_import_markdown_unknown_engagement_returns_404(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.post(
        f"/api/admin/clients/{uuid.uuid4()}/cards/import-markdown",
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
        f"/api/admin/clients/{seed_client['id']}/cards/import-markdown",
        json={"markdown": bad},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "invalid Type" in detail


async def test_import_markdown_rejects_empty_body(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.post(
        f"/api/admin/clients/{seed_client['id']}/cards/import-markdown",
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
        f"/api/admin/clients/{seed_client['id']}/cards/import-markdown",
        json={"markdown": mixed},
    )
    assert r.status_code == 400

    count = (
        await db.execute(
            text(
                "select count(*) from public.cards where client_id = cast(:c as uuid)"
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
            "insert into public.responses (card_id, client_id, state, answered_at) "
            "values (cast(:k as uuid), cast(:c as uuid), 'answered', now())"
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
