"""Multi-tenant isolation tests.

These run direct SQL — no FastAPI in the middle — so they prove the
database-layer backstop, not just the application logic. If these regress,
the multi-tenant security model is broken regardless of how the API behaves.

The two load-bearing properties:
1. `pulse_anon` with no `pulse.token` set sees zero rows across every
   RLS-protected table.
2. `pulse_anon` with a valid token sees only the matching client's rows —
   never the other client's.

The tests seed data as the superuser, then call `become_anon()` to switch
the open transaction's effective role to `pulse_anon`. The seed data is
visible to the post-switch queries (same transaction), and RLS then
filters it the same way it would on a real production connection.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tests.conftest import become_anon


RLS_TABLES = ["clients", "cards", "responses", "uploads"]


async def _seed_full_set(db: AsyncSession, client_id: str, label: str) -> None:
    """Insert one card + one response + one upload for the given client."""
    await db.execute(
        text(
            "insert into public.cards "
            "(client_id, order_index, category, title, context, question, response_type) "
            "values (cast(:cid as uuid), 1, 'C', :t, 'X', 'Q', 'short-text')"
        ),
        {"cid": client_id, "t": f"{label} card"},
    )
    await db.execute(
        text(
            "insert into public.responses (card_id, client_id, state) "
            "select id, client_id, 'answered' from public.cards "
            "where client_id = cast(:cid as uuid)"
        ),
        {"cid": client_id},
    )
    await db.execute(
        text(
            "insert into public.uploads "
            "(card_id, client_id, file_name, file_size_bytes, storage_path) "
            "select id, client_id, :fn, 100, 'x/y/z' from public.cards "
            "where client_id = cast(:cid as uuid)"
        ),
        {"cid": client_id, "fn": f"{label}.pdf"},
    )


@pytest.mark.parametrize("table", RLS_TABLES)
async def test_anon_with_no_token_sees_zero_rows(
    db: AsyncSession,
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
    table: str,
) -> None:
    await _seed_full_set(db, seed_client["id"], "renee")
    await _seed_full_set(db, other_seeded_client["id"], "josh")

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
    await _seed_full_set(db, seed_client["id"], "renee")
    await _seed_full_set(db, other_seeded_client["id"], "josh")

    await become_anon(db_conn, token=seed_client["token"])

    count = (
        await db_conn.execute(text(f"select count(*) from public.{table}"))
    ).scalar()
    assert count == 1, f"expected 1 visible row in {table}; got {count}"

    visible_client_ids = [
        str(r[0])
        for r in (
            await db_conn.execute(text(f"select client_id from public.{table}"))
        ).all()
    ]
    assert visible_client_ids == [seed_client["id"]], (
        f"{table} leaked rows from another client: {visible_client_ids}"
    )


async def test_anon_with_unknown_token_sees_nothing(
    db: AsyncSession,
    db_conn: AsyncConnection,
    seed_client: dict[str, str],
) -> None:
    await become_anon(db_conn, token="ffffffffffffffff")
    count = (await db_conn.execute(text("select count(*) from public.clients"))).scalar()
    assert count == 0
