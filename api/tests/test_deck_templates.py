"""Deck templates: save / list / get / update / delete, start an engagement
from one, reorder cards, and the org isolation around all of it
(migration 0024, ``routes/templates.py``, ``pulse_api/templates.py``)."""
from __future__ import annotations

import json
import secrets
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from pulse_api.audit import AUDIT_ACTIONS
from pulse_api.auth.password import hash_password
from pulse_api.auth.session import encode_session
from pulse_api.config import settings
from tests.conftest import become_anon, become_member

CARD_KEYS = {
    "category", "title", "context", "question", "response_type", "options",
    "default_value", "skip_allowed", "attachment_path",
}
SETTING_KEYS = {
    "voice_enabled", "reminders_enabled", "reactive_cards_enabled",
    "transcription_enabled",
}


async def _audit(db: AsyncSession, org_id: str, action: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            "select target_type, target_id, metadata from public.audit_logs "
            "where org_id = cast(:o as uuid) and action = :a order by created_at"
        ),
        {"o": org_id, "a": action},
    )
    return [dict(r) for r in rows.mappings().all()]


async def _add_card(
    db: AsyncSession,
    seed_client: dict[str, str],
    *,
    idx: int,
    title: str,
    source: str = "operator",
    recipient_id: str | None = None,
    block_id: str | None = None,
    response_type: str = "confirm-edit",
    default_value: str | None = None,
    options: list[str] | None = None,
) -> str:
    row = await db.execute(
        text(
            "insert into public.cards (engagement_id, order_index, category, title, "
            " context, question, response_type, options, default_value, org_id, "
            " source, recipient_id, block_id) "
            "values (cast(:e as uuid), :i, 'Cat', :t, 'ctx', 'q?', :rt, "
            " cast(:o as jsonb), :dv, cast(:org as uuid), :src, "
            " cast(:rid as uuid), :bid) returning id::text"
        ),
        {
            "e": seed_client["id"], "i": idx, "t": title, "rt": response_type,
            "o": json.dumps(options) if options is not None else None,
            "dv": default_value, "org": seed_client["org_id"], "src": source,
            "rid": recipient_id, "bid": block_id,
        },
    )
    return row.scalar_one()


@pytest.fixture
async def mixed_deck(
    db: AsyncSession, seed_client: dict[str, str]
) -> dict[str, Any]:
    """Operator cards 1,2,4 (one pre-filled, one with options), an AI card 3
    scoped to the recipient, and a page-block card 5."""
    a = await _add_card(db, seed_client, idx=1, title="A", default_value="Prefilled A")
    b = await _add_card(
        db, seed_client, idx=2, title="B", response_type="single-select",
        options=["x", "y"],
    )
    ai = await _add_card(
        db, seed_client, idx=3, title="AI follow-up", source="ai",
        recipient_id=seed_client["recipient_id"],
    )
    c = await _add_card(db, seed_client, idx=4, title="C")
    await _add_card(db, seed_client, idx=5, title="Block", block_id="blk-1")
    return {"a": a, "b": b, "ai": ai, "c": c}


async def _save(
    c: AsyncClient, engagement_id: str, name: str = "Onboarding", **extra: Any
):
    return await c.post(
        "/api/admin/templates",
        json={"engagement_id": engagement_id, "name": name, **extra},
    )


# ── save / get / list ────────────────────────────────────────────────────


async def test_save_snapshot_excludes_ai_scoped_and_block_cards(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    mixed_deck: dict[str, Any],
) -> None:
    await db.execute(
        text(
            "update public.engagements set voice_enabled = true, "
            "reminders_enabled = false where id = cast(:e as uuid)"
        ),
        {"e": seed_client["id"]},
    )
    r = await _save(admin_authed, seed_client["id"], "  Onboarding  ", description=" d ")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Onboarding" and body["description"] == "d"
    assert body["card_count"] == 3
    assert [c["title"] for c in body["cards"]] == ["A", "B", "C"]
    assert all(set(c) == CARD_KEYS for c in body["cards"])
    assert body["cards"][0]["default_value"] == "Prefilled A"
    assert body["cards"][1]["options"] == ["x", "y"]
    assert set(body["settings"]) == SETTING_KEYS
    assert body["settings"]["voice_enabled"] is True
    assert body["settings"]["reminders_enabled"] is False

    audit = await _audit(db, seed_client["org_id"], "template.create")
    assert audit[0]["target_type"] == "template"
    assert audit[0]["target_id"] == body["id"]
    assert audit[0]["metadata"] == {
        "name": "Onboarding", "cards": 3, "engagement_id": seed_client["id"],
    }


async def test_list_and_get(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    await _save(admin_authed, seed_client["id"], "beta")
    created = (await _save(admin_authed, seed_client["id"], "Alpha")).json()
    listing = (await admin_authed.get("/api/admin/templates")).json()
    assert [t["name"] for t in listing] == ["Alpha", "beta"]  # case-insensitive
    assert set(listing[0]) == {
        "id", "name", "description", "card_count", "created_at", "updated_at",
    }
    assert listing[0]["card_count"] == 8
    detail = (await admin_authed.get(f"/api/admin/templates/{created['id']}")).json()
    assert detail["id"] == created["id"] and len(detail["cards"]) == 8


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"engagement_id": "00000000-0000-0000-0000-000000000000", "name": "x"},
         404, "engagement not found"),
        ({"engagement_id": "not-a-uuid", "name": "x"}, 404, "engagement not found"),
        ({"name": "x", "engagement_id": "$seed"}, 400,
         "This engagement has no questions to save as a template."),
        ({"name": "   ", "engagement_id": "$seed"}, 422, None),
        ({"name": "x" * 121, "engagement_id": "$seed"}, 422, None),
        # A string detail (not pydantic's list) so the admin UI can show it.
        ({"name": "x", "engagement_id": "$seed", "description": "d" * 501}, 400,
         "Description must be at most 500 characters."),
    ],
)
async def test_save_errors(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    body: dict[str, str],
    status: int,
    detail: str | None,
) -> None:
    body = {k: (seed_client["id"] if v == "$seed" else v) for k, v in body.items()}
    r = await admin_authed.post("/api/admin/templates", json=body)
    assert r.status_code == status
    if detail:
        assert r.json()["detail"] == detail


async def test_duplicate_name_is_case_insensitive_409(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    assert (await _save(admin_authed, seed_client["id"], "Deck")).status_code == 201
    r = await _save(admin_authed, seed_client["id"], "dECK")
    assert r.status_code == 409
    assert r.json()["detail"] == "A template with that name already exists."


async def test_get_and_delete_missing_404(admin_authed: AsyncClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    for bad in (missing, "nope"):
        assert (await admin_authed.get(f"/api/admin/templates/{bad}")).status_code == 404
        assert (await admin_authed.delete(f"/api/admin/templates/{bad}")).status_code == 404
        r = await admin_authed.patch(f"/api/admin/templates/{bad}", json={"name": "z"})
        assert r.status_code == 404 and r.json()["detail"] == "template not found"


# ── update ───────────────────────────────────────────────────────────────


async def test_update_rename_and_describe(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    t = (await _save(admin_authed, seed_client["id"], "One")).json()
    await _save(admin_authed, seed_client["id"], "Two")
    r = await admin_authed.patch(
        f"/api/admin/templates/{t['id']}",
        json={"name": " Renamed ", "description": "Fresh"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed" and r.json()["description"] == "Fresh"
    assert r.json()["updated_at"] >= t["updated_at"]
    assert r.json()["card_count"] == 8  # untouched

    # Renaming onto another template's name 409s; onto its own is fine.
    clash = await admin_authed.patch(
        f"/api/admin/templates/{t['id']}", json={"name": "TWO"}
    )
    assert clash.status_code == 409
    same = await admin_authed.patch(
        f"/api/admin/templates/{t['id']}", json={"name": "renamed"}
    )
    assert same.status_code == 200

    cleared = await admin_authed.patch(
        f"/api/admin/templates/{t['id']}", json={"description": None}
    )
    assert cleared.json()["description"] is None

    audit = await _audit(db, seed_client["org_id"], "template.update")
    # One row per successful PATCH (the 409 writes none). Every row in a test
    # shares the transaction's timestamp, so don't rely on their order.
    changed = sorted(tuple(a["metadata"]["changed_fields"]) for a in audit)
    assert changed == [("description",), ("name",), ("name", "description")]


async def test_update_resnapshots_from_engagement(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    t = (await _save(admin_authed, seed_client["id"], "Snap")).json()
    await admin_authed.delete(f"/api/admin/cards/{seed_cards[0]['id']}")
    await db.execute(
        text(
            "update public.engagements set voice_enabled = true "
            "where id = cast(:e as uuid)"
        ),
        {"e": seed_client["id"]},
    )
    r = await admin_authed.patch(
        f"/api/admin/templates/{t['id']}", json={"engagement_id": seed_client["id"]}
    )
    assert r.status_code == 200
    assert r.json()["card_count"] == 7
    assert r.json()["settings"]["voice_enabled"] is True
    audit = await _audit(db, seed_client["org_id"], "template.update")
    assert audit[0]["metadata"]["cards"] == 7
    assert "cards" in audit[0]["metadata"]["changed_fields"]


async def test_update_resnapshot_errors(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    t = (await _save(admin_authed, seed_client["id"], "Snap")).json()
    empty = await admin_authed.patch(
        f"/api/admin/templates/{t['id']}",
        json={"engagement_id": other_seeded_client["id"]},
    )
    assert empty.status_code == 400
    missing = await admin_authed.patch(
        f"/api/admin/templates/{t['id']}",
        json={"engagement_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "engagement not found"


# ── delete ───────────────────────────────────────────────────────────────


async def test_delete_keeps_engagements_made_from_it(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    t = (await _save(admin_authed, seed_client["id"], "Gone")).json()
    eng = (
        await admin_authed.post(
            "/api/admin/engagements",
            json={"client_name": "Fresh Co", "template_id": t["id"]},
        )
    ).json()
    assert (await admin_authed.delete(f"/api/admin/templates/{t['id']}")).status_code == 204
    assert (await admin_authed.get(f"/api/admin/templates/{t['id']}")).status_code == 404
    detail = (await admin_authed.get(f"/api/admin/engagements/{eng['id']}")).json()
    assert detail["engagement"]["template_id"] is None
    assert len(detail["cards"]) == 8
    audit = await _audit(db, seed_client["org_id"], "template.delete")
    assert audit[0]["metadata"] == {"name": "Gone"}


# ── start an engagement from a template ──────────────────────────────────


async def test_create_engagement_from_template(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    mixed_deck: dict[str, Any],
    captured_emails: list,
) -> None:
    t = (await _save(admin_authed, seed_client["id"], "Kickoff")).json()
    r = await admin_authed.post(
        "/api/admin/engagements",
        json={
            "client_name": "New Client",
            "engagement_name": "2027 Plan",
            "template_id": t["id"],
        },
    )
    assert r.status_code == 201, r.text
    eng = r.json()
    assert eng["template_id"] == t["id"]
    assert eng["name"] == "New Client" and eng["engagement_name"] == "2027 Plan"

    detail = (await admin_authed.get(f"/api/admin/engagements/{eng['id']}")).json()
    assert detail["engagement"]["template_id"] == t["id"]
    assert [c["title"] for c in detail["cards"]] == ["A", "B", "C"]
    assert [c["order_index"] for c in detail["cards"]] == [1, 2, 3]
    assert detail["cards"][0]["default_value"] == "Prefilled A"
    assert detail["cards"][1]["options"] == ["x", "y"]
    assert all(c["source"] == "operator" and c["recipient_id"] is None
               for c in detail["cards"])
    assert detail["recipients"] == []
    assert captured_emails == []

    audit = await _audit(db, seed_client["org_id"], "engagement.create")
    assert audit[-1]["metadata"]["template_id"] == t["id"]
    assert audit[-1]["metadata"]["cards"] == 3


async def test_template_settings_applied_and_gated(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await db.execute(
        text(
            "update public.engagements set voice_enabled = true, "
            "reminders_enabled = false, reactive_cards_enabled = true, "
            "transcription_enabled = true where id = cast(:e as uuid)"
        ),
        {"e": seed_client["id"]},
    )
    t = (await _save(admin_authed, seed_client["id"], "Full")).json()

    async def make() -> dict[str, Any]:
        r = await admin_authed.post(
            "/api/admin/engagements",
            json={"client_name": "Gate Co", "template_id": t["id"]},
        )
        assert r.status_code == 201, r.text
        return r.json()

    # Neither the org nor the server allows them: voice/reminders carry
    # over, reactive/transcription stay off, no error.
    eng = await make()
    assert (eng["voice_enabled"], eng["reminders_enabled"]) == (True, False)
    assert (eng["reactive_cards_enabled"], eng["transcription_enabled"]) == (False, False)

    # Both allowed: they carry over too.
    await db.execute(
        text(
            "update public.organizations set reactive_cards_allowed = true "
            "where id = cast(:o as uuid)"
        ),
        {"o": seed_client["org_id"]},
    )
    monkeypatch.setattr(settings, "transcription_enabled", True)
    monkeypatch.setattr(settings, "transcription_provider", "fake")
    eng = await make()
    assert eng["reactive_cards_enabled"] is True
    assert eng["transcription_enabled"] is True


async def test_create_engagement_unknown_template_404(
    admin_authed: AsyncClient, db: AsyncSession, seed_client: dict[str, str]
) -> None:
    before = (
        await db.execute(text("select count(*) from public.engagements"))
    ).scalar_one()
    for tid in ("00000000-0000-0000-0000-000000000000", "junk"):
        r = await admin_authed.post(
            "/api/admin/engagements",
            json={"client_name": "Nope", "template_id": tid},
        )
        assert r.status_code == 404 and r.json()["detail"] == "template not found"
    after = (
        await db.execute(text("select count(*) from public.engagements"))
    ).scalar_one()
    assert after == before  # nothing half-created


async def test_create_engagement_without_template_unchanged(
    admin_authed: AsyncClient, db: AsyncSession, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.post(
        "/api/admin/engagements", json={"client_name": "Plain Co"}
    )
    assert r.status_code == 201
    eng = r.json()
    assert eng["template_id"] is None
    assert (eng["voice_enabled"], eng["reminders_enabled"]) == (False, True)
    assert (eng["reactive_cards_enabled"], eng["transcription_enabled"]) == (False, False)
    detail = (await admin_authed.get(f"/api/admin/engagements/{eng['id']}")).json()
    assert detail["cards"] == []
    audit = await _audit(db, seed_client["org_id"], "engagement.create")
    assert audit[-1]["metadata"] == {"name": "Plain Co"}


async def test_engagement_list_carries_template_id(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    t = (await _save(admin_authed, seed_client["id"], "L")).json()
    eng = (
        await admin_authed.post(
            "/api/admin/engagements",
            json={"client_name": "List Co", "template_id": t["id"]},
        )
    ).json()
    rows = (await admin_authed.get("/api/admin/engagements")).json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[eng["id"]]["template_id"] == t["id"]
    assert by_id[seed_client["id"]]["template_id"] is None


# ── reorder ──────────────────────────────────────────────────────────────


async def _order(db: AsyncSession, engagement_id: str) -> list[tuple[str, int]]:
    rows = await db.execute(
        text(
            "select title, order_index from public.cards "
            "where engagement_id = cast(:e as uuid) order by order_index"
        ),
        {"e": engagement_id},
    )
    return [(r[0], r[1]) for r in rows.all()]


async def test_reorder_keeps_ai_card_index(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    mixed_deck: dict[str, Any],
) -> None:
    # Shared cards hold indexes 1,2,4,5 (A,B,C,Block); AI sits at 3.
    ids = (await admin_authed.get(
        f"/api/admin/engagements/{seed_client['id']}"
    )).json()["cards"]
    block = next(c["id"] for c in ids if c["title"] == "Block")
    new_order = [mixed_deck["c"], block, mixed_deck["a"], mixed_deck["b"]]
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/reorder",
        json={"card_ids": new_order},
    )
    assert r.status_code == 200, r.text
    assert [c["title"] for c in r.json()["cards"]] == [
        "C", "Block", "AI follow-up", "A", "B",
    ]
    assert await _order(db, seed_client["id"]) == [
        ("C", 1), ("Block", 2), ("AI follow-up", 3), ("A", 4), ("B", 5),
    ]
    audit = await _audit(db, seed_client["org_id"], "card.reorder")
    assert audit[0]["target_type"] == "engagement"
    assert audit[0]["metadata"] == {"count": 4}


async def test_reorder_reverses_full_deck(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    ids = [c["id"] for c in seed_cards][::-1]
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/reorder",
        json={"card_ids": ids},
    )
    assert r.status_code == 200
    assert [c["id"] for c in r.json()["cards"]] == ids
    assert [c["order_index"] for c in r.json()["cards"]] == list(range(1, 9))


async def test_reorder_invalid_lists(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    foreign = await _add_card(db, other_seeded_client, idx=1, title="Foreign")
    ids = [c["id"] for c in seed_cards]
    url = f"/api/admin/engagements/{seed_client['id']}/cards/reorder"
    bad = {
        "missing": ids[:-1],
        "duplicate": [*ids[:-1], ids[0]],
        "foreign": [*ids[:-1], foreign],
        "extra": [*ids, foreign],
        "not-uuid": [*ids[:-1], "zzz"],
        "empty": [],
    }
    for label, card_ids in bad.items():
        r = await admin_authed.post(url, json={"card_ids": card_ids})
        assert r.status_code == 400, label
        assert r.json()["detail"] == (
            "card_ids must list every question on this engagement exactly once"
        )
    assert [t for t, _ in await _order(db, seed_client["id"])] == [
        f"Card {t}" for t in (
            "confirm-edit", "single-select", "multi-select", "short-text",
            "long-text", "file-upload", "document-link", "contact-share",
        )
    ]


async def test_reorder_unknown_engagement_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.post(
        "/api/admin/engagements/00000000-0000-0000-0000-000000000000/cards/reorder",
        json={"card_ids": []},
    )
    assert r.status_code == 404 and r.json()["detail"] == "engagement not found"


# ── markdown importer: pre-filled value ──────────────────────────────────


def _md(extra: str = "", rtype: str = "confirm-edit") -> str:
    options = "\n**Options:**\n- a\n- b\n" if rtype == "single-select" else ""
    return (
        "## Card 1: T\n\n**Category:** C\n"
        f"**Type:** {rtype}\n**Skip:** optional\n\n"
        f"**Context:**\nctx\n\n**Question:**\nq?\n{options}{extra}\n"
    )


@pytest.mark.parametrize(
    "label", ["Pre-filled", "Prefilled", "Pre-filled value", "Default", "pre-FILLED"]
)
async def test_import_prefilled_labels(
    admin_authed: AsyncClient, seed_client: dict[str, str], label: str
) -> None:
    md = _md(f"\n**{label}:**\nWe sell to banks.\nMostly EU.\n")
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": md},
    )
    assert r.status_code == 201, r.text
    assert r.json()["created"][0]["default_value"] == "We sell to banks.\nMostly EU."


@pytest.mark.parametrize("rule", ["---", "***", "___", "- - -"])
async def test_import_card_separator_is_not_field_text(
    admin_authed: AsyncClient, seed_client: dict[str, str], rule: str
) -> None:
    """Decks put a horizontal rule between cards. It must not end up in the
    last field of the card above it (the pre-filled value or the question)."""
    md = (
        _md("\n**Pre-filled:**\nWe sell to banks.\n")
        + f"\n{rule}\n\n"
        + "## Card 2: Second\n\n**Category:** C\n**Type:** short-text\n"
        "**Skip:** optional\n\n**Context:**\nctx\n\n**Question:**\nWhy?\n"
        f"\n{rule}\n"
    )
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": md},
    )
    assert r.status_code == 201, r.text
    first, second = r.json()["created"]
    assert first["default_value"] == "We sell to banks."
    assert second["question"] == "Why?"


async def test_import_without_prefilled_unchanged(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": _md()},
    )
    assert r.status_code == 201
    assert r.json()["created"][0]["default_value"] is None


async def test_import_prefilled_wrong_type_rejected(
    admin_authed: AsyncClient, db: AsyncSession, seed_client: dict[str, str]
) -> None:
    md = _md("\n**Pre-filled:** nope\n", rtype="single-select")
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": md},
    )
    assert r.status_code == 400
    assert "Pre-filled value provided but Type 'single-select'" in r.json()["detail"]
    assert await _order(db, seed_client["id"]) == []


# ── isolation ────────────────────────────────────────────────────────────


async def _org_b_admin(db: AsyncSession) -> tuple[str, str]:
    await db.execute(text("reset role"))  # a prior request left us as pulse_member
    org_b = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values ('Acme', :s) returning id::text"
            ),
            {"s": f"acme-{secrets.token_hex(3)}"},
        )
    ).scalar_one()
    user_b = (
        await db.execute(
            text(
                "insert into public.users (email, password_hash, name, "
                "last_active_org_id, email_verified_at) "
                "values ('b@example.com', :h, 'B', cast(:o as uuid), now()) "
                "returning id::text"
            ),
            {"h": hash_password("a-good-password-here"), "o": org_b},
        )
    ).scalar_one()
    await db.execute(
        text(
            "insert into public.organization_memberships (org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), 'owner')"
        ),
        {"o": org_b, "u": user_b},
    )
    return org_b, user_b


async def test_other_org_cannot_touch_template(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    t = (await _save(admin_authed, seed_client["id"], "Secret")).json()
    org_b, user_b = await _org_b_admin(db)
    admin_authed.cookies.set(settings.session_cookie_name, encode_session(user_b, org_b))

    assert (await admin_authed.get("/api/admin/templates")).json() == []
    url = f"/api/admin/templates/{t['id']}"
    assert (await admin_authed.get(url)).status_code == 404
    assert (await admin_authed.patch(url, json={"name": "x"})).status_code == 404
    assert (await admin_authed.delete(url)).status_code == 404
    r = await admin_authed.post(
        "/api/admin/engagements", json={"client_name": "Evil", "template_id": t["id"]}
    )
    assert r.status_code == 404 and r.json()["detail"] == "template not found"
    # Org A's engagement is not snapshot-able from org B either.
    r = await _save(admin_authed, seed_client["id"], "Steal")
    assert r.status_code == 404
    # A's template is still intact.
    await db.execute(text("reset role"))
    still = (
        await db.execute(text("select count(*) from public.deck_templates"))
    ).scalar_one()
    assert still == 1


async def test_deck_templates_rls_direct_sql(
    db_conn: AsyncConnection,
    db: AsyncSession,
    axiolo_org: dict[str, str],
) -> None:
    org_b, _ = await _org_b_admin(db)
    for org in (axiolo_org["id"], org_b):
        await db.execute(
            text(
                "insert into public.deck_templates (org_id, name) "
                "values (cast(:o as uuid), 'T')"
            ),
            {"o": org},
        )
    await become_member(db_conn, org_id=axiolo_org["id"])
    seen = (
        await db_conn.execute(text("select org_id::text from public.deck_templates"))
    ).scalars().all()
    assert seen == [axiolo_org["id"]]
    with pytest.raises(DBAPIError):
        await db_conn.execute(
            text(
                "insert into public.deck_templates (org_id, name) "
                "values (cast(:o as uuid), 'leak')"
            ),
            {"o": org_b},
        )


async def test_deck_templates_denied_to_anon(
    db_conn: AsyncConnection, db: AsyncSession, axiolo_org: dict[str, str]
) -> None:
    await db.execute(
        text(
            "insert into public.deck_templates (org_id, name) "
            "values (cast(:o as uuid), 'T')"
        ),
        {"o": axiolo_org["id"]},
    )
    await become_anon(db_conn, token="0" * 16)
    with pytest.raises(DBAPIError):
        await db_conn.execute(text("select * from public.deck_templates"))


def test_template_audit_actions_registered() -> None:
    assert {
        "template.create", "template.update", "template.delete", "card.reorder",
    } <= AUDIT_ACTIONS
