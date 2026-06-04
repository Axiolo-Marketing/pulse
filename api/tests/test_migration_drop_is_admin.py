"""Schema-shape tests for the 0005 migration.

These are not application-layer tests — they assert on the actual
``information_schema`` after Alembic has run, locking the two
load-bearing schema changes from PR 2 against silent regression:

  1. ``users.is_admin`` column is gone.
  2. ``cards``, ``responses``, ``uploads``, ``api_keys``, ``audit_logs``
     all have ``org_id`` as NOT NULL.

Run inside the same per-test transaction as everything else — these
are read-only queries against the test DB's current state.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_users_is_admin_column_dropped(db: AsyncSession) -> None:
    """The ``users.is_admin`` column does not exist post-0005."""
    cols = (
        await db.execute(
            text(
                "select column_name from information_schema.columns "
                "where table_schema = 'public' and table_name = 'users'"
            )
        )
    ).scalars().all()
    assert "is_admin" not in cols, (
        f"users.is_admin still present after 0005: columns={cols}"
    )


@pytest.mark.parametrize(
    "table",
    ["cards", "responses", "uploads", "api_keys", "audit_logs"],
)
async def test_org_id_not_null_on_every_tenant_table(
    db: AsyncSession, table: str
) -> None:
    """Migration 0005 sets ``org_id`` to NOT NULL on every tenant table."""
    is_nullable = (
        await db.execute(
            text(
                "select is_nullable from information_schema.columns "
                "where table_schema = 'public' "
                "  and table_name = :t and column_name = 'org_id'"
            ),
            {"t": table},
        )
    ).scalar_one()
    assert is_nullable == "NO", (
        f"{table}.org_id is_nullable={is_nullable!r}, expected 'NO'"
    )
