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


def test_grid_changes_merge_cell_by_cell() -> None:
    _, g = page_blocks.parse_blocks(PAGE)
    card = {"response_type": "grid", "block_config": g.card_fields()["block_config"]}
    change = page_blocks.clean_change(
        card, {"cells": {"crm": {"how-it-s-staffed": "Open seat", "does-the-work": " Pat "}}}
    )
    assert change == {"cells": {"crm": {"how-it-s-staffed": "Open seat", "does-the-work": "Pat"}}}
    current = {"cells": {"lead-response": {"does-the-work": "Sam"}}}
    merged = page_blocks.apply_change(card, current, change)
    assert merged == {
        "cells": {
            "lead-response": {"does-the-work": "Sam"},
            "crm": {"how-it-s-staffed": "Open seat", "does-the-work": "Pat"},
        }
    }
    # "" clears a cell; clearing the last one empties the answer.
    cleared = page_blocks.apply_change(
        card, {"cells": {"crm": {"does-the-work": "Pat"}}},
        page_blocks.clean_change(card, {"cells": {"crm": {"does-the-work": ""}}}),
    )
    assert cleared is None
    for bad in (
        {"cells": {"nope": {}}},
        {"cells": {"crm": {"nope": "x"}}},
        {"cells": {"crm": {"how-it-s-staffed": "Freelancer"}}},
        {"cells": {"crm": {"does-the-work": 3}}},
        {"text": "x"},
    ):
        with pytest.raises(ValueError):
            page_blocks.clean_change(card, bad)


def test_question_changes_replace_the_text() -> None:
    card = {"response_type": "long-text"}
    assert page_blocks.apply_change(card, {"text": "old"}, {"text": "new"}) == {"text": "new"}
    assert page_blocks.apply_change(card, {"text": "old"}, {"text": ""}) is None
    with pytest.raises(ValueError):
        page_blocks.clean_change(card, {"text": "x" * 20_001})


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


# ── shared answers ──────────────────────────────────────────────────────────


@pytest.fixture
def shared_admin_session(db_conn, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind the embed API's BYPASSRLS session to the test connection. It runs
    inside the request (not a detached task), so it must not take
    ``db_conn_lock`` (the anon session holds it); it switches to the owner
    role for its queries and puts the request's role back afterwards."""
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from pulse_api.routes import embed

    @asynccontextmanager
    async def _override():
        prev = (await db_conn.execute(text("select current_user"))).scalar_one()
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(bind=db_conn, expire_on_commit=False, class_=AsyncSession)
        try:
            async with factory() as session:
                yield session
        finally:
            if prev in ("pulse_anon", "pulse_member"):
                await db_conn.execute(text(f"set local role {prev}"))

    monkeypatch.setattr(embed, "_admin_session", _override)


async def _shared_engagement(
    admin: AsyncClient, db: AsyncSession, seed_client: dict[str, str]
) -> dict[str, str]:
    """Synced PAGE in shared mode with two respondents, Adam (seeded) and Chachi."""
    eid = seed_client["id"]
    await _sync(admin, eid)
    r = await admin.patch(f"/api/admin/engagements/{eid}", json={"shared_answers": True})
    assert r.json()["shared_answers"] is True
    await db.execute(
        text("update public.recipients set name = 'Adam' where id = cast(:r as uuid)"),
        {"r": seed_client["recipient_id"]},
    )
    chachi = (
        await admin.post(
            f"/api/admin/engagements/{eid}/recipients",
            json={"email": "chachi@example.com", "name": "Chachi"},
        )
    ).json()
    return {"eid": eid, "adam": seed_client["token"], "chachi": chachi["token"]}


async def _save(admin: AsyncClient, token: str, block: str, value: dict):
    r = await admin.post(
        "/api/embed/responses",
        headers={"X-Pulse-Token": token},
        json={"block_id": block, "value": value},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_shared_answers_overwrite_and_log_every_change(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    shared_admin_session: None,
) -> None:
    e = await _shared_engagement(admin_authed, db, seed_client)

    await _save(admin_authed, e["adam"], "budget", {"text": "$10k"})
    out = await _save(admin_authed, e["chachi"], "budget", {"text": "$15k"})
    assert out["value"] == {"text": "$15k"}
    assert out["updated_by"] == "Chachi"

    # Different rows of the grid, edited by different people, both survive.
    await _save(admin_authed, e["adam"], "ownership", {"cells": {"crm": {"does-the-work": "Pat"}}})
    await _save(
        admin_authed,
        e["chachi"],
        "ownership",
        {"cells": {"lead-response": {"how-it-s-staffed": "Open seat"}}},
    )

    # Adam's page shows Chachi's overwrite and the merged grid.
    seen = (
        await admin_authed.get("/api/embed/blocks", headers={"X-Pulse-Token": e["adam"]})
    ).json()
    assert seen["shared"] is True
    assert seen["blocks"]["budget"]["value"] == {"text": "$15k"}
    assert seen["blocks"]["budget"]["updated_by"] == "Chachi"
    assert seen["blocks"]["ownership"]["value"] == {
        "cells": {
            "crm": {"does-the-work": "Pat"},
            "lead-response": {"how-it-s-staffed": "Open seat"},
        }
    }

    # Admin: current answers plus the full trail, newest first.
    detail = (await admin_authed.get(f"/api/admin/engagements/{e['eid']}")).json()
    assert len(detail["shared_answers"]) == 2
    trail = [(ev["actor_label"], ev["change"]) for ev in detail["answer_events"]]
    assert trail == [
        ("Chachi", {"cells": {"lead-response": {"how-it-s-staffed": "Open seat"}}}),
        ("Adam", {"cells": {"crm": {"does-the-work": "Pat"}}}),
        ("Chachi", {"text": "$15k"}),
        ("Adam", {"text": "$10k"}),
    ]
    # Shared mode doesn't write per-respondent responses.
    assert detail["responses"] == []

    # Clearing logs too, and the answer disappears.
    cleared = await _save(admin_authed, e["adam"], "budget", {"text": ""})
    assert cleared["state"] == "viewed"
    detail = (await admin_authed.get(f"/api/admin/engagements/{e['eid']}")).json()
    assert len(detail["shared_answers"]) == 1
    assert detail["answer_events"][0]["change"] == {"text": ""}

    # Reset clears answers and history.
    r = await admin_authed.post(f"/api/admin/engagements/{e['eid']}/reset")
    assert r.status_code == 200
    detail = (await admin_authed.get(f"/api/admin/engagements/{e['eid']}")).json()
    assert detail["shared_answers"] == [] and detail["answer_events"] == []


async def test_removed_respondent_stays_in_the_log(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    shared_admin_session: None,
) -> None:
    e = await _shared_engagement(admin_authed, db, seed_client)
    await _save(admin_authed, e["chachi"], "budget", {"text": "$15k"})
    rid = (
        await db.execute(
            text("select id::text from public.recipients where token = :t"), {"t": e["chachi"]}
        )
    ).scalar_one()
    await admin_authed.delete(f"/api/admin/engagements/{e['eid']}/recipients/{rid}")
    detail = (await admin_authed.get(f"/api/admin/engagements/{e['eid']}")).json()
    assert detail["answer_events"][0]["actor_label"] == "Chachi"
    assert detail["answer_events"][0]["recipient_id"] is None
    assert detail["shared_answers"][0]["value"] == {"text": "$15k"}


async def test_shared_tables_are_org_scoped(
    db: AsyncSession, db_conn, seed_client: dict[str, str], seed_cards: list[dict[str, str]]
) -> None:
    """Members see their own org's shared answers and trail, never another's."""
    from tests.conftest import become_member

    params = {
        "c": seed_cards[0]["id"],
        "e": seed_client["id"],
        "o": seed_client["org_id"],
    }
    await db.execute(
        text(
            "insert into public.shared_answers (card_id, engagement_id, org_id, value) "
            "values (cast(:c as uuid), cast(:e as uuid), cast(:o as uuid), '{}'::jsonb)"
        ),
        params,
    )
    await db.execute(
        text(
            "insert into public.answer_events (card_id, engagement_id, org_id, actor_label) "
            "values (cast(:c as uuid), cast(:e as uuid), cast(:o as uuid), 'X')"
        ),
        params,
    )
    await become_member(db_conn, org_id=str(uuid.uuid4()))
    for table in ("shared_answers", "answer_events"):
        assert (await db.execute(text(f"select count(*) from public.{table}"))).scalar_one() == 0
    await become_member(db_conn, org_id=seed_client["org_id"])
    for table in ("shared_answers", "answer_events"):
        assert (await db.execute(text(f"select count(*) from public.{table}"))).scalar_one() == 1


async def test_list_progress_counts_shared_answers(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    shared_admin_session: None,
) -> None:
    e = await _shared_engagement(admin_authed, db, seed_client)

    def row(rows):
        return next(r for r in rows if r["id"] == e["eid"])

    await _save(admin_authed, e["chachi"], "budget", {"text": "$15k"})
    r = row((await admin_authed.get("/api/admin/engagements")).json())
    assert (r["answered_responses"], r["completed_recipients"]) == (1, 0)

    await _save(admin_authed, e["adam"], "ownership", {"cells": {"crm": {"does-the-work": "Pat"}}})
    r = row((await admin_authed.get("/api/admin/engagements")).json())
    assert (r["answered_responses"], r["completed_recipients"]) == (2, r["recipients_count"])
