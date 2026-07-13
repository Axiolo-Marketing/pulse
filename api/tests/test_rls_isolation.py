"""Multi-tenant isolation tests.

These run direct SQL — no FastAPI in the middle — so they prove the
database-layer backstop, not just the application logic. If these regress,
the multi-tenant security model is broken regardless of how the API behaves.

The two load-bearing properties:
1. `pulse_anon` with no `pulse.token` set sees zero rows across every
   RLS-protected table.
2. `pulse_anon` with a valid token sees only the matching engagement's
   rows — never the other engagement's.

The tests seed data as the superuser, then call `become_anon()` to switch
the open transaction's effective role to `pulse_anon`. The seed data is
visible to the post-switch queries (same transaction), and RLS then
filters it the same way it would on a real production connection.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tests.conftest import become_anon, become_member

RLS_TABLES = ["engagements", "cards", "responses", "uploads"]


async def _seed_full_set(
    db: AsyncSession,
    engagement_id: str,
    label: str,
    *,
    org_id: str,
) -> None:
    """Insert one card + one response + one upload for the given engagement.

    ``org_id`` is NOT NULL on every tenant-scoped table (migration 0005);
    we thread it through explicitly so the seed doesn't rely on the
    ``responses`` / ``uploads`` GUC default.
    """
    await db.execute(
        text(
            "insert into public.cards "
            "(engagement_id, order_index, category, title, context, question, "
            " response_type, org_id) "
            "values (cast(:cid as uuid), 1, 'C', :t, 'X', 'Q', 'short-text', "
            "        cast(:o as uuid))"
        ),
        {"cid": engagement_id, "t": f"{label} card", "o": org_id},
    )
    await db.execute(
        text(
            "insert into public.responses "
            "(card_id, engagement_id, recipient_id, state, org_id) "
            "select id, engagement_id, "
            "  (select id from public.recipients "
            "     where engagement_id = cast(:cid as uuid) limit 1), "
            "  'answered', cast(:o as uuid) from public.cards "
            "where engagement_id = cast(:cid as uuid)"
        ),
        {"cid": engagement_id, "o": org_id},
    )
    await db.execute(
        text(
            "insert into public.uploads "
            "(card_id, engagement_id, recipient_id, file_name, file_size_bytes, "
            " storage_path, org_id) "
            "select id, engagement_id, "
            "  (select id from public.recipients "
            "     where engagement_id = cast(:cid as uuid) limit 1), "
            "  :fn, 100, 'x/y/z', cast(:o as uuid) "
            "from public.cards where engagement_id = cast(:cid as uuid)"
        ),
        {"cid": engagement_id, "fn": f"{label}.pdf", "o": org_id},
    )


@pytest.mark.parametrize("table", RLS_TABLES)
async def test_anon_with_no_token_sees_zero_rows(
    db: AsyncSession,
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
    table: str,
) -> None:
    await _seed_full_set(db, seed_client["id"], "renee", org_id=seed_client["org_id"])
    await _seed_full_set(db, other_seeded_client["id"], "josh", org_id=other_seeded_client["org_id"])

    await become_anon(db_conn)

    count = (await db_conn.execute(text(f"select count(*) from public.{table}"))).scalar()
    assert count == 0, f"pulse_anon saw rows in {table} with no token set"


@pytest.mark.parametrize("table", ["cards", "responses", "uploads"])
async def test_anon_with_token_sees_only_own_rows(
    db: AsyncSession,
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
    table: str,
) -> None:
    await _seed_full_set(db, seed_client["id"], "renee", org_id=seed_client["org_id"])
    await _seed_full_set(db, other_seeded_client["id"], "josh", org_id=other_seeded_client["org_id"])

    await become_anon(db_conn, token=seed_client["token"])

    count = (
        await db_conn.execute(text(f"select count(*) from public.{table}"))
    ).scalar()
    assert count == 1, f"expected 1 visible row in {table}; got {count}"

    visible_engagement_ids = [
        str(r[0])
        for r in (
            await db_conn.execute(text(f"select engagement_id from public.{table}"))
        ).all()
    ]
    assert visible_engagement_ids == [seed_client["id"]], (
        f"{table} leaked rows from another engagement: {visible_engagement_ids}"
    )


async def test_anon_with_unknown_token_sees_nothing(
    db: AsyncSession,
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
) -> None:
    await become_anon(db_conn, token="ffffffffffffffff")
    count = (await db_conn.execute(text("select count(*) from public.engagements"))).scalar()
    assert count == 0


async def test_two_recipients_on_one_engagement_are_isolated(
    db: AsyncSession,
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
) -> None:
    """The core multi-respondent property: two recipients who SHARE one
    engagement (and therefore its cards) each see ONLY their own answers,
    never the other respondent's. Recipient A is the one ``seed_client``
    minted; we add recipient B on the same engagement and give each its own
    response to the same shared card (the unique is ``(card_id,
    recipient_id)``, so both coexist). The RLS backstop — not the app — is
    what keeps them apart, so this runs direct SQL via ``become_anon``.
    """
    eid, org_id = seed_client["id"], seed_client["org_id"]
    token_a, rid_a = seed_client["token"], seed_client["recipient_id"]

    token_b = "b" * 16
    rid_b = (
        await db.execute(
            text(
                "insert into public.recipients (engagement_id, org_id, token) "
                "values (cast(:e as uuid), cast(:o as uuid), :t) returning id::text"
            ),
            {"e": eid, "o": org_id, "t": token_b},
        )
    ).scalar_one()

    card_id = (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, org_id) "
                "values (cast(:e as uuid), 1, 'C', 'shared', 'X', 'Q', "
                "        'short-text', cast(:o as uuid)) returning id::text"
            ),
            {"e": eid, "o": org_id},
        )
    ).scalar_one()
    for rid, val in ((rid_a, '"answer-A"'), (rid_b, '"answer-B"')):
        await db.execute(
            text(
                "insert into public.responses "
                "(card_id, engagement_id, recipient_id, state, response_value, org_id) "
                "values (cast(:c as uuid), cast(:e as uuid), cast(:r as uuid), "
                "        'answered', cast(:v as jsonb), cast(:o as uuid))"
            ),
            {"c": card_id, "e": eid, "r": rid, "v": val, "o": org_id},
        )

    # Recipient A's token sees only A's response…
    await become_anon(db_conn, token=token_a)
    rows_a = (
        await db_conn.execute(text("select recipient_id::text from public.responses"))
    ).all()
    assert [r[0] for r in rows_a] == [rid_a], "recipient A leaked B's answer"

    # …and re-pointing the GUC to B's token flips the visible row to B's.
    await db_conn.execute(
        text("select set_config('pulse.token', :t, true)"), {"t": token_b}
    )
    rows_b = (
        await db_conn.execute(text("select recipient_id::text from public.responses"))
    ).all()
    assert [r[0] for r in rows_b] == [rid_b], "recipient B leaked A's answer"


# ── Reactive cards (migration 0017): recipient-scoped cards + card_generations ──


async def _add_second_recipient(
    db: AsyncSession, *, engagement_id: str, org_id: str, token: str
) -> str:
    return (
        await db.execute(
            text(
                "insert into public.recipients (engagement_id, org_id, token) "
                "values (cast(:e as uuid), cast(:o as uuid), :t) returning id::text"
            ),
            {"e": engagement_id, "o": org_id, "t": token},
        )
    ).scalar_one()


async def test_recipient_scoped_card_visible_only_to_owner(
    db: AsyncSession,
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
) -> None:
    """``cards_self_read`` (re-created in 0017) admits a card when it's
    engagement-shared (``recipient_id is null``) OR scoped to the caller's
    own recipient. A card scoped to recipient A must stay invisible to
    recipient B even though they share the same engagement's card set."""
    eid, org_id = seed_client["id"], seed_client["org_id"]
    token_a, rid_a = seed_client["token"], seed_client["recipient_id"]
    token_b = "c" * 16
    await _add_second_recipient(db, engagement_id=eid, org_id=org_id, token=token_b)

    shared_card_id = (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, org_id) "
                "values (cast(:e as uuid), 1, 'C', 'shared', 'X', 'Q', "
                "        'short-text', cast(:o as uuid)) returning id::text"
            ),
            {"e": eid, "o": org_id},
        )
    ).scalar_one()
    scoped_card_id = (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, org_id, recipient_id, source) "
                "values (cast(:e as uuid), 2, 'C', 'scoped-to-a', 'X', 'Q', "
                "        'short-text', cast(:o as uuid), cast(:r as uuid), 'ai') "
                "returning id::text"
            ),
            {"e": eid, "o": org_id, "r": rid_a},
        )
    ).scalar_one()

    # Recipient A sees both the shared card and their own scoped card.
    await become_anon(db_conn, token=token_a)
    ids_a = {
        r[0]
        for r in (
            await db_conn.execute(text("select id::text from public.cards"))
        ).all()
    }
    assert ids_a == {shared_card_id, scoped_card_id}, "recipient A missing a visible card"

    # Recipient B — same engagement, different recipient — sees only the
    # shared card. A's scoped card must not leak.
    await db_conn.execute(
        text("select set_config('pulse.token', :t, true)"), {"t": token_b}
    )
    ids_b = {
        r[0]
        for r in (
            await db_conn.execute(text("select id::text from public.cards"))
        ).all()
    }
    assert ids_b == {shared_card_id}, "recipient B saw a card scoped to recipient A"


async def test_pulse_anon_cannot_insert_into_cards(
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
) -> None:
    """``pulse_anon`` only has SELECT on ``cards`` (migration 0001) — even
    with a valid token, INSERT must be refused at the grant level. This is
    the trust boundary the reactive-cards generation engine (PR 2) must
    never rely on: every AI-card write goes through the BYPASSRLS admin
    engine, never a pulse_anon connection."""
    await become_anon(db_conn, token=seed_client["token"])
    with pytest.raises(DBAPIError):
        await db_conn.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, "
                " question, response_type, org_id) "
                "values (cast(:e as uuid), 1, 'C', 'hack', 'X', 'Q', "
                "        'short-text', cast(:o as uuid))"
            ),
            {"e": seed_client["id"], "o": seed_client["org_id"]},
        )


async def _seed_card_generation(
    db: AsyncSession, *, engagement_id: str, org_id: str, recipient_id: str
) -> str:
    """Insert one triggering card + response + ``card_generations`` row for
    ``recipient_id``. Returns the generation row's id."""
    card_id = (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, org_id) "
                "values (cast(:e as uuid), 1, 'C', 'trigger', 'X', 'Q', "
                "        'confirm-edit', cast(:o as uuid)) returning id::text"
            ),
            {"e": engagement_id, "o": org_id},
        )
    ).scalar_one()
    response_id = (
        await db.execute(
            text(
                "insert into public.responses "
                "(card_id, engagement_id, recipient_id, state, org_id) "
                "values (cast(:c as uuid), cast(:e as uuid), cast(:r as uuid), "
                "        'answered', cast(:o as uuid)) returning id::text"
            ),
            {"c": card_id, "e": engagement_id, "r": recipient_id, "o": org_id},
        )
    ).scalar_one()
    return (
        await db.execute(
            text(
                "insert into public.card_generations "
                "(org_id, engagement_id, recipient_id, response_id, card_id, "
                " trigger_hash, status) "
                "values (cast(:o as uuid), cast(:e as uuid), cast(:r as uuid), "
                "        cast(:resp as uuid), cast(:c as uuid), 'hash-x', 'completed') "
                "returning id::text"
            ),
            {
                "o": org_id,
                "e": engagement_id,
                "r": recipient_id,
                "resp": response_id,
                "c": card_id,
            },
        )
    ).scalar_one()


async def test_card_generations_visible_only_to_owning_recipient(
    db: AsyncSession,
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
) -> None:
    """``card_generations_self_read`` scopes strictly to
    ``recipient_id = pulse_request_recipient_id()`` — a sibling recipient
    on the same engagement must see none of it (this is also the deck's
    future poll surface, so a leak here would expose another respondent's
    in-flight generation)."""
    eid, org_id = seed_client["id"], seed_client["org_id"]
    token_a, rid_a = seed_client["token"], seed_client["recipient_id"]
    token_b = "d" * 16
    await _add_second_recipient(db, engagement_id=eid, org_id=org_id, token=token_b)

    generation_id = await _seed_card_generation(
        db, engagement_id=eid, org_id=org_id, recipient_id=rid_a
    )

    await become_anon(db_conn, token=token_a)
    visible_a = (
        await db_conn.execute(text("select id::text from public.card_generations"))
    ).all()
    assert [r[0] for r in visible_a] == [generation_id]

    await db_conn.execute(
        text("select set_config('pulse.token', :t, true)"), {"t": token_b}
    )
    visible_b = (
        await db_conn.execute(text("select id::text from public.card_generations"))
    ).all()
    assert visible_b == [], "recipient B saw recipient A's card_generations row"


async def test_card_generations_visible_to_org_member_not_other_orgs(
    db: AsyncSession,
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
) -> None:
    """``card_generations_member_scope`` admits the owning org's rows to a
    ``pulse_member`` session (the operator-facing usage/cost reporting
    surface, PR 4) and refuses every other org."""
    eid, org_id, rid_a = seed_client["id"], seed_client["org_id"], seed_client["recipient_id"]
    generation_id = await _seed_card_generation(
        db, engagement_id=eid, org_id=org_id, recipient_id=rid_a
    )

    await become_member(db_conn, org_id=org_id)
    visible = (
        await db_conn.execute(text("select id::text from public.card_generations"))
    ).all()
    assert [r[0] for r in visible] == [generation_id]

    # A member of a different org sees none of it.
    await db_conn.execute(text("reset role"))
    await become_member(db_conn, org_id=str(uuid.uuid4()))
    visible_other_org = (
        await db_conn.execute(text("select id::text from public.card_generations"))
    ).all()
    assert visible_other_org == [], "a different org's member saw this org's card_generations row"
