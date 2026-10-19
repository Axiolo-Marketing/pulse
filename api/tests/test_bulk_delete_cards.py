"""Bulk card deletion + replace-on-import (``POST …/cards/bulk-delete``,
``POST …/cards/import-markdown`` with ``replace``)."""
from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import storage
from tests.test_admin_api import IMPORT_MD_TWO_CARDS


async def _card_titles(db: AsyncSession, engagement_id: str) -> list[str]:
    rows = await db.execute(
        text(
            "select title from public.cards where engagement_id = cast(:e as uuid) "
            "order by order_index"
        ),
        {"e": engagement_id},
    )
    return list(rows.scalars().all())


async def _answer_with_upload(
    db: AsyncSession, seed_client: dict[str, str], card_id: str
) -> str:
    """Give the seeded respondent an answer + an uploaded file on ``card_id``;
    returns the file's storage path."""
    await db.execute(
        text(
            "insert into public.responses (card_id, engagement_id, recipient_id, org_id, state) "
            "values (cast(:c as uuid), cast(:e as uuid), cast(:r as uuid), cast(:o as uuid), "
            "        'answered')"
        ),
        {
            "c": card_id,
            "e": seed_client["id"],
            "r": seed_client["recipient_id"],
            "o": seed_client["org_id"],
        },
    )
    rel = f"{seed_client['client_id']}/{card_id}/{uuid.uuid4()}-answer.txt"
    storage.write_upload(relative_path=rel, content=b"bytes")
    await db.execute(
        text(
            "insert into public.uploads (card_id, engagement_id, recipient_id, org_id, "
            "file_name, file_size_bytes, storage_path) values (cast(:c as uuid), "
            "cast(:e as uuid), cast(:r as uuid), cast(:o as uuid), 'answer.txt', 5, :p)"
        ),
        {
            "c": card_id,
            "e": seed_client["id"],
            "r": seed_client["recipient_id"],
            "o": seed_client["org_id"],
            "p": rel,
        },
    )
    return rel


async def test_bulk_delete_selected_cards(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    doomed = seed_cards[:3]
    path = await _answer_with_upload(db, seed_client, doomed[0]["id"])

    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/bulk-delete",
        json={"card_ids": [c["id"] for c in doomed]},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 3}

    remaining = await _card_titles(db, seed_client["id"])
    assert remaining == [c["title"] for c in seed_cards[3:]]
    answers = (
        await db.execute(
            text("select count(*) from public.responses where card_id = cast(:c as uuid)"),
            {"c": doomed[0]["id"]},
        )
    ).scalar_one()
    assert answers == 0
    assert not (storage.settings.upload_dir / path).exists()

    audit = (
        await db.execute(
            text(
                "select metadata from public.audit_logs where action = 'card.bulk_delete' "
                "and target_id = :e"
            ),
            {"e": seed_client["id"]},
        )
    ).scalar_one()
    assert audit["count"] == 3
    assert audit["all"] is False
    assert sorted(audit["titles"]) == sorted(c["title"] for c in doomed)


async def test_bulk_delete_all_keeps_engagement_and_respondent_link(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/bulk-delete",
        json={"all": True},
    )
    assert r.json() == {"deleted": len(seed_cards)}
    assert await _card_titles(db, seed_client["id"]) == []
    token = (
        await db.execute(
            text("select token from public.recipients where id = cast(:r as uuid)"),
            {"r": seed_client["recipient_id"]},
        )
    ).scalar_one()
    assert token == seed_client["token"]


async def test_bulk_delete_ignores_cards_on_other_engagements(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    """A card id from another deck is silently skipped, never deleted."""
    r = await admin_authed.post(
        f"/api/admin/engagements/{other_seeded_client['id']}/cards/bulk-delete",
        json={"card_ids": [seed_cards[0]["id"], "not-a-uuid"]},
    )
    assert r.json() == {"deleted": 0}
    assert len(await _card_titles(db, seed_client["id"])) == len(seed_cards)
    audits = (
        await db.execute(
            text("select count(*) from public.audit_logs where action = 'card.bulk_delete'")
        )
    ).scalar_one()
    assert audits == 0


async def test_bulk_delete_validates_body(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    url = f"/api/admin/engagements/{seed_client['id']}/cards/bulk-delete"
    for body in ({}, {"card_ids": []}, {"card_ids": ["x"], "all": True}):
        r = await admin_authed.post(url, json=body)
        assert r.status_code == 422, body


async def test_bulk_delete_unknown_engagement_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{uuid.uuid4()}/cards/bulk-delete", json={"all": True}
    )
    assert r.status_code == 404


async def test_import_replace_swaps_the_deck(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    path = await _answer_with_upload(db, seed_client, seed_cards[0]["id"])
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": IMPORT_MD_TWO_CARDS, "replace": True},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["replaced"] == len(seed_cards)
    assert [c["order_index"] for c in body["created"]] == [1, 2]
    assert await _card_titles(db, seed_client["id"]) == ["Buying Trigger", "SOW Template"]
    assert not (storage.settings.upload_dir / path).exists()

    meta = (
        await db.execute(
            text("select metadata from public.audit_logs where action = 'card.import'")
        )
    ).scalar_one()
    assert meta == {"count": 2, "replaced": len(seed_cards)}


async def test_import_replace_with_bad_markdown_keeps_the_old_deck(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
) -> None:
    r = await admin_authed.post(
        f"/api/admin/engagements/{seed_client['id']}/cards/import-markdown",
        json={"markdown": "## Card 1: Broken\n\n**Type:** nope\n", "replace": True},
    )
    assert r.status_code == 400
    assert len(await _card_titles(db, seed_client["id"])) == len(seed_cards)
