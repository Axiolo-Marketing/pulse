"""Phase 2 ("Generation engine") tests for reactive cards.

Covers `pulse_api.reactive` end to end: the full gate chain (deployment
key/flag, org flag, engagement flag, depth-1 loop guard, per-recipient
cap, dedup claim), the Anthropic call + server-side output validation,
cost/usage recording, and the client-facing wiring (`POST /api/responses`
scheduling the background task, `GET /api/generations` poll surface,
`GET /api/cards` recipient scoping for a generated card).

The Anthropic HTTP call is mocked via `respx` at
`https://api.anthropic.com/v1/messages` — the `anthropic` SDK is
httpx-based, so this is a transport-level intercept regardless of which
`httpx.AsyncClient` instance the SDK constructs internally. Every test
in this file sets `settings.reactive_max_retries = 0` (via
`_enable_all_gates`) so a mocked 429/5xx response fails fast instead of
paying the SDK's real exponential-backoff sleeps.

`run_generation` opens its own BYPASSRLS `admin_engine` session outside
FastAPI's DI graph (it's scheduled via `BackgroundTasks` and runs after
the triggering request has already returned) via the `_admin_session()`
seam in `reactive.py`. Production's `admin_engine` is a real connection
pool built at import time from `settings.admin_database_url` /
`settings.database_url` — NOT the test database — so left unpatched,
every generated-card write in these tests would silently land on the
real dev DB and be invisible to the test's own rolled-back transaction.
The `_reactive_admin_session_via_db_conn` autouse fixture below
monkeypatches that seam to bind through `db_conn` instead, mirroring
`test_oauth_provider.py`'s `patched_oauth_sessions` fixture for the
identically-shaped OAuth provider seam.
"""
from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

from pulse_api import reactive
from pulse_api.config import settings
from pulse_api.repos import cards as cards_repo

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


# ── admin-session routing (see module docstring) ────────────────────────────


@pytest.fixture(autouse=True)
def _reactive_admin_session_via_db_conn(
    db_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def _override() -> AsyncIterator[AsyncSession]:
        # Whatever role a prior request in this test left the shared
        # connection in (pulse_anon, from `_override_anon_session`) —
        # reset to the owner role first, which bypasses RLS by
        # ownership, exactly like production's pulse_admin BYPASSRLS
        # session.
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    monkeypatch.setattr(reactive, "_admin_session", _override)


# ── helpers ──────────────────────────────────────────────────────────────────


async def _enable_all_gates(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    *,
    org_id: str,
    engagement_id: str,
    api_key: str = "sk-ant-test-key-00000000",
    fake_mode: bool = False,
) -> None:
    """Flip every reactive-cards gate on: settings (global flag,
    key/fake-mode, zero SDK retries for deterministic error-path tests)
    plus the org + engagement DB flags. Individual tests dial one gate
    back off to exercise it in isolation."""
    monkeypatch.setattr(settings, "reactive_cards_enabled", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "" if fake_mode else api_key)
    monkeypatch.setattr(settings, "reactive_fake_mode", fake_mode)
    monkeypatch.setattr(settings, "reactive_max_retries", 0)
    await db.execute(
        text(
            "update public.organizations set reactive_cards_allowed = true "
            "where id = cast(:o as uuid)"
        ),
        {"o": org_id},
    )
    await db.execute(
        text(
            "update public.engagements set reactive_cards_enabled = true "
            "where id = cast(:e as uuid)"
        ),
        {"e": engagement_id},
    )


def _stub_anthropic_success(
    respx_mock: respx.Router,
    *,
    needs_followup: bool,
    cards: list[dict] | None = None,
    input_tokens: int = 123,
    output_tokens: int = 45,
    model: str = "claude-opus-4-8",
) -> respx.Route:
    body = {
        "id": "msg_test123",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "needs_followup": needs_followup,
                        "reason": "test reason",
                        "cards": cards or [],
                    }
                ),
            }
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    return respx_mock.post(ANTHROPIC_MESSAGES_URL).mock(
        return_value=httpx.Response(200, json=body)
    )


def _stub_anthropic_stop_reason(
    respx_mock: respx.Router, *, stop_reason: str
) -> respx.Route:
    """`refusal`/`max_tokens` responses — content is irrelevant since
    `_call_anthropic` branches on `stop_reason` before reading content."""
    body = {
        "id": "msg_test123",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-8",
        "content": [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    return respx_mock.post(ANTHROPIC_MESSAGES_URL).mock(
        return_value=httpx.Response(200, json=body)
    )


def _stub_anthropic_status_error(
    respx_mock: respx.Router, *, status_code: int = 500
) -> respx.Route:
    return respx_mock.post(ANTHROPIC_MESSAGES_URL).mock(
        return_value=httpx.Response(
            status_code, json={"error": {"type": "api_error", "message": "boom"}}
        )
    )


def _stub_anthropic_timeout(respx_mock: respx.Router) -> respx.Route:
    return respx_mock.post(ANTHROPIC_MESSAGES_URL).mock(
        side_effect=httpx.ReadTimeout("timed out")
    )


def _confirm_edit_card_id(seed_cards: list[dict[str, str]]) -> str:
    return next(c["id"] for c in seed_cards if c["response_type"] == "confirm-edit")


async def _add_recipient(
    db: AsyncSession, *, engagement_id: str, org_id: str, email: str | None = None
) -> dict[str, str]:
    token = secrets.token_hex(8)
    row = (
        await db.execute(
            text(
                "insert into public.recipients (engagement_id, org_id, token, email) "
                "values (cast(:e as uuid), cast(:o as uuid), :t, :em) "
                "returning id::text, token"
            ),
            {"e": engagement_id, "o": org_id, "t": token, "em": email},
        )
    ).mappings().one()
    return dict(row)


async def _generation_rows_for_response(
    db: AsyncSession, response_id: str
) -> list[dict]:
    result = await db.execute(
        text(
            "select id::text, status, model, error, input_tokens, output_tokens, "
            "cost_usd, created_card_ids::text[] as created_card_ids, "
            "trigger_hash, response_id::text "
            "from public.card_generations where response_id = cast(:r as uuid) "
            "order by created_at"
        ),
        {"r": response_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def _ai_cards_for_recipient(db: AsyncSession, recipient_id: str) -> list[dict]:
    result = await db.execute(
        text(
            "select id::text, source, recipient_id::text, "
            "generated_from_response_id::text, response_type, category, title "
            "from public.cards where recipient_id = cast(:r as uuid) and source = 'ai'"
        ),
        {"r": recipient_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def _seed_answered_response(
    db: AsyncSession,
    *,
    card_id: str,
    engagement_id: str,
    recipient_id: str,
    org_id: str,
    response_value: dict,
) -> str:
    """Directly seed an `answered` `responses` row via raw SQL (owner
    role) — used instead of a real `client_authed.post` round trip when a
    test just needs a valid `response_id` to hang a seeded
    `card_generations` row off of. Going through the HTTP client here
    would flip `db_conn`'s effective role to `pulse_anon` for the rest of
    the test (SET LOCAL ROLE persists until the next explicit change),
    which would then break a subsequent direct write like
    `_seed_generation_row` (`pulse_anon` has no INSERT grant on
    `card_generations`). Returns the new response's id."""
    row = (
        await db.execute(
            text(
                "insert into public.responses "
                "(card_id, engagement_id, recipient_id, org_id, state, response_value) "
                "values (cast(:card as uuid), cast(:eng as uuid), cast(:rid as uuid), "
                "        cast(:org as uuid), 'answered', cast(:rv as jsonb)) "
                "returning id::text"
            ),
            {
                "card": card_id,
                "eng": engagement_id,
                "rid": recipient_id,
                "org": org_id,
                "rv": json.dumps(response_value),
            },
        )
    ).mappings().one()
    return row["id"]


async def _seed_generation_row(
    db: AsyncSession,
    *,
    org_id: str,
    engagement_id: str,
    recipient_id: str,
    response_id: str,
    card_id: str,
    trigger_hash: str,
    status: str = "completed",
    created_card_ids: list[str] | None = None,
) -> str:
    """Directly seed a `card_generations` row, bypassing `run_generation`
    entirely — used to simulate "this recipient already has N prior
    generation ATTEMPTS" (any status) without paying for a real (mocked)
    LLM round trip per prior attempt. Returns the new row's id."""
    row = (
        await db.execute(
            text(
                "insert into public.card_generations "
                "(org_id, engagement_id, recipient_id, response_id, card_id, "
                " trigger_hash, status, created_card_ids) "
                "values (cast(:org as uuid), cast(:eng as uuid), cast(:rid as uuid), "
                "        cast(:resp as uuid), cast(:card as uuid), :hash, :status, "
                "        cast(:ids as uuid[])) "
                "returning id::text"
            ),
            {
                "org": org_id,
                "eng": engagement_id,
                "rid": recipient_id,
                "resp": response_id,
                "card": card_id,
                "hash": trigger_hash,
                "status": status,
                "ids": created_card_ids or [],
            },
        )
    ).mappings().one()
    return row["id"]


async def _response_row(db: AsyncSession, response_id: str) -> dict | None:
    result = await db.execute(
        text(
            "select id::text, state, response_value, recipient_id::text "
            "from public.responses where id = cast(:r as uuid)"
        ),
        {"r": response_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def _audit_rows(db: AsyncSession, *, action: str, target_id: str) -> list[dict]:
    result = await db.execute(
        text(
            "select action, target_type, target_id, metadata "
            "from public.audit_logs where action = :a and target_id = :t"
        ),
        {"a": action, "t": target_id},
    )
    return [dict(r) for r in result.mappings().all()]


_CORRECTION_A = "Actually the number should be $59, not $49 — can you clarify what changed?"
_CORRECTION_A_RESPACED = "Actually  the number  should be $59, not $49 — can you clarify what changed?  "
_CORRECTION_B = "We actually need annual billing at $99/mo instead, starting next quarter."


def _followup_card_payload(title: str = "Clarify pricing change") -> dict:
    return {
        "category": "Pricing",
        "title": title,
        "context": "You corrected the price.",
        "question": "What changed?",
        "response_type": "short-text",
        "options": None,
        "skip_allowed": True,
    }


# ── happy path ───────────────────────────────────────────────────────────────


async def test_happy_path_generates_scoped_ai_card_and_records_ledger(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    route = _stub_anthropic_success(
        respx_mock, needs_followup=True, cards=[_followup_card_payload()]
    )

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    assert "card" not in r.json(), "internal 'card' metadata must not leak to the client"
    response_id = r.json()["id"]

    assert route.call_count == 1

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    gen = gens[0]
    assert gen["status"] == "completed"
    assert gen["model"] == "claude-opus-4-8"
    assert gen["input_tokens"] == 123
    assert gen["output_tokens"] == 45
    assert gen["cost_usd"] == Decimal("0.001740")  # (123*5.00 + 45*25.00) / 1e6
    assert len(gen["created_card_ids"]) == 1

    ai_cards = await _ai_cards_for_recipient(db, seed_client["recipient_id"])
    assert len(ai_cards) == 1
    card = ai_cards[0]
    assert card["id"] == gen["created_card_ids"][0]
    assert card["source"] == "ai"
    assert card["recipient_id"] == seed_client["recipient_id"]
    assert card["generated_from_response_id"] == response_id
    assert card["title"] == "Clarify pricing change"

    audit_rows = await _audit_rows(
        db, action="card.reactive_generate", target_id=seed_client["recipient_id"]
    )
    assert len(audit_rows) == 1
    assert audit_rows[0]["target_type"] == "recipient"
    assert audit_rows[0]["metadata"]["response_id"] == response_id
    assert audit_rows[0]["metadata"]["card_ids"] == gen["created_card_ids"]


# ── regression: trigger passed from the route, never a stale row read ──────


async def test_run_generation_uses_route_passed_trigger_not_stale_row(
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the production race that made reactive cards
    silently never fire on the view-then-answer path.

    `run_generation` used to derive its trigger by re-reading
    `response_value` off the `responses` row on its own fresh admin
    session. But `save_response` schedules it via `BackgroundTasks` after
    `await session.commit()` on the request's `pulse_anon` session — and
    that commit only ends the INNER SQLAlchemy transaction; the real
    Postgres COMMIT happens later, in `get_anon_session`'s teardown, which
    (on this stack) runs AFTER background tasks. In the view-then-answer
    flow the response row pre-exists from the `viewed` mark with
    `response_value = NULL`, so a `run_generation` that re-read the row
    could observe that stale NULL and silently skip generation even
    though the respondent's correction had already been saved and
    returned to them.

    This seeds exactly that stale state directly — a response row still
    sitting at `state='viewed'`, `response_value=NULL` — and calls
    `run_generation` with a valid `trigger_text` the way the route now
    does. Generation must still complete: the trigger comes from the
    argument, never from re-reading the row.
    """
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    card_id = _confirm_edit_card_id(seed_cards)

    # A response row that only ever recorded the `viewed` mark — mirrors
    # exactly what `responses_repo.mark_viewed` inserts: no
    # `response_value`, `state='viewed'`. Never updated with the
    # respondent's correction, which is the "stale/pre-save snapshot"
    # `run_generation` must NOT depend on.
    view_row = (
        await db.execute(
            text(
                "insert into public.responses "
                "(card_id, engagement_id, recipient_id, org_id, state, viewed_at) "
                "values (cast(:card as uuid), cast(:eng as uuid), cast(:rid as uuid), "
                "        cast(:org as uuid), 'viewed', now()) "
                "returning id::text"
            ),
            {
                "card": card_id,
                "eng": seed_client["id"],
                "rid": seed_client["recipient_id"],
                "org": seed_client["org_id"],
            },
        )
    ).mappings().one()
    response_id = view_row["id"]

    stale_row = await _response_row(db, response_id)
    assert stale_row is not None
    assert stale_row["state"] == "viewed"
    assert stale_row["response_value"] is None

    route = _stub_anthropic_success(
        respx_mock, needs_followup=True, cards=[_followup_card_payload()]
    )

    await reactive.run_generation(
        response_id=response_id,
        recipient_id=seed_client["recipient_id"],
        engagement_id=seed_client["id"],
        card_id=card_id,
        trigger_text=_CORRECTION_A,
    )

    assert route.call_count == 1
    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "completed"
    assert len(gens[0]["created_card_ids"]) == 1

    ai_cards = await _ai_cards_for_recipient(db, seed_client["recipient_id"])
    assert len(ai_cards) == 1

    # The seeded row itself was never touched — proving the completed
    # generation really did come from the passed-in `trigger_text`, not
    # from any update to the row's own `response_value`.
    untouched_row = await _response_row(db, response_id)
    assert untouched_row is not None
    assert untouched_row["state"] == "viewed"
    assert untouched_row["response_value"] is None


async def test_view_then_answer_sequence_generates_via_route(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route-level companion to the regression test above: drives the
    actual deck sequence (`POST /api/responses/view` — which creates the
    response row `run_generation` must not depend on the stale snapshot
    of — followed by `POST /api/responses` with the correction) through
    the real HTTP surface, and asserts a generation still completes."""
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    card_id = _confirm_edit_card_id(seed_cards)
    route = _stub_anthropic_success(
        respx_mock, needs_followup=True, cards=[_followup_card_payload()]
    )

    view_resp = await client_authed.post(
        "/api/responses/view", json={"card_id": card_id}
    )
    assert view_resp.status_code == 200

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": card_id,
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]
    assert route.call_count == 1

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "completed"
    assert len(gens[0]["created_card_ids"]) == 1


# ── needs_followup: false ────────────────────────────────────────────────────


async def test_needs_followup_false_marks_skipped_with_zero_cards(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_success(respx_mock, needs_followup=False, cards=[])

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "skipped"
    assert gens[0]["created_card_ids"] == []

    assert await _ai_cards_for_recipient(db, seed_client["recipient_id"]) == []


# ── failure paths ────────────────────────────────────────────────────────────


async def test_api_500_marks_failed_and_response_row_intact(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_status_error(respx_mock, status_code=500)

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "failed"
    assert gens[0]["error"]

    row = await _response_row(db, response_id)
    assert row is not None
    assert row["state"] == "answered"
    assert row["response_value"] == {"confirmed": False, "correction": _CORRECTION_A}


async def test_api_timeout_marks_failed_and_response_row_intact(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_timeout(respx_mock)

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "failed"

    row = await _response_row(db, response_id)
    assert row is not None and row["state"] == "answered"


async def test_stop_reason_refusal_marks_skipped(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_stop_reason(respx_mock, stop_reason="refusal")

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "skipped"
    # A refusal is still a real, billed-or-not response — usage recorded.
    assert gens[0]["input_tokens"] == 10
    assert gens[0]["output_tokens"] == 5

    row = await _response_row(db, response_id)
    assert row is not None and row["state"] == "answered"
    assert await _ai_cards_for_recipient(db, seed_client["recipient_id"]) == []


async def test_stop_reason_max_tokens_marks_failed(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_stop_reason(respx_mock, stop_reason="max_tokens")

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "failed"


async def test_asyncio_wait_for_timeout_guard_marks_failed(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hard-coded 90s `asyncio.wait_for` wall-clock guard around the
    whole Anthropic call — a distinct code path from the SDK's own
    per-request timeout. Monkeypatch `asyncio.wait_for` to raise
    instantly (closing the never-awaited coroutine cleanly) so this is
    deterministic and doesn't actually wait 90 real seconds."""
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )

    async def _instant_timeout(coro, timeout):  # noqa: ANN001
        coro.close()
        raise TimeoutError("simulated wall-clock timeout")

    monkeypatch.setattr(reactive.asyncio, "wait_for", _instant_timeout)

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "failed"
    assert "timed out" in gens[0]["error"]
    assert len(respx_mock.calls) == 0, "no real HTTP call should have been attempted"


# ── gates: each off individually -> no generation ───────────────────────────


async def test_gate_off_missing_api_key_no_generation(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "reactive_fake_mode", False)

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    assert await _generation_rows_for_response(db, response_id) == []
    assert await _ai_cards_for_recipient(db, seed_client["recipient_id"]) == []
    assert len(respx_mock.calls) == 0


async def test_gate_off_global_flag_no_generation(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    monkeypatch.setattr(settings, "reactive_cards_enabled", False)

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    assert await _generation_rows_for_response(db, response_id) == []
    assert len(respx_mock.calls) == 0


async def test_gate_off_org_not_allowed_no_generation(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    await db.execute(
        text(
            "update public.organizations set reactive_cards_allowed = false "
            "where id = cast(:o as uuid)"
        ),
        {"o": seed_client["org_id"]},
    )

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    # is_candidate (the cheap route check) doesn't know about org flags, so
    # this exercises run_generation's own fresh gate re-check.
    assert await _generation_rows_for_response(db, response_id) == []
    assert len(respx_mock.calls) == 0


async def test_gate_off_engagement_not_enabled_no_generation(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    await db.execute(
        text(
            "update public.engagements set reactive_cards_enabled = false "
            "where id = cast(:e as uuid)"
        ),
        {"e": seed_client["id"]},
    )

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    assert await _generation_rows_for_response(db, response_id) == []
    assert len(respx_mock.calls) == 0


async def test_run_generation_direct_call_re_checks_gates_bypassing_is_candidate(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run_generation` re-derives every gate itself rather than trusting
    the route's cheap `is_candidate` pre-check (settings can flip between
    schedule-time and execution-time). Proves those internal re-checks
    are independently effective by calling `run_generation` directly —
    bypassing `is_candidate`/the route's background-task scheduling
    entirely — with the deployment gates off, then on."""
    # Flags fully off so the route itself never schedules a background
    # task for this save — we drive run_generation ourselves below.
    monkeypatch.setattr(settings, "reactive_cards_enabled", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "reactive_fake_mode", False)

    card_id = _confirm_edit_card_id(seed_cards)
    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": card_id,
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]
    assert await _generation_rows_for_response(db, response_id) == []

    run_kwargs = dict(
        response_id=response_id,
        recipient_id=seed_client["recipient_id"],
        engagement_id=seed_client["id"],
        card_id=card_id,
        trigger_text=_CORRECTION_A,
    )

    await reactive.run_generation(**run_kwargs)  # global flag off
    assert await _generation_rows_for_response(db, response_id) == []

    monkeypatch.setattr(settings, "reactive_cards_enabled", True)
    await reactive.run_generation(**run_kwargs)  # key/fake_mode off
    assert await _generation_rows_for_response(db, response_id) == []

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-key")
    await reactive.run_generation(**run_kwargs)  # org/engagement flags still off
    assert await _generation_rows_for_response(db, response_id) == []

    # Positive control: flip every remaining gate on -> a real generation.
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_success(
        respx_mock, needs_followup=True, cards=[_followup_card_payload()]
    )
    await reactive.run_generation(**run_kwargs)
    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "completed"


# ── context-load retry (commit-visibility latency) ──────────────────────────


async def test_context_load_retries_then_succeeds(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_load_generation_context` returning `None` a few times (the
    outer-transaction commit-visibility window described in
    `run_generation`'s docstring) must not give up immediately —
    `run_generation` retries with a short sleep in between, up to
    `_CONTEXT_LOAD_MAX_ATTEMPTS` attempts, before proceeding once the
    context resolves."""
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_success(
        respx_mock, needs_followup=True, cards=[_followup_card_payload()]
    )

    real_load_context = reactive._load_generation_context
    calls = {"n": 0}

    async def _flaky_load_context(session, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return None
        return await real_load_context(session, **kwargs)

    monkeypatch.setattr(reactive, "_load_generation_context", _flaky_load_context)

    sleeps: list[float] = []

    async def _fast_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(reactive.asyncio, "sleep", _fast_sleep)

    card_id = _confirm_edit_card_id(seed_cards)
    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": card_id,
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    assert calls["n"] == 3
    assert sleeps == [reactive._CONTEXT_LOAD_RETRY_SECONDS] * 2

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "completed"


async def test_context_load_gives_up_silently_after_max_attempts(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the context never resolves (row genuinely missing, deleted
    engagement, etc.), `run_generation` gives up silently after
    `_CONTEXT_LOAD_MAX_ATTEMPTS` attempts — no exception, no generation
    row, no LLM call."""
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )

    calls = {"n": 0}

    async def _always_none(session, **kwargs):
        calls["n"] += 1
        return None

    monkeypatch.setattr(reactive, "_load_generation_context", _always_none)

    async def _instant_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(reactive.asyncio, "sleep", _instant_sleep)

    card_id = _confirm_edit_card_id(seed_cards)
    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": card_id,
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    assert calls["n"] == reactive._CONTEXT_LOAD_MAX_ATTEMPTS
    assert await _generation_rows_for_response(db, response_id) == []
    assert len(respx_mock.calls) == 0


# ── depth-1 guard ────────────────────────────────────────────────────────────


async def test_answering_ai_generated_card_never_triggers_generation(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    ai_card = await cards_repo.create_card(
        db,
        engagement_id=seed_client["id"],
        category="Clarification",
        title="Existing follow-up",
        context="ctx",
        question="q?",
        response_type="confirm-edit",
        options=None,
        default_value="the AI's statement",
        skip_allowed=True,
        attachment_path=None,
        org_id=seed_client["org_id"],
        recipient_id=seed_client["recipient_id"],
        source="ai",
    )
    assert ai_card is not None

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": ai_card["id"],
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    assert await _generation_rows_for_response(db, response_id) == []
    # Still exactly the one (pre-existing) AI card — no second-generation card.
    assert len(await _ai_cards_for_recipient(db, seed_client["recipient_id"])) == 1
    assert len(respx_mock.calls) == 0


# ── per-recipient cap ────────────────────────────────────────────────────────


async def test_per_recipient_cap_blocks_further_generation(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    monkeypatch.setattr(settings, "reactive_max_generated_per_recipient", 1)

    # Seed one PRIOR generation ATTEMPT for this recipient directly (owner
    # role — see `_seed_answered_response`'s docstring for why not a real
    # HTTP round trip): a `completed` generation from an earlier
    # correction, wired to the AI card it produced. The cap counts
    # `card_generations` rows (attempts), not `source='ai'` cards
    # directly — see the sibling test below for the case where the prior
    # attempt produced no card at all.
    short_text_card_id = next(
        c["id"] for c in seed_cards if c["response_type"] == "short-text"
    )
    prior_response_id = await _seed_answered_response(
        db,
        card_id=short_text_card_id,
        engagement_id=seed_client["id"],
        recipient_id=seed_client["recipient_id"],
        org_id=seed_client["org_id"],
        response_value={"text": "unrelated answer"},
    )

    existing_ai_card = await cards_repo.create_card(
        db,
        engagement_id=seed_client["id"],
        category="C",
        title="Already generated",
        context="ctx",
        question="q?",
        response_type="short-text",
        options=None,
        default_value=None,
        skip_allowed=True,
        attachment_path=None,
        org_id=seed_client["org_id"],
        recipient_id=seed_client["recipient_id"],
        source="ai",
    )
    assert existing_ai_card is not None
    await _seed_generation_row(
        db,
        org_id=seed_client["org_id"],
        engagement_id=seed_client["id"],
        recipient_id=seed_client["recipient_id"],
        response_id=prior_response_id,
        card_id=short_text_card_id,
        trigger_hash="seeded-prior-hash",
        created_card_ids=[existing_ai_card["id"]],
    )

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    assert await _generation_rows_for_response(db, response_id) == []
    ai_cards = await _ai_cards_for_recipient(db, seed_client["recipient_id"])
    assert len(ai_cards) == 1
    assert ai_cards[0]["id"] == existing_ai_card["id"]
    assert len(respx_mock.calls) == 0


async def test_per_recipient_cap_counts_skipped_attempts_not_just_created_cards(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the cost/DoS finding: a generation ATTEMPT that
    produced zero cards (the model said `needs_followup: false`, or every
    proposal failed server-side validation) must still count against the
    per-recipient lifetime cap. Counting only successfully-created
    `source='ai'` cards would let a caller keep submitting distinct
    corrections (each a fresh `trigger_hash`, so the dedup lock never
    engages) forever without ever hitting the cap, since most real
    corrections legitimately produce no follow-up (the system prompt's
    "prefer proposing nothing" bar)."""
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    monkeypatch.setattr(settings, "reactive_max_generated_per_recipient", 1)

    short_text_card_id = next(
        c["id"] for c in seed_cards if c["response_type"] == "short-text"
    )
    prior_response_id = await _seed_answered_response(
        db,
        card_id=short_text_card_id,
        engagement_id=seed_client["id"],
        recipient_id=seed_client["recipient_id"],
        org_id=seed_client["org_id"],
        response_value={"text": "unrelated answer"},
    )

    # A prior attempt that created NO card at all (skipped) — no
    # `source='ai'` card exists for this recipient anywhere yet.
    await _seed_generation_row(
        db,
        org_id=seed_client["org_id"],
        engagement_id=seed_client["id"],
        recipient_id=seed_client["recipient_id"],
        response_id=prior_response_id,
        card_id=short_text_card_id,
        trigger_hash="seeded-skipped-hash",
        status="skipped",
    )
    assert await _ai_cards_for_recipient(db, seed_client["recipient_id"]) == []

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    # Blocked by the cap: no new generation row, no LLM call, no card —
    # even though this recipient has zero AI cards to their name.
    assert await _generation_rows_for_response(db, response_id) == []
    assert await _ai_cards_for_recipient(db, seed_client["recipient_id"]) == []
    assert len(respx_mock.calls) == 0


# ── admin_engine pool discipline ─────────────────────────────────────────────


async def test_llm_call_holds_no_admin_session_open(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the pool-starvation finding: `admin_engine`
    (BYPASSRLS) is a small pool shared with MCP token verification, OAuth
    issuance, and superadmin routes for every org. This asserts zero
    `_admin_session()` contexts are open at the moment `_call_anthropic`
    runs — i.e. no phase of `run_generation` holds a db session/
    transaction checked out across the network I/O."""
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_success(
        respx_mock, needs_followup=True, cards=[_followup_card_payload()]
    )

    depth = {"n": 0}
    real_admin_session = reactive._admin_session

    @asynccontextmanager
    async def _tracking_admin_session() -> AsyncIterator[AsyncSession]:
        depth["n"] += 1
        try:
            async with real_admin_session() as session:
                yield session
        finally:
            depth["n"] -= 1

    monkeypatch.setattr(reactive, "_admin_session", _tracking_admin_session)

    observed: dict[str, int | None] = {"depth_during_call": None}
    real_call_anthropic = reactive._call_anthropic

    async def _wrapped_call_anthropic(**kwargs):
        observed["depth_during_call"] = depth["n"]
        return await real_call_anthropic(**kwargs)

    monkeypatch.setattr(reactive, "_call_anthropic", _wrapped_call_anthropic)

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200

    assert observed["depth_during_call"] == 0


# ── dedup ────────────────────────────────────────────────────────────────────


async def test_dedup_identical_normalized_correction_creates_one_generation(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    route = _stub_anthropic_success(
        respx_mock, needs_followup=True, cards=[_followup_card_payload()]
    )
    card_id = _confirm_edit_card_id(seed_cards)

    r1 = await client_authed.post(
        "/api/responses",
        json={
            "card_id": card_id,
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r1.status_code == 200
    response_id = r1.json()["id"]
    assert route.call_count == 1

    # Re-save with only whitespace differences — normalizes to the same
    # trigger text, so the (response_id, trigger_hash) dedup key matches
    # and no second LLM call / generation row is created.
    r2 = await client_authed.post(
        "/api/responses",
        json={
            "card_id": card_id,
            "state": "answered",
            "response_value": {
                "confirmed": False,
                "correction": _CORRECTION_A_RESPACED,
            },
        },
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == response_id  # same response row (upsert)
    assert route.call_count == 1, "identical correction must not re-trigger the LLM"

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1


async def test_dedup_changed_correction_creates_second_generation(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    route = _stub_anthropic_success(
        respx_mock, needs_followup=True, cards=[_followup_card_payload()]
    )
    card_id = _confirm_edit_card_id(seed_cards)

    r1 = await client_authed.post(
        "/api/responses",
        json={
            "card_id": card_id,
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    response_id = r1.json()["id"]
    assert route.call_count == 1

    r2 = await client_authed.post(
        "/api/responses",
        json={
            "card_id": card_id,
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_B},
        },
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == response_id
    assert route.call_count == 2, "a materially different correction must re-trigger"

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 2
    assert {g["status"] for g in gens} == {"completed"}
    assert gens[0]["trigger_hash"] != gens[1]["trigger_hash"]


# ── GET /api/generations recipient isolation ────────────────────────────────


async def test_get_generations_scoped_to_owning_recipient_not_sibling(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_success(
        respx_mock, needs_followup=True, cards=[_followup_card_payload()]
    )
    sibling = await _add_recipient(
        db,
        engagement_id=seed_client["id"],
        org_id=seed_client["org_id"],
        email="sibling@example.com",
    )

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    response_id = r.json()["id"]

    # Owning recipient sees the row.
    own = await client_authed.get("/api/generations", params={"response_id": response_id})
    assert own.status_code == 200
    body = own.json()
    assert len(body) == 1
    assert body[0]["response_id"] == response_id
    assert body[0]["status"] == "completed"
    assert len(body[0]["card_ids"]) == 1

    # Sibling recipient's token gets nothing back — even asking for the
    # exact same response_id explicitly.
    client_authed.headers["X-Pulse-Token"] = sibling["token"]
    theirs = await client_authed.get(
        "/api/generations", params={"response_id": response_id}
    )
    assert theirs.status_code == 200
    assert theirs.json() == []

    theirs_unfiltered = await client_authed.get("/api/generations")
    assert theirs_unfiltered.json() == []


# ── GET /api/cards recipient scoping for the generated card ─────────────────


async def test_get_cards_returns_generated_card_only_for_owning_recipient(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db, monkeypatch, org_id=seed_client["org_id"], engagement_id=seed_client["id"]
    )
    _stub_anthropic_success(
        respx_mock,
        needs_followup=True,
        cards=[_followup_card_payload(title="Unique AI Title 12345")],
    )
    sibling = await _add_recipient(
        db,
        engagement_id=seed_client["id"],
        org_id=seed_client["org_id"],
        email="sibling2@example.com",
    )

    await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )

    own_cards = await client_authed.get("/api/cards")
    own_titles = {c["title"] for c in own_cards.json()}
    assert "Unique AI Title 12345" in own_titles

    client_authed.headers["X-Pulse-Token"] = sibling["token"]
    sibling_cards = await client_authed.get("/api/cards")
    sibling_titles = {c["title"] for c in sibling_cards.json()}
    assert "Unique AI Title 12345" not in sibling_titles
    # Sibling still sees every shared (operator) card from seed_cards.
    assert len(sibling_cards.json()) == len(seed_cards)


# ── fake mode ────────────────────────────────────────────────────────────────


async def test_fake_mode_generates_cards_with_no_outbound_http(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_all_gates(
        db,
        monkeypatch,
        org_id=seed_client["org_id"],
        engagement_id=seed_client["id"],
        fake_mode=True,
    )
    assert settings.anthropic_api_key == ""

    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": _confirm_edit_card_id(seed_cards),
            "state": "answered",
            "response_value": {"confirmed": False, "correction": _CORRECTION_A},
        },
    )
    assert r.status_code == 200
    response_id = r.json()["id"]

    gens = await _generation_rows_for_response(db, response_id)
    assert len(gens) == 1
    assert gens[0]["status"] == "completed"
    assert gens[0]["model"] == "fake"
    assert gens[0]["cost_usd"] == Decimal("0")
    assert len(gens[0]["created_card_ids"]) >= 1

    ai_cards = await _ai_cards_for_recipient(db, seed_client["recipient_id"])
    assert len(ai_cards) >= 1
    assert len(respx_mock.calls) == 0, "fake mode must never hit the network"
