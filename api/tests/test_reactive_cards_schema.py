"""Phase 1 ("Schema + scoped-card read path") tests for reactive cards.

Migration 0017 adds `cards.recipient_id` / `source` / `generated_from_response_id`,
the `card_generations` lifecycle/cost-ledger table, and narrows `cards_self_read`
to the recipient grain (covered directly in `test_rls_isolation.py`). This file
covers the repo-level helpers (`create_card` kwargs, `count_generated_for_recipient`,
`delete_generated_for_engagement`) plus the lifecycle and regression behavior the
plan calls out: engagement reset removes AI cards and cascades their generation
row, recipient delete cascades their scoped cards, and the `recipients.py`
card-count fixes actually protect a sibling recipient's progress/reminder
eligibility from another recipient's scoped cards.

The generation engine itself (`reactive.py`, PR 2) does not exist yet on this
branch — nothing here calls an LLM or a poll endpoint.
"""
from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.repos import cards as cards_repo


async def _add_card(
    db: AsyncSession,
    *,
    engagement_id: str,
    org_id: str,
    title: str = "shared",
    response_type: str = "short-text",
    recipient_id: str | None = None,
    source: str = "operator",
) -> str:
    row = (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, org_id, recipient_id, source) "
                "values (cast(:e as uuid), "
                "  coalesce((select max(order_index) from public.cards "
                "            where engagement_id = cast(:e as uuid)), 0) + 1, "
                "  'C', :t, 'x', 'q?', :rt, cast(:o as uuid), "
                "  cast(:r as uuid), :src) "
                "returning id::text"
            ),
            {
                "e": engagement_id,
                "t": title,
                "rt": response_type,
                "o": org_id,
                "r": recipient_id,
                "src": source,
            },
        )
    ).mappings().one()
    return row["id"]


async def _add_response(
    db: AsyncSession,
    *,
    card_id: str,
    engagement_id: str,
    recipient_id: str,
    org_id: str,
    state: str = "answered",
) -> str:
    row = (
        await db.execute(
            text(
                "insert into public.responses "
                "(card_id, engagement_id, recipient_id, state, org_id) "
                "values (cast(:c as uuid), cast(:e as uuid), cast(:r as uuid), "
                "        :s, cast(:o as uuid)) returning id::text"
            ),
            {
                "c": card_id,
                "e": engagement_id,
                "r": recipient_id,
                "s": state,
                "o": org_id,
            },
        )
    ).mappings().one()
    return row["id"]


async def _add_generation(
    db: AsyncSession,
    *,
    org_id: str,
    engagement_id: str,
    recipient_id: str,
    response_id: str,
    card_id: str,
    created_card_ids: list[str],
    status: str = "completed",
) -> str:
    row = (
        await db.execute(
            text(
                "insert into public.card_generations "
                "(org_id, engagement_id, recipient_id, response_id, card_id, "
                " trigger_hash, status, created_card_ids) "
                "values (cast(:o as uuid), cast(:e as uuid), cast(:r as uuid), "
                "        cast(:resp as uuid), cast(:c as uuid), 'hash-1', :st, "
                "        cast(:cc as uuid[])) "
                "returning id::text"
            ),
            {
                "o": org_id,
                "e": engagement_id,
                "r": recipient_id,
                "resp": response_id,
                "c": card_id,
                "st": status,
                "cc": created_card_ids,
            },
        )
    ).mappings().one()
    return row["id"]


async def _add_recipient(
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


# ── repo: create_card kwargs ────────────────────────────────────────────────


async def test_create_card_defaults_to_operator_shared(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    """Existing callers (no new kwargs passed) keep getting the pre-0017
    shape: engagement-shared, operator-authored, no provenance link."""
    row = await cards_repo.create_card(
        db,
        engagement_id=seed_client["id"],
        category="C",
        title="T",
        context="x",
        question="q?",
        response_type="short-text",
        options=None,
        default_value=None,
        skip_allowed=True,
        attachment_path=None,
        org_id=seed_client["org_id"],
    )
    assert row is not None
    assert row["recipient_id"] is None
    assert row["source"] == "operator"
    assert row["generated_from_response_id"] is None


async def test_create_card_recipient_scoped_ai(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    """The generation engine's call shape (PR 2): recipient-scoped,
    ``source='ai'``, with a back-link to the triggering response."""
    trigger_card = await _add_card(
        db,
        engagement_id=seed_client["id"],
        org_id=seed_client["org_id"],
        response_type="confirm-edit",
    )
    response_id = await _add_response(
        db,
        card_id=trigger_card,
        engagement_id=seed_client["id"],
        recipient_id=seed_client["recipient_id"],
        org_id=seed_client["org_id"],
    )
    row = await cards_repo.create_card(
        db,
        engagement_id=seed_client["id"],
        category="C",
        title="Follow-up",
        context="x",
        question="q?",
        response_type="short-text",
        options=None,
        default_value=None,
        skip_allowed=True,
        attachment_path=None,
        org_id=seed_client["org_id"],
        recipient_id=seed_client["recipient_id"],
        source="ai",
        generated_from_response_id=response_id,
    )
    assert row is not None
    assert row["recipient_id"] == seed_client["recipient_id"]
    assert row["source"] == "ai"
    assert row["generated_from_response_id"] == response_id


# ── repo: count_generated_for_recipient / delete_generated_for_engagement ───


async def test_count_generated_for_recipient_scoped_per_recipient(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    eid, org_id, rid_a = (
        seed_client["id"],
        seed_client["org_id"],
        seed_client["recipient_id"],
    )
    rid_b = await _add_recipient(db, engagement_id=eid, org_id=org_id, token="b" * 16)

    # 2 AI cards for A, 1 for B, 1 shared card (counts toward neither).
    await _add_card(db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai")
    await _add_card(db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai")
    await _add_card(db, engagement_id=eid, org_id=org_id, recipient_id=rid_b, source="ai")
    await _add_card(db, engagement_id=eid, org_id=org_id)

    assert await cards_repo.count_generated_for_recipient(db, rid_a) == 2
    assert await cards_repo.count_generated_for_recipient(db, rid_b) == 1


async def test_delete_generated_for_engagement_removes_only_ai_cards(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    eid, org_id, rid_a = (
        seed_client["id"],
        seed_client["org_id"],
        seed_client["recipient_id"],
    )
    operator_card = await _add_card(db, engagement_id=eid, org_id=org_id)
    ai_card_1 = await _add_card(
        db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai"
    )
    ai_card_2 = await _add_card(
        db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai"
    )

    removed = await cards_repo.delete_generated_for_engagement(db, eid)
    assert removed == 2

    remaining_ids = {
        r[0]
        for r in (
            await db.execute(
                text(
                    "select id::text from public.cards "
                    "where engagement_id = cast(:e as uuid)"
                ),
                {"e": eid},
            )
        ).all()
    }
    assert remaining_ids == {operator_card}
    assert ai_card_1 not in remaining_ids
    assert ai_card_2 not in remaining_ids


async def test_delete_generated_for_engagement_unknown_id_is_noop(
    db: AsyncSession,
) -> None:
    assert await cards_repo.delete_generated_for_engagement(db, "not-a-uuid") == 0


async def test_count_generated_for_recipient_unknown_id_is_zero(
    db: AsyncSession,
) -> None:
    assert await cards_repo.count_generated_for_recipient(db, "not-a-uuid") == 0


# ── lifecycle: engagement reset removes AI cards + cascades generations ────


async def test_reset_engagement_removes_ai_cards_and_cascades_generation_row(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
) -> None:
    """Reset must remove AI-generated cards (they were produced from a
    correction that no longer exists after the wipe) and their
    ``card_generations`` row, while leaving the operator-authored trigger
    card intact."""
    eid, org_id, rid_a = (
        seed_client["id"],
        seed_client["org_id"],
        seed_client["recipient_id"],
    )

    trigger_card = await _add_card(
        db, engagement_id=eid, org_id=org_id, response_type="confirm-edit"
    )
    triggering_response = await _add_response(
        db, card_id=trigger_card, engagement_id=eid, recipient_id=rid_a, org_id=org_id
    )
    ai_card = await _add_card(
        db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai"
    )
    generation_id = await _add_generation(
        db,
        org_id=org_id,
        engagement_id=eid,
        recipient_id=rid_a,
        response_id=triggering_response,
        card_id=trigger_card,
        created_card_ids=[ai_card],
    )
    # The respondent also answered the generated card.
    await _add_response(
        db, card_id=ai_card, engagement_id=eid, recipient_id=rid_a, org_id=org_id
    )

    r = await admin_authed.post(f"/api/admin/engagements/{eid}/reset")
    assert r.status_code == 200

    cards_left = {
        row[0]
        for row in (
            await db.execute(
                text(
                    "select id::text from public.cards "
                    "where engagement_id = cast(:e as uuid)"
                ),
                {"e": eid},
            )
        ).all()
    }
    assert cards_left == {trigger_card}, "AI card must be removed on reset"

    generations_left = (
        await db.execute(
            text(
                "select count(*) from public.card_generations "
                "where id = cast(:g as uuid)"
            ),
            {"g": generation_id},
        )
    ).scalar_one()
    assert generations_left == 0, (
        "card_generations row must cascade away with its (now-deleted) response"
    )

    responses_left = (
        await db.execute(
            text(
                "select count(*) from public.responses "
                "where engagement_id = cast(:e as uuid)"
            ),
            {"e": eid},
        )
    ).scalar_one()
    assert responses_left == 0

    audit_meta = (
        await db.execute(
            text(
                "select metadata from public.audit_logs "
                "where action = 'engagement.reset' and target_id = :e "
                "order by created_at desc limit 1"
            ),
            {"e": eid},
        )
    ).scalar_one()
    assert audit_meta["ai_cards_removed"] == 1


async def test_reset_engagement_cleans_up_upload_attached_to_ai_card(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    tmp_uploads_dir,
) -> None:
    """Regression: the upload route has no ``response_type`` restriction
    (any card the recipient can see can carry a ``kind='voice'`` upload),
    so an AI-generated card can have an on-disk file attached. Uploads
    must be wiped (and their files unlinked) BEFORE AI cards are deleted —
    otherwise the cascade from ``cards`` -> ``uploads`` (``on delete
    cascade``) silently drops the upload row before this route's own
    upload-cleanup query ever sees its ``storage_path``, leaking the file
    on disk forever."""
    eid, org_id, rid_a = (
        seed_client["id"],
        seed_client["org_id"],
        seed_client["recipient_id"],
    )
    ai_card = await _add_card(
        db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai"
    )
    rel_path = f"{eid}/{ai_card}/voice-note.webm"
    full_path = tmp_uploads_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(b"audio-bytes")
    await db.execute(
        text(
            "insert into public.uploads "
            "(card_id, engagement_id, recipient_id, file_name, "
            " file_size_bytes, storage_path, kind, org_id) "
            "values (cast(:k as uuid), cast(:e as uuid), cast(:r as uuid), "
            "        'voice-note.webm', 11, :sp, 'voice', cast(:o as uuid))"
        ),
        {"k": ai_card, "e": eid, "r": rid_a, "sp": rel_path, "o": org_id},
    )

    r = await admin_authed.post(f"/api/admin/engagements/{eid}/reset")
    assert r.status_code == 200
    assert r.json()["uploads_cleared"] == 1, (
        "the AI card's upload must be counted/cleaned by reset, not "
        "silently dropped by the cards->uploads cascade"
    )
    assert not full_path.exists(), "on-disk file for the AI card's upload must be removed"

    uploads_left = (
        await db.execute(
            text(
                "select count(*) from public.uploads "
                "where engagement_id = cast(:e as uuid)"
            ),
            {"e": eid},
        )
    ).scalar_one()
    assert uploads_left == 0


# ── lifecycle: recipient delete cascades their scoped cards ────────────────


async def test_recipient_delete_cascades_their_scoped_cards(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
) -> None:
    """Deleting a recipient must cascade away any card scoped to them
    (``cards.recipient_id`` is ``on delete cascade``) — but a shared card,
    and another recipient's scoped card, must survive untouched."""
    eid, org_id, rid_a = (
        seed_client["id"],
        seed_client["org_id"],
        seed_client["recipient_id"],
    )
    rid_b_resp = await admin_authed.post(
        f"/api/admin/engagements/{eid}/recipients", json={"email": "b@example.com"}
    )
    assert rid_b_resp.status_code == 201
    rid_b = rid_b_resp.json()["id"]

    shared_card = await _add_card(db, engagement_id=eid, org_id=org_id)
    a_scoped_card = await _add_card(
        db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai"
    )
    b_scoped_card = await _add_card(
        db, engagement_id=eid, org_id=org_id, recipient_id=rid_b, source="ai"
    )

    r = await admin_authed.delete(f"/api/admin/engagements/{eid}/recipients/{rid_b}")
    assert r.status_code == 204

    remaining_ids = {
        row[0]
        for row in (
            await db.execute(
                text(
                    "select id::text from public.cards "
                    "where engagement_id = cast(:e as uuid)"
                ),
                {"e": eid},
            )
        ).all()
    }
    assert remaining_ids == {shared_card, a_scoped_card}
    assert b_scoped_card not in remaining_ids


# ── regression: recipients.py card-count fixes (progress rollup) ───────────


async def test_add_recipient_total_cards_ignores_other_recipients_scoped_cards(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
) -> None:
    """Regression for ``recipients_repo.add``'s RETURNING subquery: an AI
    card scoped to an existing recipient must not count toward a brand-new
    recipient's ``total_cards``."""
    eid, org_id, rid_a = (
        seed_client["id"],
        seed_client["org_id"],
        seed_client["recipient_id"],
    )
    await _add_card(db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai")

    r = await admin_authed.post(
        f"/api/admin/engagements/{eid}/recipients", json={"email": "fresh@example.com"}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["total_cards"] == 0, "new recipient must not inherit A's scoped card"
    assert body["completed_count"] == 0


async def test_completed_recipients_ignores_scoped_cards_for_other_recipients(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
) -> None:
    """Regression for ``list_all_with_counts``'s ``completed_recipients``:
    an AI card scoped to recipient A must not make recipient B look
    permanently incomplete in the admin engagement-list overview."""
    eid, org_id, rid_a = (
        seed_client["id"],
        seed_client["org_id"],
        seed_client["recipient_id"],
    )
    shared_card = await _add_card(db, engagement_id=eid, org_id=org_id)
    ai_card = await _add_card(
        db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai"
    )

    b_resp = await admin_authed.post(
        f"/api/admin/engagements/{eid}/recipients", json={"email": "b@example.com"}
    )
    rid_b = b_resp.json()["id"]

    # A answers both cards they can see; B answers only the shared one —
    # B can't even see the AI card generated for A.
    await _add_response(db, card_id=shared_card, engagement_id=eid, recipient_id=rid_a, org_id=org_id)
    await _add_response(db, card_id=ai_card, engagement_id=eid, recipient_id=rid_a, org_id=org_id)
    await _add_response(db, card_id=shared_card, engagement_id=eid, recipient_id=rid_b, org_id=org_id)

    r = await admin_authed.get("/api/admin/engagements")
    assert r.status_code == 200
    row = next(c for c in r.json() if c["id"] == eid)
    assert row["recipients_count"] == 2
    assert row["completed_recipients"] == 2, (
        "recipient B should be complete (1 of 1 visible cards) despite "
        "recipient A's AI-scoped card"
    )


async def test_list_recipients_total_cards_scoped_per_recipient(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
) -> None:
    """Regression for the ``_RECIPIENT_SELECT`` progress rollup: each
    recipient's ``total_cards`` reflects only what they can see."""
    eid, org_id, rid_a = (
        seed_client["id"],
        seed_client["org_id"],
        seed_client["recipient_id"],
    )
    await _add_card(db, engagement_id=eid, org_id=org_id)  # shared
    await _add_card(db, engagement_id=eid, org_id=org_id, recipient_id=rid_a, source="ai")

    b_resp = await admin_authed.post(
        f"/api/admin/engagements/{eid}/recipients", json={"email": "b@example.com"}
    )
    rid_b = b_resp.json()["id"]

    r = await admin_authed.get(f"/api/admin/engagements/{eid}/recipients")
    assert r.status_code == 200
    by_id = {row["id"]: row for row in r.json()}
    assert by_id[rid_a]["total_cards"] == 2
    assert by_id[rid_b]["total_cards"] == 1
