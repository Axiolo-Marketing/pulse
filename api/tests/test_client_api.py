"""Endpoint tests for the client-facing API.

Every test relies on the conftest override of `get_anon_session`, which
flips the test's db_conn into `pulse_anon` mode and sets `pulse.token`
from the X-Pulse-Token header. Same path the production middleware takes,
so RLS policies are exercised on every read and write.

Isolation checks intentionally span two seeded clients: token A must
never see B's data, and B's writes must not be addressable by A.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ── /api/me ────────────────────────────────────────────────────────────────


async def test_me_returns_token_owner(client_authed: AsyncClient, seed_client: dict[str, str]) -> None:
    r = await client_authed.get("/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == seed_client["id"]
    assert body["name"] == seed_client["name"]


async def test_me_with_unknown_token_returns_404(client: AsyncClient) -> None:
    client.headers["X-Pulse-Token"] = "ffffffffffffffff"
    r = await client.get("/api/me")
    assert r.status_code == 404


async def test_me_voice_enabled_defaults_false(
    client_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    """A fresh engagement has voice off; the deck reads the flag here."""
    r = await client_authed.get("/api/me")
    assert r.status_code == 200
    assert r.json()["voice_enabled"] is False


async def test_me_reflects_voice_enabled_when_on(
    client_authed: AsyncClient, seed_client: dict[str, str], db: AsyncSession
) -> None:
    # The shared test connection may be left in the pulse_anon role by a
    # prior client request (which has no UPDATE grant on clients); reset to
    # the owner role before seeding the flag, like _set_voice_enabled does.
    await db.execute(text("reset role"))
    await db.execute(
        text(
            "update public.engagements set voice_enabled = true "
            "where id = cast(:cid as uuid)"
        ),
        {"cid": seed_client["id"]},
    )
    r = await client_authed.get("/api/me")
    assert r.status_code == 200
    assert r.json()["voice_enabled"] is True


# ── /api/cards ─────────────────────────────────────────────────────────────


async def test_cards_returns_only_my_clients_cards(
    client_authed: AsyncClient,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    db: AsyncSession,
) -> None:
    # seed_cards inserts 8 cards for seed_client. Add one for the other
    # client too — it must not appear in the response.
    await db.execute(
        text(
            "insert into public.cards "
            "(engagement_id, order_index, category, title, context, question, "
            " response_type, org_id) "
            "values (cast(:cid as uuid), 99, 'C', 'Other card', 'X', 'Q', "
            "        'short-text', cast(:o as uuid))"
        ),
        {"cid": other_seeded_client["id"], "o": other_seeded_client["org_id"]},
    )

    r = await client_authed.get("/api/cards")
    assert r.status_code == 200
    titles = {c["title"] for c in r.json()}
    assert "Other card" not in titles
    assert len(r.json()) == 8  # exactly seed_cards' 8


async def test_cards_returns_empty_for_client_with_no_cards(
    client_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await client_authed.get("/api/cards")
    assert r.status_code == 200
    assert r.json() == []


async def test_cards_ordering_by_order_index(
    client_authed: AsyncClient,
    seed_client: dict[str, str],
    db: AsyncSession,
) -> None:
    # Insert out-of-order indices
    for idx in [3, 1, 2]:
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, org_id) "
                "values (cast(:cid as uuid), :i, 'C', :t, 'X', 'Q', "
                "        'short-text', cast(:o as uuid))"
            ),
            {
                "cid": seed_client["id"],
                "i": idx,
                "t": f"card-{idx}",
                "o": seed_client["org_id"],
            },
        )

    r = await client_authed.get("/api/cards")
    titles = [c["title"] for c in r.json()]
    assert titles == ["card-1", "card-2", "card-3"]


# ── POST /api/responses/view ───────────────────────────────────────────────


async def test_mark_viewed_inserts_a_row(
    client_authed: AsyncClient, seed_cards: list[dict[str, str]]
) -> None:
    card_id = seed_cards[0]["id"]
    r = await client_authed.post("/api/responses/view", json={"card_id": card_id})
    assert r.status_code == 200
    body = r.json()
    assert body["card_id"] == card_id
    assert body["state"] == "viewed"
    assert body["viewed_at"] is not None


async def test_mark_viewed_is_idempotent_and_preserves_answered_state(
    client_authed: AsyncClient, seed_cards: list[dict[str, str]]
) -> None:
    """If a card has already been answered, calling /view must NOT overwrite
    the state back to 'viewed' — that would clobber real user data."""
    card_id = seed_cards[0]["id"]

    # First answer the card
    r = await client_authed.post(
        "/api/responses",
        json={"card_id": card_id, "state": "answered", "response_value": {"text": "x"}},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "answered"

    # Then mark viewed — must be a no-op
    r = await client_authed.post("/api/responses/view", json={"card_id": card_id})
    assert r.status_code == 200
    assert r.json()["state"] == "answered"  # NOT 'viewed'


async def test_mark_viewed_rejects_unknown_card(client_authed: AsyncClient) -> None:
    bogus = str(uuid.uuid4())
    r = await client_authed.post("/api/responses/view", json={"card_id": bogus})
    assert r.status_code == 404


async def test_mark_viewed_cannot_target_other_clients_card(
    client_authed: AsyncClient,
    other_seeded_client: dict[str, str],
    db: AsyncSession,
) -> None:
    """A client posting with a card_id belonging to another client must 404.
    The card_id field is in the request body, so this is the place a hostile
    client would try to address rows it doesn't own."""
    row = (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, org_id) "
                "values (cast(:cid as uuid), 1, 'C', 'theirs', 'X', 'Q', "
                "        'short-text', cast(:o as uuid)) "
                "returning id::text"
            ),
            {"cid": other_seeded_client["id"], "o": other_seeded_client["org_id"]},
        )
    ).mappings().one()

    r = await client_authed.post("/api/responses/view", json={"card_id": row["id"]})
    assert r.status_code == 404


# ── POST /api/responses (the main save) — parametrized per response type ──


RESPONSE_TYPE_FIXTURES = [
    ("confirm-edit",  {"confirmed": True}),
    ("confirm-edit",  {"confirmed": False, "correction": "fix me"}),
    ("single-select", {"selected": "A"}),
    ("multi-select",  {"selected": ["A", "B"]}),
    ("short-text",    {"text": "hello"}),
    ("long-text",     {"text": "world"}),
    ("document-link", {"url": "https://example.com"}),
    ("contact-share", {"name": "x", "email": "x@y.z", "role": "founder"}),
]


@pytest.mark.parametrize("response_type, payload", RESPONSE_TYPE_FIXTURES)
async def test_save_response_per_type(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
    response_type: str,
    payload: dict,
) -> None:
    card = next(c for c in seed_cards if c["response_type"] == response_type)
    r = await client_authed.post(
        "/api/responses",
        json={"card_id": card["id"], "state": "answered", "response_value": payload},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "answered"
    assert body["response_value"] == payload
    assert body["answered_at"] is not None


async def test_save_response_upsert_overwrites_existing(
    client_authed: AsyncClient, seed_cards: list[dict[str, str]]
) -> None:
    card_id = seed_cards[0]["id"]
    r1 = await client_authed.post(
        "/api/responses",
        json={"card_id": card_id, "state": "answered", "response_value": {"text": "first"}},
    )
    r2 = await client_authed.post(
        "/api/responses",
        json={"card_id": card_id, "state": "answered", "response_value": {"text": "second"}},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]  # same row
    assert r2.json()["response_value"] == {"text": "second"}


async def test_save_response_skipped_state(
    client_authed: AsyncClient, seed_cards: list[dict[str, str]]
) -> None:
    card_id = seed_cards[0]["id"]
    r = await client_authed.post(
        "/api/responses",
        json={"card_id": card_id, "state": "skipped"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "skipped"


@pytest.mark.parametrize("bad_state", ["invalid", "ANSWERED", "", "deleted"])
async def test_save_response_rejects_invalid_state(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
    bad_state: str,
) -> None:
    r = await client_authed.post(
        "/api/responses",
        json={"card_id": seed_cards[0]["id"], "state": bad_state},
    )
    assert r.status_code == 422


async def test_save_response_rejects_unknown_card(client_authed: AsyncClient) -> None:
    r = await client_authed.post(
        "/api/responses",
        json={"card_id": str(uuid.uuid4()), "state": "answered", "response_value": {"text": "x"}},
    )
    assert r.status_code == 404


# ── POST /api/responses — url scheme validation (audit M5/F2) ──────────────
#
# The operator's admin console later renders any `response_value.url` as a
# clickable <a href=...>. HTML-escaping neutralizes markup but not the URL
# *scheme*, so a hostile client could otherwise smuggle a `javascript:`/
# `data:` link into storage for the operator to click. The server must
# enforce the same http/https-only rule the deck UI applies client-side
# (`isValidUrl` in `src/lib/render.ts`), since a direct API call bypasses it.


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(document.cookie)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "//evil.example.com/payload",
    ],
)
async def test_save_response_rejects_unsafe_url_schemes(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
    bad_url: str,
) -> None:
    card = next(c for c in seed_cards if c["response_type"] == "document-link")
    r = await client_authed.post(
        "/api/responses",
        json={"card_id": card["id"], "state": "answered", "response_value": {"url": bad_url}},
    )
    assert r.status_code == 400

    # Nothing was stored: the card has no saved response at all.
    listed = await client_authed.get("/api/responses")
    assert not any(row["card_id"] == card["id"] for row in listed.json())


async def test_save_response_accepts_valid_https_url(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
) -> None:
    card = next(c for c in seed_cards if c["response_type"] == "document-link")
    r = await client_authed.post(
        "/api/responses",
        json={
            "card_id": card["id"],
            "state": "answered",
            "response_value": {"url": "https://example.com"},
        },
    )
    assert r.status_code == 200
    assert r.json()["response_value"] == {"url": "https://example.com"}


async def test_save_response_without_url_key_unaffected(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
) -> None:
    """Response types with no `url` key (e.g. short-text) are untouched by
    the URL-scheme check -- it must only fire when `url` is present."""
    card = next(c for c in seed_cards if c["response_type"] == "short-text")
    r = await client_authed.post(
        "/api/responses",
        json={"card_id": card["id"], "state": "answered", "response_value": {"text": "hello"}},
    )
    assert r.status_code == 200
    assert r.json()["response_value"] == {"text": "hello"}


# ── /api/responses (list) ──────────────────────────────────────────────────


async def test_list_responses_returns_own_only(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
) -> None:
    # Save responses for two cards
    for c in seed_cards[:2]:
        await client_authed.post(
            "/api/responses",
            json={"card_id": c["id"], "state": "answered", "response_value": {"text": "x"}},
        )

    r = await client_authed.get("/api/responses")
    assert r.status_code == 200
    assert len(r.json()) == 2


# ── /api/uploads (list) ────────────────────────────────────────────────────


async def test_list_uploads_returns_empty_for_new_client(
    client_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await client_authed.get("/api/uploads")
    assert r.status_code == 200
    assert r.json() == []


# ── PATCH /api/me/heartbeat ────────────────────────────────────────────────


async def test_heartbeat_updates_last_active_at(
    client_authed: AsyncClient,
    seed_client: dict[str, str],
    db: AsyncSession,
) -> None:
    # Capture initial value BEFORE the API call (which switches role to anon).
    # Post-0015 last_active_at lives on the token-bound recipient row.
    initial = (
        await db.execute(
            text(
                "select last_active_at from public.recipients "
                "where id = cast(:i as uuid)"
            ),
            {"i": seed_client["recipient_id"]},
        )
    ).scalar()
    assert initial is None

    r = await client_authed.patch("/api/me/heartbeat")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    # After the API call we're now in pulse_anon mode; the token's
    # column-scoped grant on recipients lets us SELECT and see the updated
    # row through the policy.
    new = (
        await db.execute(
            text("select last_active_at from public.recipients limit 1")
        )
    ).scalar()
    assert new is not None


# ── Auth gate (missing token) across every client-facing endpoint ──────────


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET",   "/api/me",             None),
        ("PATCH", "/api/me/heartbeat",   None),
        ("GET",   "/api/cards",          None),
        ("GET",   "/api/responses",      None),
        ("POST",  "/api/responses/view", {"card_id": str(uuid.uuid4())}),
        ("POST",  "/api/responses",      {"card_id": str(uuid.uuid4()), "state": "answered"}),
        ("GET",   "/api/uploads",        None),
    ],
)
async def test_endpoint_requires_token(
    client: AsyncClient, method: str, path: str, body: dict | None
) -> None:
    r = await client.request(method, path, json=body)
    assert r.status_code == 401
