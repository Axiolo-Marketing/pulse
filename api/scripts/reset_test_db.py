"""Drop + recreate the test database.

Useful when the test DB has been left at a migration revision that
doesn't exist on the current branch (e.g. after `git checkout` from a
feature branch with extra migrations) or when an interactive debugger
left half-written rows behind.

Hardcoded to operate on `pulse_test` only — this never touches the dev
or production database. Run via:
    make reset-test-db

…or directly inside the backend container:
    docker compose exec backend uv run python -m scripts.reset_test_db
"""
from __future__ import annotations

import asyncio
import sys
from urllib.parse import urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pulse_api.config import settings

TARGET_DB = "pulse_test"


def _maintenance_dsn() -> str:
    """Return a DSN that connects to `postgres` on the same server.

    Drop/create can't run against the database being dropped. We pivot to
    `postgres` (the default maintenance DB created by every Postgres
    server) using the test_database_url as the credential source, falling
    back to database_url if test_database_url isn't set."""
    source = settings.test_database_url or settings.database_url
    parsed = urlparse(source)
    return urlunparse(parsed._replace(path="/postgres"))


async def reset() -> None:
    engine = create_async_engine(_maintenance_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            # Kick any active connections so the drop doesn't block. The
            # FORCE clause makes this atomic on Postgres 13+.
            await conn.execute(
                text(f'drop database if exists "{TARGET_DB}" with (force)')
            )
            await conn.execute(text(f'create database "{TARGET_DB}"'))
    finally:
        await engine.dispose()


def main() -> int:
    asyncio.run(reset())
    sys.stdout.write(f"reset database {TARGET_DB}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
