"""Scheduled-reminder eligibility + per-recipient unsubscribe.

`list_due_reminders` is the heart of the daily job — it decides who gets a
nudge. These tests drive it directly on the owner-role `db` session (it runs
BYPASSRLS in prod), seeding recipients with controllable timing, and exercise
the public unsubscribe route end to end.
"""
from __future__ import annotations

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import reminders as reminders_lib
from pulse_api.auth.email_messages import engagement_reminder_email
from pulse_api.auth.tokens import issue_token
from pulse_api.repos import recipients as recipients_repo


async def _add_card(db: AsyncSession, *, engagement_id: str, org_id: str) -> str:
    row = (
        await db.execute(
            text(
                "insert into public.cards "
                "(engagement_id, order_index, category, title, context, question, "
                " response_type, org_id) "
                "values (cast(:e as uuid), 1, 'C', 'T', 'x', 'q?', 'short-text', "
                "        cast(:o as uuid)) returning id::text"
            ),
            {"e": engagement_id, "o": org_id},
        )
    ).mappings().one()
    return row["id"]


async def _insert_recipient(
    db: AsyncSession,
    *,
    engagement_id: str,
    org_id: str,
    email: str | None = "due@example.com",
    invited_days_ago: int | None = 10,
    reminder_count: int = 0,
    last_reminded_days_ago: int | None = None,
    last_active_days_ago: int | None = None,
    unsubscribed: bool = False,
) -> str:
    """Insert a recipient with explicit timing. ``None`` day-offsets leave the
    timestamp NULL; otherwise it's ``now() - <n> days``."""
    row = (
        await db.execute(
            text(
                """
                insert into public.recipients
                  (engagement_id, org_id, email, token, invited_at,
                   last_reminded_at, last_active_at, reminder_count, unsubscribed_at)
                values (
                  cast(:e as uuid), cast(:o as uuid), :email,
                  :token,
                  case when cast(:inv as int) is null then null
                       else now() - make_interval(days => cast(:inv as int)) end,
                  case when cast(:lrd as int) is null then null
                       else now() - make_interval(days => cast(:lrd as int)) end,
                  case when cast(:lad as int) is null then null
                       else now() - make_interval(days => cast(:lad as int)) end,
                  cast(:rc as int),
                  case when cast(:unsub as boolean) then now() else null end
                )
                returning id::text
                """
            ),
            {
                "e": engagement_id,
                "o": org_id,
                "email": email,
                "token": secrets.token_hex(8),
                "inv": invited_days_ago,
                "lrd": last_reminded_days_ago,
                "lad": last_active_days_ago,
                "rc": reminder_count,
                "unsub": unsubscribed,
            },
        )
    ).mappings().one()
    return row["id"]


async def _due_ids(db: AsyncSession) -> list[str]:
    rows = await recipients_repo.list_due_reminders(
        db, inactivity_days=7, cadence_days=7, max_reminders=3
    )
    return [r["id"] for r in rows]


# ── Eligibility ───────────────────────────────────────────────────────────


async def test_eligible_recipient_is_due(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    await _add_card(db, engagement_id=seed_client["id"], org_id=seed_client["org_id"])
    rid = await _insert_recipient(
        db, engagement_id=seed_client["id"], org_id=seed_client["org_id"]
    )
    due = await recipients_repo.list_due_reminders(
        db, inactivity_days=7, cadence_days=7, max_reminders=3
    )
    assert rid in [r["id"] for r in due]
    # Carries what the email needs.
    row = next(r for r in due if r["id"] == rid)
    assert row["email"] == "due@example.com"
    assert row["token"] and row["org_name"]


@pytest.mark.parametrize(
    "override",
    [
        {"invited_days_ago": None},            # never invited
        {"unsubscribed": True},                # opted out
        {"email": None},                       # no address to send to
        {"last_active_days_ago": 1},           # active recently → not stale
        {"last_reminded_days_ago": 1, "reminder_count": 1},  # within cadence
        {"reminder_count": 3},                 # hit the cap (max 3)
    ],
)
async def test_ineligible_recipient_excluded(
    db: AsyncSession, seed_client: dict[str, str], override: dict
) -> None:
    await _add_card(db, engagement_id=seed_client["id"], org_id=seed_client["org_id"])
    rid = await _insert_recipient(
        db, engagement_id=seed_client["id"], org_id=seed_client["org_id"], **override
    )
    assert rid not in await _due_ids(db)


async def test_completed_recipient_excluded(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    card_id = await _add_card(
        db, engagement_id=seed_client["id"], org_id=seed_client["org_id"]
    )
    rid = await _insert_recipient(
        db, engagement_id=seed_client["id"], org_id=seed_client["org_id"]
    )
    # Answer the only card → complete → no nudge.
    await db.execute(
        text(
            "insert into public.responses "
            "(card_id, engagement_id, recipient_id, state, org_id) "
            "values (cast(:c as uuid), cast(:e as uuid), cast(:r as uuid), "
            "        'answered', cast(:o as uuid))"
        ),
        {"c": card_id, "e": seed_client["id"], "r": rid, "o": seed_client["org_id"]},
    )
    assert rid not in await _due_ids(db)


async def test_no_cards_engagement_excluded(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    # seed_client has no cards — nothing to answer, so no reminder.
    rid = await _insert_recipient(
        db, engagement_id=seed_client["id"], org_id=seed_client["org_id"]
    )
    assert rid not in await _due_ids(db)


async def test_engagement_reminders_disabled_excluded(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    await _add_card(db, engagement_id=seed_client["id"], org_id=seed_client["org_id"])
    rid = await _insert_recipient(
        db, engagement_id=seed_client["id"], org_id=seed_client["org_id"]
    )
    await db.execute(
        text(
            "update public.engagements set reminders_enabled = false "
            "where id = cast(:e as uuid)"
        ),
        {"e": seed_client["id"]},
    )
    assert rid not in await _due_ids(db)


async def test_mark_reminded_bumps_counter_and_timestamp(
    db: AsyncSession, seed_client: dict[str, str]
) -> None:
    rid = await _insert_recipient(
        db, engagement_id=seed_client["id"], org_id=seed_client["org_id"]
    )
    await recipients_repo.mark_reminded(db, rid)
    row = (
        await db.execute(
            text(
                "select reminder_count, last_reminded_at is not null as stamped "
                "from public.recipients where id = cast(:r as uuid)"
            ),
            {"r": rid},
        )
    ).mappings().one()
    assert row["reminder_count"] == 1
    assert row["stamped"] is True


# ── Unsubscribe ───────────────────────────────────────────────────────────


async def test_unsubscribe_flips_flag(
    client: AsyncClient, db: AsyncSession, seed_client: dict[str, str]
) -> None:
    rid = await _insert_recipient(
        db, engagement_id=seed_client["id"], org_id=seed_client["org_id"]
    )
    token = reminders_lib.unsubscribe_url(rid).split("u=")[1]
    r = await client.post("/api/reminders/unsubscribe", json={"token": token})
    assert r.status_code == 200
    unsub = (
        await db.execute(
            text(
                "select unsubscribed_at from public.recipients "
                "where id = cast(:r as uuid)"
            ),
            {"r": rid},
        )
    ).scalar_one()
    assert unsub is not None


async def test_unsubscribe_rejects_garbage_token(client: AsyncClient) -> None:
    r = await client.post("/api/reminders/unsubscribe", json={"token": "not-a-token"})
    assert r.status_code == 400


async def test_unsubscribe_rejects_wrong_salt_token(
    client: AsyncClient, db: AsyncSession, seed_client: dict[str, str]
) -> None:
    """A token signed for a different purpose must NOT redeem as an
    unsubscribe — even though it embeds a real recipient id and uses the
    same SESSION_SECRET. Locks in per-purpose salt isolation."""
    rid = await _insert_recipient(
        db, engagement_id=seed_client["id"], org_id=seed_client["org_id"]
    )
    wrong = issue_token("email-verify", {"rid": rid})
    r = await client.post("/api/reminders/unsubscribe", json={"token": wrong})
    assert r.status_code == 400


# ── Email template ─────────────────────────────────────────────────────────


def test_reminder_email_carries_both_links() -> None:
    subject, body = engagement_reminder_email(
        deck_url="https://pulse.example/?t=abc",
        org_name="Acme",
        recipient_name="Ren",
        engagement_name="Q3",
        unsubscribe_url="https://pulse.example/unsubscribe?u=xyz",
    )
    assert "Acme" in subject
    assert "Hi Ren," in body
    assert "https://pulse.example/?t=abc" in body
    assert "https://pulse.example/unsubscribe?u=xyz" in body
