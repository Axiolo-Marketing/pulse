"""Completion alerts: the engagement owner is emailed when a respondent
finishes the deck (``pulse_api/completion.py``)."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from pulse_api import completion, reactive
from pulse_api.auth.email_messages import respondent_finished_email
from pulse_api.config import settings
from pulse_api.email import OutboundEmail

_real_schedule = completion.schedule_completion_check


@pytest.fixture(autouse=True)
def _completion_on_via_db_conn(
    db_conn: AsyncConnection,
    db_conn_lock: asyncio.Lock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Undo conftest's stub and bind the job's BYPASSRLS session to the
    rolled-back test connection (same seam as the reactive/transcription
    tests). Claim retries don't sleep."""

    @asynccontextmanager
    async def _override() -> AsyncIterator[AsyncSession]:
        async with db_conn_lock:
            await db_conn.execute(text("reset role"))
            factory = async_sessionmaker(
                bind=db_conn, expire_on_commit=False, class_=AsyncSession
            )
            async with factory() as session:
                yield session

    monkeypatch.setattr(completion, "schedule_completion_check", _real_schedule)
    monkeypatch.setattr(completion, "_admin_session", _override)
    monkeypatch.setattr(reactive, "_admin_session", _override)
    monkeypatch.setattr(completion, "_CLAIM_RETRY_SECONDS", 0)


async def _add_card(
    db: AsyncSession, *, engagement_id: str, org_id: str, idx: int, rt: str = "short-text"
) -> str:
    return (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, org_id) "
                "values (cast(:e as uuid), :i, 'Test', :t, 'ctx', 'q?', :rt, "
                "        cast(:o as uuid)) returning id::text"
            ),
            {"e": engagement_id, "i": idx, "t": f"Card {idx}", "rt": rt, "o": org_id},
        )
    ).scalar_one()


@pytest.fixture
async def deck(
    db: AsyncSession, seed_client: dict[str, str], seed_admin_user: dict[str, str]
) -> dict:
    """Two-card engagement owned by the seeded admin, with a named respondent."""
    eid, org = seed_client["id"], seed_client["org_id"]
    await db.execute(
        text(
            "update public.engagements set created_by = cast(:u as uuid), "
            "engagement_name = '2027 Revenue Plan' where id = cast(:e as uuid)"
        ),
        {"u": seed_admin_user["id"], "e": eid},
    )
    await db.execute(
        text(
            "update public.recipients set email = 'jane@client.test', name = 'Jane' "
            "where id = cast(:r as uuid)"
        ),
        {"r": seed_client["recipient_id"]},
    )
    cards = [
        await _add_card(db, engagement_id=eid, org_id=org, idx=1),
        await _add_card(db, engagement_id=eid, org_id=org, idx=2, rt="confirm-edit"),
    ]
    return {**seed_client, "cards": cards, "owner_email": seed_admin_user["email"]}


async def _save(client: AsyncClient, card_id: str, state: str = "answered", value=None):
    resp = await client.post(
        "/api/responses",
        json={
            "card_id": card_id,
            "state": state,
            "response_value": value if value is not None else {"text": "Grow ARR."},
        },
    )
    assert resp.status_code == 200, resp.text
    await completion.wait_for_pending_checks()


async def test_owner_emailed_once_when_respondent_finishes(
    client_authed: AsyncClient, deck: dict, captured_emails: list[OutboundEmail]
) -> None:
    await _save(client_authed, deck["cards"][0])
    assert captured_emails == []

    await _save(client_authed, deck["cards"][1], value={"confirmed": True})
    assert len(captured_emails) == 1
    sent = captured_emails[0]
    assert sent.to == deck["owner_email"]
    assert sent.subject == "Jane finished 2027 Revenue Plan"
    assert "Jane (jane@client.test) just finished 2027 Revenue Plan for Renee" in sent.body
    assert "(2 answered)" in sent.body
    assert f"/admin/#/client/{deck['id']}" in sent.body

    # Editing an answer afterwards doesn't re-alert.
    await _save(client_authed, deck["cards"][0], value={"text": "Changed my mind."})
    assert len(captured_emails) == 1


async def test_skips_count_as_finished(
    client_authed: AsyncClient, deck: dict, captured_emails: list[OutboundEmail]
) -> None:
    await _save(client_authed, deck["cards"][0], state="skipped", value={})
    await _save(client_authed, deck["cards"][1], value={"confirmed": True})
    assert len(captured_emails) == 1
    assert "(1 answered, 1 skipped)" in captured_emails[0].body


async def test_viewing_cards_is_not_finishing(
    client_authed: AsyncClient, deck: dict, captured_emails: list[OutboundEmail]
) -> None:
    await _save(client_authed, deck["cards"][0])
    await _save(client_authed, deck["cards"][1], state="viewed", value={})
    assert captured_emails == []


async def test_new_card_after_alert_realerts_when_finished_again(
    db: AsyncSession,
    client_authed: AsyncClient,
    deck: dict,
    captured_emails: list[OutboundEmail],
) -> None:
    for c in deck["cards"]:
        await _save(client_authed, c, value={"confirmed": True, "text": "ok"})
    assert len(captured_emails) == 1

    extra = await _add_card(
        db, engagement_id=deck["id"], org_id=deck["org_id"], idx=3
    )
    # `now()` is frozen at the test transaction's start; in production the
    # card's own insert is naturally later than the alert.
    await db.execute(
        text("update public.cards set created_at = clock_timestamp() where id = cast(:c as uuid)"),
        {"c": extra},
    )
    await _save(client_authed, extra)
    assert len(captured_emails) == 2


async def test_falls_back_to_org_owners_without_a_creator(
    db: AsyncSession,
    client_authed: AsyncClient,
    deck: dict,
    captured_emails: list[OutboundEmail],
) -> None:
    await db.execute(
        text("update public.engagements set created_by = null where id = cast(:e as uuid)"),
        {"e": deck["id"]},
    )
    for c in deck["cards"]:
        await _save(client_authed, c, value={"confirmed": True, "text": "ok"})
    assert deck["owner_email"] in [e.to for e in captured_emails]


async def test_creator_who_left_the_org_is_not_emailed(
    db: AsyncSession,
    client_authed: AsyncClient,
    deck: dict,
    captured_emails: list[OutboundEmail],
) -> None:
    ex = (
        await db.execute(
            text(
                "insert into public.users (email, name) "
                "values ('former@example.com', 'Former') returning id::text"
            )
        )
    ).scalar_one()
    await db.execute(
        text("update public.engagements set created_by = cast(:u as uuid) where id = cast(:e as uuid)"),
        {"u": ex, "e": deck["id"]},
    )
    for c in deck["cards"]:
        await _save(client_authed, c, value={"confirmed": True, "text": "ok"})
    recipients = [e.to for e in captured_emails]
    assert "former@example.com" not in recipients
    assert deck["owner_email"] in recipients


async def _enable_reactive(
    db: AsyncSession, deck: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "reactive_cards_enabled", True)
    monkeypatch.setattr(settings, "reactive_fake_mode", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    await db.execute(
        text("update public.organizations set reactive_cards_allowed = true where id = cast(:o as uuid)"),
        {"o": deck["org_id"]},
    )
    await db.execute(
        text("update public.engagements set reactive_cards_enabled = true where id = cast(:e as uuid)"),
        {"e": deck["id"]},
    )


_CORRECTION = {"confirmed": False, "correction": "It's actually 12 regions, not 8."}


async def test_ai_followup_on_last_card_defers_the_alert(
    db: AsyncSession,
    client_authed: AsyncClient,
    deck: dict,
    captured_emails: list[OutboundEmail],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Correcting the last card spawns AI follow-ups (fake mode), so the
    respondent isn't finished yet — the alert waits until they answer those."""
    await _enable_reactive(db, deck, monkeypatch)

    await _save(client_authed, deck["cards"][0])
    await _save(client_authed, deck["cards"][1], value=_CORRECTION)
    await reactive.wait_for_pending_generations()
    await completion.wait_for_pending_checks()
    assert captured_emails == []

    followups = (
        await db.execute(
            text(
                "select id::text from public.cards where engagement_id = cast(:e as uuid) "
                "and recipient_id = cast(:r as uuid)"
            ),
            {"e": deck["id"], "r": deck["recipient_id"]},
        )
    ).scalars().all()
    assert followups
    for card_id in followups:
        await _save(client_authed, card_id, value={"text": "12"})
    assert len(captured_emails) == 1


async def test_generation_that_adds_nothing_releases_the_alert(
    db: AsyncSession,
    client_authed: AsyncClient,
    deck: dict,
    captured_emails: list[OutboundEmail],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the last card's correction yields no follow-up, the generation's
    own completion check sends the alert (the route leaves it to it)."""
    await _enable_reactive(db, deck, monkeypatch)
    monkeypatch.setattr(
        reactive,
        "_fake_completion",
        lambda _t: reactive._LLMResult(
            status="skipped", model="fake", input_tokens=0, output_tokens=0
        ),
    )

    await _save(client_authed, deck["cards"][0])
    await _save(client_authed, deck["cards"][1], value=_CORRECTION)
    await reactive.wait_for_pending_generations()
    await completion.wait_for_pending_checks()
    assert len(captured_emails) == 1


async def _pending_generation(db: AsyncSession, deck: dict, *, age_minutes: int) -> str:
    """A `pending` generation row for the respondent's first card, as if an
    earlier correction's follow-up were still being generated. Written as
    the owner — the respondent's role can only read generations."""
    await db.execute(text("reset role"))
    return (
        await db.execute(
            text(
                "insert into public.card_generations "
                "(org_id, engagement_id, recipient_id, response_id, card_id, "
                " trigger_hash, created_at) "
                "select r.org_id, r.engagement_id, r.recipient_id, r.id, r.card_id, "
                "       'h', now() - make_interval(mins => :age) "
                "from public.responses r "
                "where r.card_id = cast(:c as uuid) and r.recipient_id = cast(:rid as uuid) "
                "returning id::text"
            ),
            {"c": deck["cards"][0], "rid": deck["recipient_id"], "age": age_minutes},
        )
    ).scalar_one()


async def test_pending_generation_from_earlier_save_holds_the_alert(
    db: AsyncSession,
    client_authed: AsyncClient,
    deck: dict,
    captured_emails: list[OutboundEmail],
) -> None:
    """The deck stopped waiting on an earlier card's follow-up and the
    respondent answered the rest: not finished until that generation lands."""
    await _save(client_authed, deck["cards"][0])
    gen = await _pending_generation(db, deck, age_minutes=0)

    await _save(client_authed, deck["cards"][1], value={"confirmed": True})
    assert captured_emails == []

    # The generation finishes without adding cards; its completion check
    # (what `reactive` schedules after every generation) now sends the alert.
    await db.execute(text("reset role"))
    await db.execute(
        text("update public.card_generations set status = 'skipped' where id = cast(:g as uuid)"),
        {"g": gen},
    )
    await completion.run_completion_check(deck["recipient_id"], retry=False)
    assert len(captured_emails) == 1


async def test_stale_pending_generation_does_not_hold_the_alert(
    db: AsyncSession,
    client_authed: AsyncClient,
    deck: dict,
    captured_emails: list[OutboundEmail],
) -> None:
    await _save(client_authed, deck["cards"][0])
    await _pending_generation(db, deck, age_minutes=11)

    await _save(client_authed, deck["cards"][1], value={"confirmed": True})
    assert len(captured_emails) == 1


async def test_alert_due_is_false_for_an_empty_deck(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    assert await completion.alert_due(db, seed_client["recipient_id"]) is False


async def test_run_completion_check_ignores_bad_ids(
    captured_emails: list[OutboundEmail],
) -> None:
    await completion.run_completion_check("not-a-uuid")
    assert captured_emails == []


def test_email_without_name_or_engagement_name() -> None:
    subject, body = respondent_finished_email(
        respondent_name=None,
        respondent_email="pat@client.test",
        engagement_name=None,
        client_name=None,
        answered=3,
        skipped=0,
        engagement_url="https://pulse.example/admin/#/client/x",
    )
    assert subject == "pat@client.test finished their Pulse deck"
    assert body.startswith("pat@client.test just finished their Pulse deck (3 answered).")
