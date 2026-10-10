"""Outbound webhook (Reba signals): owner settings + signed respondent events
(``pulse_api/webhooks.py``, ``PUT/DELETE /api/orgs/me/webhook``)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from pulse_api import completion, reactive, webhooks
from pulse_api.audit import AUDIT_ACTIONS
from pulse_api.auth.session import encode_session
from pulse_api.config import settings
from tests.test_org_branding import _seed_member_caller

pytestmark = pytest.mark.respx(assert_all_called=False)

HOOK_URL = "https://reba.test/signals/pulse"
SECRET = "whsec-test-0123456789abcdef"

_real = {
    name: getattr(webhooks, name)
    for name in ("schedule_deck_opened", "schedule_answer", "schedule_deck_completed")
}
_real_completion = completion.schedule_completion_check


@pytest.fixture(autouse=True)
def _webhooks_on_via_db_conn(
    db_conn: AsyncConnection,
    db_conn_lock: asyncio.Lock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Undo conftest's stubs and bind the jobs' BYPASSRLS sessions to the
    rolled-back test connection. Retries don't sleep."""

    @asynccontextmanager
    async def _override() -> AsyncIterator[AsyncSession]:
        async with db_conn_lock:
            await db_conn.execute(text("reset role"))
            factory = async_sessionmaker(
                bind=db_conn, expire_on_commit=False, class_=AsyncSession
            )
            async with factory() as session:
                yield session

    for name, fn in _real.items():
        monkeypatch.setattr(webhooks, name, fn)
    monkeypatch.setattr(webhooks, "_admin_session", _override)
    monkeypatch.setattr(webhooks, "_RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(webhooks, "_LOAD_RETRY_SECONDS", 0)
    monkeypatch.setattr(completion, "schedule_completion_check", _real_completion)
    monkeypatch.setattr(completion, "_admin_session", _override)
    monkeypatch.setattr(reactive, "_admin_session", _override)
    monkeypatch.setattr(completion, "_CLAIM_RETRY_SECONDS", 0)


# ── helpers ───────────────────────────────────────────────────────────────


async def _configure(db: AsyncSession, org_id: str) -> None:
    await db.execute(
        text(
            "update public.organizations set webhook_url = :u, webhook_secret = :s "
            "where id = cast(:o as uuid)"
        ),
        {"u": HOOK_URL, "s": SECRET, "o": org_id},
    )


def _verify(request: httpx.Request) -> dict:
    """Assert the request is signed per the contract; return its body."""
    assert request.headers["content-type"] == "application/json"
    header = request.headers[webhooks.SIGNATURE_HEADER]
    parts = dict(p.split("=", 1) for p in header.split(","))
    t = int(parts["t"])
    assert abs(time.time() - t) < 60
    expected = hmac.new(
        SECRET.encode(), f"{t}.".encode() + request.content, hashlib.sha256
    ).hexdigest()
    assert hmac.compare_digest(parts["v1"], expected)
    return json.loads(request.content)


def _bodies(route: respx.Route) -> list[dict]:
    return [_verify(call.request) for call in route.calls]


async def _add_card(
    db: AsyncSession,
    *,
    engagement_id: str,
    org_id: str,
    idx: int,
    rt: str,
    title: str,
    default_value: str | None = None,
) -> str:
    return (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, default_value, org_id) "
                "values (cast(:e as uuid), :i, 'Test', :t, 'ctx', 'q?', :rt, :d, "
                "        cast(:o as uuid)) returning id::text"
            ),
            {
                "e": engagement_id,
                "i": idx,
                "t": title,
                "rt": rt,
                "d": default_value,
                "o": org_id,
            },
        )
    ).scalar_one()


@pytest.fixture
async def deck(db: AsyncSession, seed_client: dict[str, str]) -> dict:
    eid, org = seed_client["id"], seed_client["org_id"]
    await db.execute(
        text(
            "update public.engagements set engagement_name = 'Growth Brief' "
            "where id = cast(:e as uuid)"
        ),
        {"e": eid},
    )
    await db.execute(
        text(
            "update public.recipients set email = 'Jane@Client.test', name = 'Jane' "
            "where id = cast(:r as uuid)"
        ),
        {"r": seed_client["recipient_id"]},
    )
    cards = {
        "confirm": await _add_card(
            db, engagement_id=eid, org_id=org, idx=1, rt="confirm-edit",
            title="Who you sell to", default_value="You sell to dental practices.",
        ),
        "single": await _add_card(
            db, engagement_id=eid, org_id=org, idx=2, rt="single-select",
            title="Main channel",
        ),
        "contact": await _add_card(
            db, engagement_id=eid, org_id=org, idx=3, rt="contact-share",
            title="Who else should see this",
        ),
    }
    return {**seed_client, "cards": cards}


async def _save(client: AsyncClient, card_id: str, value: dict, state: str = "answered"):
    resp = await client.post(
        "/api/responses",
        json={"card_id": card_id, "state": state, "response_value": value},
    )
    assert resp.status_code == 200, resp.text
    await completion.wait_for_pending_checks()
    await webhooks.wait_for_pending_deliveries()
    return resp.json()


async def _updated_epoch(db: AsyncSession, response_id: str) -> int:
    return (
        await db.execute(
            text(
                "select floor(extract(epoch from updated_at))::bigint from public.responses "
                "where id = cast(:i as uuid)"
            ),
            {"i": response_id},
        )
    ).scalar_one()


# ── settings ──────────────────────────────────────────────────────────────


def test_webhook_update_is_a_known_audit_action() -> None:
    assert "org.webhook_update" in AUDIT_ACTIONS


async def test_owner_sets_webhook_secret_never_returned(
    admin_authed: AsyncClient, db: AsyncSession, seed_admin_user: dict[str, str]
) -> None:
    r = await admin_authed.put(
        "/api/orgs/me/webhook", json={"url": HOOK_URL, "secret": SECRET}
    )
    assert r.status_code == 200, r.text
    assert r.json()["webhook_url"] == HOOK_URL
    assert r.json()["webhook_secret_set"] is True
    assert SECRET not in r.text

    got = await admin_authed.get("/api/orgs/me")
    assert got.json()["webhook_secret_set"] is True
    assert SECRET not in got.text

    stored = (
        await db.execute(
            text(
                "select webhook_url, webhook_secret from public.organizations "
                "where id = cast(:o as uuid)"
            ),
            {"o": seed_admin_user["org_id"]},
        )
    ).mappings().one()
    assert stored["webhook_url"] == HOOK_URL
    assert stored["webhook_secret"] == SECRET

    audit = (
        await db.execute(
            text(
                "select user_id::text as user_id, target_type, metadata "
                "from public.audit_logs where org_id = cast(:o as uuid) "
                "  and action = 'org.webhook_update'"
            ),
            {"o": seed_admin_user["org_id"]},
        )
    ).mappings().all()
    assert len(audit) == 1
    assert audit[0]["user_id"] == seed_admin_user["id"]
    assert audit[0]["target_type"] == "org"
    assert audit[0]["metadata"]["new_url"] == HOOK_URL
    assert audit[0]["metadata"]["secret_set"] is True
    assert SECRET not in json.dumps(audit[0]["metadata"])

    feed = await admin_authed.get("/api/orgs/me/activity")
    assert feed.status_code == 200
    assert SECRET not in feed.text


async def test_change_url_keeps_secret_then_clear(
    admin_authed: AsyncClient, db: AsyncSession, seed_admin_user: dict[str, str]
) -> None:
    # No secret stored yet: one is required.
    r = await admin_authed.put("/api/orgs/me/webhook", json={"url": HOOK_URL})
    assert r.status_code == 400

    await admin_authed.put("/api/orgs/me/webhook", json={"url": HOOK_URL, "secret": SECRET})
    r = await admin_authed.put(
        "/api/orgs/me/webhook", json={"url": "https://reba.test/other"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["webhook_url"] == "https://reba.test/other"
    assert r.json()["webhook_secret_set"] is True

    r = await admin_authed.delete("/api/orgs/me/webhook")
    assert r.status_code == 200, r.text
    assert r.json()["webhook_url"] is None
    assert r.json()["webhook_secret_set"] is False
    stored = (
        await db.execute(
            text(
                "select webhook_url, webhook_secret from public.organizations "
                "where id = cast(:o as uuid)"
            ),
            {"o": seed_admin_user["org_id"]},
        )
    ).mappings().one()
    assert stored["webhook_url"] is None and stored["webhook_secret"] is None
    count = (
        await db.execute(
            text(
                "select count(*) from public.audit_logs where org_id = cast(:o as uuid) "
                "and action = 'org.webhook_update'"
            ),
            {"o": seed_admin_user["org_id"]},
        )
    ).scalar_one()
    assert count == 3


@pytest.mark.parametrize(
    "body",
    [
        {"url": "http://reba.test/hook", "secret": SECRET},
        {"url": "not a url", "secret": SECRET},
        {"url": HOOK_URL, "secret": "short"},
    ],
    ids=["http", "garbage", "short-secret"],
)
async def test_webhook_validation(admin_authed: AsyncClient, body: dict) -> None:
    r = await admin_authed.put("/api/orgs/me/webhook", json=body)
    assert r.status_code == 422


async def test_members_cannot_set_or_clear_webhook(
    client: AsyncClient, db: AsyncSession, axiolo_org: dict[str, str]
) -> None:
    user_id = await _seed_member_caller(db, org_id=axiolo_org["id"], role="member")
    await db.flush()
    client.cookies.set(
        settings.session_cookie_name, encode_session(user_id, axiolo_org["id"])
    )
    r = await client.put("/api/orgs/me/webhook", json={"url": HOOK_URL, "secret": SECRET})
    assert r.status_code == 403
    r = await client.delete("/api/orgs/me/webhook")
    assert r.status_code == 403


# ── events ────────────────────────────────────────────────────────────────


async def test_deck_opened_once_per_recipient(
    client_authed: AsyncClient,
    db: AsyncSession,
    deck: dict,
    respx_mock: respx.Router,
) -> None:
    await _configure(db, deck["org_id"])
    route = respx_mock.post(HOOK_URL).mock(return_value=httpx.Response(200))

    for _ in range(2):
        r = await client_authed.get("/api/me")
        assert r.status_code == 200
        await webhooks.wait_for_pending_deliveries()

    bodies = _bodies(route)
    assert len(bodies) == 1
    body = bodies[0]
    assert body["id"] == f"opened:{deck['recipient_id']}"
    assert body["kind"] == "deck_opened"
    assert body["engagement_id"] == deck["id"]
    assert body["email"] == "jane@client.test"
    assert body["title"] == "Growth Brief"
    assert body["occurred_at"].endswith("Z")
    assert "first_opened_at" not in r.text


async def test_no_send_when_unconfigured_but_open_is_still_claimed(
    client_authed: AsyncClient,
    db: AsyncSession,
    deck: dict,
    respx_mock: respx.Router,
) -> None:
    route = respx_mock.post(HOOK_URL).mock(return_value=httpx.Response(200))
    await client_authed.get("/api/me")
    await webhooks.wait_for_pending_deliveries()
    await _save(client_authed, deck["cards"]["single"], {"selected": "Referrals"})
    assert route.call_count == 0
    opened = (
        await db.execute(
            text(
                "select first_opened_at from public.recipients where id = cast(:r as uuid)"
            ),
            {"r": deck["recipient_id"]},
        )
    ).scalar_one()
    assert opened is not None

    # Configuring a webhook afterwards doesn't replay the old open.
    await _configure(db, deck["org_id"])
    await client_authed.get("/api/me")
    await webhooks.wait_for_pending_deliveries()
    assert route.call_count == 0


async def test_confirmed_confirm_edit_sends_the_statement(
    client_authed: AsyncClient, db: AsyncSession, deck: dict, respx_mock: respx.Router
) -> None:
    await _configure(db, deck["org_id"])
    route = respx_mock.post(HOOK_URL).mock(return_value=httpx.Response(200))
    saved = await _save(client_authed, deck["cards"]["confirm"], {"confirmed": True})

    (body,) = _bodies(route)
    epoch = await _updated_epoch(db, saved["id"])
    assert body["id"] == f"answered:{saved['id']}:{epoch}"
    assert body["kind"] == "deck_answered"
    assert body["fact"] == {
        "text": "You sell to dental practices.",
        "source": "your answers: Who you sell to",
    }
    assert body["confirmed"] is True
    assert "corrects" not in body
    assert body["email"] == "jane@client.test"
    assert body["engagement_id"] == deck["id"]


async def test_corrected_confirm_edit_sends_the_correction(
    client_authed: AsyncClient, db: AsyncSession, deck: dict, respx_mock: respx.Router
) -> None:
    await _configure(db, deck["org_id"])
    route = respx_mock.post(HOOK_URL).mock(return_value=httpx.Response(200))
    await _save(
        client_authed,
        deck["cards"]["confirm"],
        {"confirmed": False, "correction": "Dental and orthodontic practices."},
    )
    (body,) = _bodies(route)
    assert body["fact"] == {
        "text": "Dental and orthodontic practices.",
        "source": "your answers: Who you sell to",
    }
    assert body["confirmed"] is False
    assert body["corrects"] == "You sell to dental practices."


async def test_select_card_and_viewed_then_answered(
    client_authed: AsyncClient, db: AsyncSession, deck: dict, respx_mock: respx.Router
) -> None:
    """A card viewed first (row already committed) still reports the answer
    once the answer's own commit lands; viewing alone sends nothing."""
    await _configure(db, deck["org_id"])
    route = respx_mock.post(HOOK_URL).mock(return_value=httpx.Response(200))
    await client_authed.post("/api/responses/view", json={"card_id": deck["cards"]["single"]})
    await _save(client_authed, deck["cards"]["single"], {}, state="viewed")
    assert route.call_count == 0

    await _save(
        client_authed, deck["cards"]["single"], {"selected": "Referrals", "note": "Mostly"}
    )
    (body,) = _bodies(route)
    assert body["kind"] == "deck_answered"
    assert body["fact"] == {
        "text": "Main channel: Referrals. Note: Mostly",
        "source": "your answers: Main channel",
    }
    assert "confirmed" not in body


async def test_skips_send_nothing(
    client_authed: AsyncClient, db: AsyncSession, deck: dict, respx_mock: respx.Router
) -> None:
    await _configure(db, deck["org_id"])
    route = respx_mock.post(HOOK_URL).mock(return_value=httpx.Response(200))
    await _save(client_authed, deck["cards"]["single"], {}, state="skipped")
    assert route.call_count == 0


async def test_contact_shared_body(
    client_authed: AsyncClient, db: AsyncSession, deck: dict, respx_mock: respx.Router
) -> None:
    await _configure(db, deck["org_id"])
    route = respx_mock.post(HOOK_URL).mock(return_value=httpx.Response(200))
    saved = await _save(
        client_authed,
        deck["cards"]["contact"],
        {"name": "Sam Lee", "email": "Sam@Client.test", "role": "COO"},
    )
    (body,) = _bodies(route)
    epoch = await _updated_epoch(db, saved["id"])
    assert body["id"] == f"contact:{saved['id']}:{epoch}"
    assert body["kind"] == "contact_shared"
    assert body["contact"] == {"email": "sam@client.test", "name": "Sam Lee", "company": "COO"}
    assert "fact" not in body


async def test_deck_completed_on_claim(
    client_authed: AsyncClient,
    db: AsyncSession,
    deck: dict,
    respx_mock: respx.Router,
    captured_emails: list,
) -> None:
    await _configure(db, deck["org_id"])
    route = respx_mock.post(HOOK_URL).mock(return_value=httpx.Response(200))
    await _save(client_authed, deck["cards"]["confirm"], {"confirmed": True})
    await _save(client_authed, deck["cards"]["single"], {"selected": "Referrals"})
    assert not [b for b in _bodies(route) if b["kind"] == "deck_completed"]
    await _save(client_authed, deck["cards"]["contact"], {}, state="skipped")

    completed = [b for b in _bodies(route) if b["kind"] == "deck_completed"]
    assert len(completed) == 1
    claimed = (
        await db.execute(
            text(
                "select floor(extract(epoch from completed_notified_at))::bigint "
                "from public.recipients where id = cast(:r as uuid)"
            ),
            {"r": deck["recipient_id"]},
        )
    ).scalar_one()
    assert completed[0]["id"] == f"completed:{deck['recipient_id']}:{claimed}"
    assert completed[0]["title"] == "Growth Brief"
    assert completed[0]["email"] == "jane@client.test"


async def test_retries_on_5xx_then_succeeds(
    client_authed: AsyncClient, db: AsyncSession, deck: dict, respx_mock: respx.Router
) -> None:
    await _configure(db, deck["org_id"])
    route = respx_mock.post(HOOK_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectError("boom"),
            httpx.Response(200),
        ]
    )
    await _save(client_authed, deck["cards"]["single"], {"selected": "Referrals"})
    bodies = _bodies(route)  # every attempt is signed
    assert len(bodies) == 3
    assert len({b["id"] for b in bodies}) == 1


async def test_gives_up_after_retries_and_skips_4xx(respx_mock: respx.Router) -> None:
    route = respx_mock.post(HOOK_URL).mock(return_value=httpx.Response(500))
    assert await webhooks.deliver(HOOK_URL, SECRET, {"id": "x"}) is False
    assert route.call_count == 4

    route4 = respx_mock.post("https://reba.test/4xx").mock(return_value=httpx.Response(401))
    assert await webhooks.deliver("https://reba.test/4xx", SECRET, {"id": "y"}) is False
    assert route4.call_count == 1


# ── pure helpers ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "card, value, expected",
    [
        ({"title": "T", "response_type": "file-upload"}, {"file_ids": ["a"]}, None),
        ({"title": "T", "response_type": "document-link"}, {"url": "https://x"}, None),
        ({"title": "T", "response_type": "short-text"}, {"text": "  "}, None),
        ({"title": "T", "response_type": "confirm-edit"}, {"confirmed": False}, None),
        ({"title": "T", "response_type": "contact-share"}, {"name": "No email"}, None),
        (
            {"title": "T", "response_type": "long-text"},
            {"text": "We grew 20%."},
            ("deck_answered", {"fact": {"text": "T: We grew 20%.", "source": "your answers: T"}}),
        ),
        (
            {"title": "T", "response_type": "multi-select"},
            {"selected": ["A", "B"]},
            ("deck_answered", {"fact": {"text": "T: A, B", "source": "your answers: T"}}),
        ),
        (
            {"title": "T", "response_type": "single-select"},
            {"note": "Only a note"},
            ("deck_answered", {"fact": {"text": "T: Only a note", "source": "your answers: T"}}),
        ),
        (
            {"title": "T", "response_type": "confirm-edit", "default_value": None},
            {"confirmed": True},
            (
                "deck_answered",
                {"fact": {"text": "T: confirmed", "source": "your answers: T"}, "confirmed": True},
            ),
        ),
        (
            {"title": "T", "response_type": "contact-share"},
            {"email": "a@b.test", "name": "A", "company": "Acme", "role": "CEO"},
            ("contact_shared", {"contact": {"email": "a@b.test", "name": "A", "company": "Acme"}}),
        ),
    ],
)
def test_answer_fields(card: dict, value: dict, expected) -> None:
    assert webhooks.answer_fields(card, value) == expected


def test_sign_matches_contract() -> None:
    body = b'{"id":"x"}'
    header = webhooks.sign("k" * 16, body, 1700000000)
    mac = hmac.new(b"k" * 16, b"1700000000." + body, hashlib.sha256).hexdigest()
    assert header == f"t=1700000000,v1={mac}"


async def test_jobs_ignore_bad_ids() -> None:
    await webhooks.run_deck_opened("not-a-uuid")
    await webhooks.run_answer("not-a-uuid", None)  # type: ignore[arg-type]
