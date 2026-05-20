"""Database engines + FastAPI dependency factories.

Three engines, one per Postgres role:
- `engine` connects as the schema owner (used by migrations and admin-only
  background tasks).
- `anon_engine` connects as `pulse_anon`. RLS applies. Client-facing routes
  use a per-request session that runs `SET LOCAL pulse.token = $1` before
  any query so the helper functions resolve to the right client_id.
- `admin_engine` connects as `pulse_admin` (BYPASSRLS). Admin routes use
  this directly — no token gymnastics needed.
"""
from collections.abc import AsyncIterator

from fastapi import Header, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from pulse_api.config import settings


def _engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


engine: AsyncEngine = _engine(settings.database_url)
anon_engine: AsyncEngine = _engine(settings.anon_database_url or settings.database_url)
admin_engine: AsyncEngine = _engine(settings.admin_database_url or settings.database_url)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Owner session — for migrations, healthchecks, internal jobs only."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


async def get_admin_session() -> AsyncIterator[AsyncSession]:
    """Admin session — BYPASSRLS. Gate the calling route with admin auth."""
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


async def get_anon_session(
    x_pulse_token: str | None = Header(default=None, alias="X-Pulse-Token"),
) -> AsyncIterator[AsyncSession]:
    """Client session — RLS-filtered by the request's X-Pulse-Token header.

    Opens a transaction, sets `pulse.token` for that transaction, yields a
    session bound to it. RLS policies fire against the token's client_id.
    Commits on success or rolls back on exception, then closes.
    """
    if not x_pulse_token:
        raise HTTPException(status_code=401, detail="missing token")

    async with anon_engine.connect() as conn:
        trans = await conn.begin()
        try:
            # set_config(name, value, is_local=true) is the parameter-binding
            # equivalent of SET LOCAL. SET LOCAL itself does not accept $-params.
            await conn.execute(
                text("select set_config('pulse.token', :t, true)"),
                {"t": x_pulse_token},
            )
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
                yield session
            await trans.commit()
        except Exception:
            await trans.rollback()
            raise
