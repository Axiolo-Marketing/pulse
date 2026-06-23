"""Repository helpers for the ``engagement_groups`` table (folders).

Every function runs on the org-scoped ``pulse_member`` session yielded
by ``get_org_scoped_session``. The session has no BYPASSRLS and carries
the ``pulse.org_id`` GUC, so RLS narrows every read/write to the active
org's folders. A forgotten ``where org_id = ...`` therefore cannot leak
across tenants — the database refuses (USING on reads/updates/deletes,
WITH CHECK on inserts).

The route layer passes the active org's ``org_id`` on ``create`` so the
INSERT satisfies the RLS WITH CHECK; the other functions don't need it
because RLS already scopes them.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_for_org(session: AsyncSession) -> list[dict[str, object]]:
    """List the active org's folders with a per-folder engagement count.

    RLS narrows ``engagement_groups`` to the active org. The correlated
    count joins ``clients`` (also member-scoped, so it stays in-org) so
    the operator sees how many engagements each folder holds without an
    extra round-trip. Ordered by ``name`` for a stable list.

    Args:
        session: ``pulse_member`` session with ``pulse.org_id`` set.

    Returns:
        List of ``{id, name, created_at, client_count}`` dicts.
    """
    result = await session.execute(
        text(
            """
            select
              g.id::text as id,
              g.name,
              g.created_at,
              (select count(*) from public.engagements c
                 where c.group_id = g.id)::int as client_count
            from public.engagement_groups g
            order by g.name
            """
        )
    )
    return [dict(r) for r in result.mappings().all()]


async def get_by_id(
    session: AsyncSession, group_id: str
) -> dict[str, object] | None:
    """Fetch one folder by id, or ``None`` if it doesn't resolve.

    RLS hides folders from other orgs, so a cross-org id yields ``None``
    here even though the row exists in the table. A malformed UUID raises
    on cast and is caught → ``None``.
    """
    try:
        result = await session.execute(
            text(
                "select id::text as id, name, created_at "
                "from public.engagement_groups "
                "where id = cast(:gid as uuid)"
            ),
            {"gid": group_id},
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def create(
    session: AsyncSession, *, name: str, org_id: uuid.UUID | str
) -> dict[str, object]:
    """Insert a new folder under the active org and return its row.

    ``org_id`` comes from the resolved membership, never the wire body —
    RLS WITH CHECK would reject any other value anyway.

    Args:
        session: ``pulse_member`` session.
        name: Folder label (already validated at the route layer).
        org_id: Owning organization UUID (the active org).

    Returns:
        ``{id, name, created_at}`` row dict.
    """
    result = await session.execute(
        text(
            "insert into public.engagement_groups (name, org_id) "
            "values (:n, cast(:org as uuid)) "
            "returning id::text as id, name, created_at"
        ),
        {"n": name, "org": str(org_id)},
    )
    return dict(result.mappings().one())


async def rename(
    session: AsyncSession, group_id: str, name: str
) -> dict[str, object] | None:
    """Rename a folder. Returns the refreshed row, or ``None`` if absent.

    RLS scopes the UPDATE to the active org, so a cross-org id simply
    matches no row → ``None``.
    """
    try:
        result = await session.execute(
            text(
                "update public.engagement_groups set name = :n "
                "where id = cast(:gid as uuid) "
                "returning id::text as id, name, created_at"
            ),
            {"n": name, "gid": group_id},
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def delete(session: AsyncSession, group_id: str) -> bool:
    """Delete a folder. Returns True if a row was removed.

    The ``clients.group_id`` FK is ``on delete set null``, so any
    engagements in this folder are ungrouped (not deleted) in the same
    transaction. RLS scopes the DELETE to the active org.
    """
    try:
        result = await session.execute(
            text(
                "delete from public.engagement_groups "
                "where id = cast(:gid as uuid)"
            ),
            {"gid": group_id},
        )
    except Exception:
        return False
    return result.rowcount > 0
