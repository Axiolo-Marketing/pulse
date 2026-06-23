"""Database engines + FastAPI dependency factories.

Four engines, one per Postgres role:

- ``engine`` connects as the schema owner (used by migrations and
  admin-only background tasks).
- ``anon_engine`` connects as ``pulse_anon``. RLS applies. Client-facing
  routes use a per-request session that runs
  ``SET LOCAL pulse.token = $1`` (via ``set_config``) before any query
  so the helper functions resolve to the right ``engagement_id``. The
  same session ALSO sets ``pulse.org_id`` from the resolved engagement's
  row so any cross-table reads stay tenant-scoped.
- ``admin_engine`` connects as ``pulse_admin`` (BYPASSRLS). Reserved for
  ``/api/superadmin/*`` and migrations after PR 2 lands; PR 1's admin
  routes still use it.
- ``member_engine`` connects as ``pulse_member`` (no BYPASSRLS). PR 2
  swaps every ``/api/admin/*`` route over to this engine via
  ``get_member_session(org_id)``. PR 1 only wires the factory — no
  callers yet — so the production data path is unchanged.
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
member_engine: AsyncEngine = _engine(
    settings.member_database_url or settings.database_url
)


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

    Opens a transaction, sets ``pulse.token`` and ``pulse.org_id`` for
    that transaction, yields a session bound to it. RLS policies fire
    against the token's engagement_id; the resolved engagement's org_id
    flows into the GUC so any future cross-tenant reads stay scoped.

    If no engagement matches the token we set ``pulse.org_id`` to the
    empty string — the helper's NULLIF turns that into NULL, and the
    org-scoped policies reject the comparison.
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
            # Resolve the engagement's org_id so cross-table reads (audit
            # logs, org-scoped extras coming in PR 2+) stay tenant-scoped.
            # Returns empty string if no row matches the token — NULLIF in
            # the RLS helper turns that into NULL and the policy rejects.
            org_row = (
                await conn.execute(
                    text(
                        "select coalesce((select org_id::text from public.engagements "
                        "where token = :t limit 1), '')"
                    ),
                    {"t": x_pulse_token},
                )
            ).scalar_one()
            await conn.execute(
                text("select set_config('pulse.org_id', :o, true)"),
                {"o": org_row or ""},
            )
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
                yield session
            await trans.commit()
        except Exception:
            await trans.rollback()
            raise


async def get_member_session(org_id: str) -> AsyncIterator[AsyncSession]:
    """Org-member session — RLS-filtered by the caller's active org_id.

    Wired but uncalled in PR 1. PR 2 makes every ``/api/admin/*`` route
    take an ``Annotated[AsyncSession, Depends(get_member_session)]`` via
    an auth dependency that resolves ``(user, membership)`` and passes
    the membership's ``org_id`` in here.

    The ``pulse_member`` role has no BYPASSRLS, so a route handler that
    forgets to filter by ``org_id`` still cannot leak across tenants —
    Postgres refuses the row.

    Args:
        org_id: UUID string for the active organization. Set on the
            ``pulse.org_id`` GUC for the lifetime of the request.

    Yields:
        AsyncSession bound to a connection with the GUC set.
    """
    async with member_engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(
                text("select set_config('pulse.org_id', :org_id, true)"),
                {"org_id": str(org_id)},
            )
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
                yield session
            await trans.commit()
        except Exception:
            await trans.rollback()
            raise
