"""Page blocks: parsing ``<pulse-*>`` markup, syncing a page into an
engagement, the embed API a page's script calls, and its CORS scope."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import page_blocks, reminders
from pulse_api.config import settings
from pulse_api.embed_cors import origin_allowed

PAGE = """
<html><body>
<h2>Questions</h2>
<pulse-question id="budget" owner="Adam" category="Questions for you"
    context="This tells us who you staff.">
  <span class="q">How much do you want to spend each month?</span>
</pulse-question>
<pulse-grid id="ownership" title="Who owns each department" row-header="Department">
  <pulse-row label="Lead response"></pulse-row>
  <pulse-row label="CRM"></pulse-row>
  <pulse-col label="Does the work"></pulse-col>
  <pulse-col label="How it's staffed" options="Your team|Outside help|Open seat"></pulse-col>
</pulse-grid>
</body></html>
"""


# ── parser ──────────────────────────────────────────────────────────────────


def test_parse_question_and_grid() -> None:
    q, g = page_blocks.parse_blocks(PAGE)
    assert q.block_id == "budget"
    f = q.card_fields()
    assert f["response_type"] == "long-text"
    assert f["question"] == "How much do you want to spend each month?"
    assert f["title"] == f["question"]
    assert f["context"] == "For Adam. This tells us who you staff."
    assert f["category"] == "Questions for you"

    gf = g.card_fields()
    assert gf["response_type"] == "grid"
    assert gf["title"] == "Who owns each department"
    assert gf["block_config"] == {
        "rows": [
            {"key": "lead-response", "label": "Lead response"},
            {"key": "crm", "label": "CRM"},
        ],
        "columns": [
            {"key": "does-the-work", "label": "Does the work", "options": None},
            {
                "key": "how-it-s-staffed",
                "label": "How it's staffed",
                "options": ["Your team", "Outside help", "Open seat"],
            },
        ],
    }


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>nothing</p>", "no <pulse-question>"),
        ('<pulse-question id="Bad Id">Q?</pulse-question>', "id must be"),
        ('<pulse-question id="a">Q?</pulse-question><pulse-question id="a">R?</pulse-question>', "used twice"),
        ('<pulse-question id="a"></pulse-question>', "has no text"),
        ('<pulse-grid id="g"><pulse-row label="R"></pulse-row></pulse-grid>', "needs at least one"),
        ('<pulse-grid id="g"><pulse-row label="R"></pulse-row><pulse-row label="R"></pulse-row>'
         '<pulse-col label="C"></pulse-col></pulse-grid>', "same label"),
        ('<pulse-question id="a">Q?', "never closed"),
    ],
)
def test_parse_rejects_bad_pages(html: str, expected: str) -> None:
    with pytest.raises(page_blocks.PageBlockError) as exc:
        page_blocks.parse_blocks(html)
    assert expected in str(exc.value)


def test_clean_answer_validates_grid_cells() -> None:
    _, g = page_blocks.parse_blocks(PAGE)
    card = {"response_type": "grid", "block_config": g.card_fields()["block_config"]}
    ok = page_blocks.clean_answer(
        card, {"cells": {"crm": {"how-it-s-staffed": "Open seat", "does-the-work": " "}}}
    )
    assert ok == {"cells": {"crm": {"how-it-s-staffed": "Open seat"}}}
    assert page_blocks.clean_answer(card, {"cells": {}}) is None
    for bad in (
        {"cells": {"nope": {}}},
        {"cells": {"crm": {"nope": "x"}}},
        {"cells": {"crm": {"how-it-s-staffed": "Freelancer"}}},
        {"cells": {"crm": {"does-the-work": 3}}},
        {"text": "x"},
    ):
        with pytest.raises(ValueError):
            page_blocks.clean_answer(card, bad)


def test_deck_url_points_at_the_page_when_set() -> None:
    base = settings.frontend_base_url.rstrip("/")
    assert reminders.deck_url("abc") == f"{base}/?t=abc"
    assert reminders.deck_url("abc", "https://doc.example.com/#top") == (
        "https://doc.example.com/?t=abc"
    )
    assert reminders.deck_url("abc", "https://doc.example.com/p?x=1") == (
        "https://doc.example.com/p?x=1&t=abc"
    )


def test_origin_patterns() -> None:
    pats = ["https://*.quickhost.ing", "http://localhost:8765"]
    assert origin_allowed("https://avenus.quickhost.ing", pats)
    assert origin_allowed("http://localhost:8765", pats)
    assert not origin_allowed("https://quickhost.ing", pats)
    assert not origin_allowed("https://evil.com/.quickhost.ing", pats)
    assert not origin_allowed("http://avenus.quickhost.ing", pats)
    assert not origin_allowed("https://a.quickhost.ing.evil.com", pats)


# ── sync ────────────────────────────────────────────────────────────────────


async def _sync(admin: AsyncClient, eid: str, html: str = PAGE, **extra):
    return await admin.post(
        f"/api/admin/engagements/{eid}/page", json={"html": html, **extra}
    )


async def test_sync_creates_then_updates_in_place(
    admin_authed: AsyncClient, db: AsyncSession, seed_client: dict[str, str]
) -> None:
    eid = seed_client["id"]
    r = await _sync(admin_authed, eid, page_url="https://doc.example.com/")
    assert r.status_code == 200, r.text
    assert r.json() == {"created": ["budget", "ownership"], "updated": [], "missing": []}

    detail = (await admin_authed.get(f"/api/admin/engagements/{eid}")).json()
    assert detail["engagement"]["page_url"] == "https://doc.example.com/"
    grid = next(c for c in detail["cards"] if c["block_id"] == "ownership")
    assert grid["response_type"] == "grid"

    reworded = PAGE.replace("each month?", "per month?").replace(
        '<pulse-grid id="ownership"', '<pulse-grid id="ownership-x"'
    )
    r2 = await _sync(admin_authed, eid, reworded)
    assert r2.json() == {
        "created": ["ownership-x"],
        "updated": ["budget"],
        "missing": ["ownership"],
    }
    q = (
        await db.execute(
            text(
                "select question from public.cards where engagement_id = cast(:e as uuid) "
                "and block_id = 'budget'"
            ),
            {"e": eid},
        )
    ).scalar_one()
    assert q == "How much do you want to spend per month?"
    audit = (
        await db.execute(
            text(
                "select count(*) from public.audit_logs where action = 'engagement.page_sync' "
                "and target_id = :e"
            ),
            {"e": eid},
        )
    ).scalar_one()
    assert audit == 2


async def test_sync_rejects_bad_page_and_type_changes(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    eid = seed_client["id"]
    bad = await _sync(admin_authed, eid, "<p>no blocks</p>")
    assert bad.status_code == 400
    await _sync(admin_authed, eid)
    flipped = PAGE.replace('<pulse-grid id="ownership"', '<pulse-grid id="budget-grid"').replace(
        '<pulse-question id="budget"', '<pulse-question id="ownership"'
    )
    r = await _sync(admin_authed, eid, flipped)
    assert r.status_code == 400
    assert "changed from grid to long-text" in r.json()["detail"]
    nope = await admin_authed.post(
        f"/api/admin/engagements/{uuid.uuid4()}/page", json={"html": PAGE}
    )
    assert nope.status_code == 404
    bad_url = await _sync(admin_authed, eid, page_url="javascript:alert(1)")
    assert bad_url.status_code == 422


async def test_invite_links_to_the_page(
    admin_authed: AsyncClient, seed_client: dict[str, str], captured_emails: list
) -> None:
    eid = seed_client["id"]
    await _sync(admin_authed, eid, page_url="https://doc.example.com/")
    rec = (
        await admin_authed.post(
            f"/api/admin/engagements/{eid}/recipients", json={"email": "adam@example.com"}
        )
    ).json()
    r = await admin_authed.post(
        f"/api/admin/engagements/{eid}/invites", json={"recipient_ids": [rec["id"]]}
    )
    assert r.status_code == 200
    assert f"https://doc.example.com/?t={rec['token']}" in captured_emails[0].body


async def test_page_url_editable_and_clearable(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    url = f"/api/admin/engagements/{seed_client['id']}"
    r = await admin_authed.patch(url, json={"page_url": "https://doc.example.com/"})
    assert r.json()["page_url"] == "https://doc.example.com/"
    r = await admin_authed.patch(url, json={"page_url": ""})
    assert r.json()["page_url"] is None


# ── embed API ───────────────────────────────────────────────────────────────


async def test_embed_load_and_save(
    admin_authed: AsyncClient, db: AsyncSession, seed_client: dict[str, str]
) -> None:
    await _sync(admin_authed, seed_client["id"])
    headers = {"X-Pulse-Token": seed_client["token"]}

    blocks = (await admin_authed.get("/api/embed/blocks", headers=headers)).json()
    assert set(blocks["blocks"]) == {"budget", "ownership"}
    assert blocks["blocks"]["budget"] == {
        "type": "long-text",
        "state": "not_started",
        "value": None,
    }

    r = await admin_authed.post(
        "/api/embed/responses",
        headers=headers,
        json={"block_id": "budget", "value": {"text": "  $15k a month  "}},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"state": "answered", "value": {"text": "$15k a month"}}

    grid = {"cells": {"crm": {"how-it-s-staffed": "Open seat"}}}
    r = await admin_authed.post(
        "/api/embed/responses", headers=headers, json={"block_id": "ownership", "value": grid}
    )
    assert r.json() == {"state": "answered", "value": grid}

    after = (await admin_authed.get("/api/embed/blocks", headers=headers)).json()
    assert after["blocks"]["ownership"]["value"] == grid

    # Clearing an answer stops it counting as answered.
    r = await admin_authed.post(
        "/api/embed/responses",
        headers=headers,
        json={"block_id": "budget", "value": {"text": ""}},
    )
    assert r.json() == {"state": "viewed", "value": None}

    active = (
        await db.execute(
            text("select last_active_at from public.recipients where id = cast(:r as uuid)"),
            {"r": seed_client["recipient_id"]},
        )
    ).scalar_one()
    assert active is not None


async def test_embed_rejects_bad_values_and_unknown_blocks(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    await _sync(admin_authed, seed_client["id"])
    headers = {"X-Pulse-Token": seed_client["token"]}
    bad = await admin_authed.post(
        "/api/embed/responses",
        headers=headers,
        json={"block_id": "ownership", "value": {"cells": {"crm": {"how-it-s-staffed": "Nope"}}}},
    )
    assert bad.status_code == 422
    missing = await admin_authed.post(
        "/api/embed/responses", headers=headers, json={"block_id": "zzz", "value": {"text": "x"}}
    )
    assert missing.status_code == 404


async def test_embed_needs_a_valid_token(client: AsyncClient) -> None:
    r = await client.get("/api/embed/blocks", headers={"X-Pulse-Token": "0" * 16})
    assert r.status_code in (401, 404)


async def test_embed_cannot_see_another_engagements_blocks(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
) -> None:
    await _sync(admin_authed, seed_client["id"])
    headers = {"X-Pulse-Token": other_seeded_client["token"]}
    r = await admin_authed.get("/api/embed/blocks", headers=headers)
    assert r.json()["blocks"] == {}
    save = await admin_authed.post(
        "/api/embed/responses", headers=headers, json={"block_id": "budget", "value": {"text": "x"}}
    )
    assert save.status_code == 404


# ── CORS scope ──────────────────────────────────────────────────────────────


async def test_embed_cors_only_for_allowed_origins_and_embed_paths(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "embed_allowed_origins", "https://*.quickhost.ing")
    pre = {
        "Origin": "https://avenus.quickhost.ing",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-pulse-token",
    }
    ok = await client.options("/api/embed/responses", headers=pre)
    assert ok.status_code == 204
    assert ok.headers["access-control-allow-origin"] == "https://avenus.quickhost.ing"
    assert "access-control-allow-credentials" not in ok.headers

    other = await client.options("/api/embed/responses", headers={**pre, "Origin": "https://evil.com"})
    assert other.headers.get("access-control-allow-origin") is None

    admin = await client.options("/api/admin/engagements", headers=pre)
    assert admin.headers.get("access-control-allow-origin") is None

    simple = await client.get(
        "/api/embed/blocks",
        headers={"Origin": "https://avenus.quickhost.ing", "X-Pulse-Token": "0" * 16},
    )
    assert simple.headers["access-control-allow-origin"] == "https://avenus.quickhost.ing"
