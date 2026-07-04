"""Repository helpers for the ``clients`` table (real clients/companies).

Every function runs on the org-scoped ``pulse_member`` session yielded by
``get_org_scoped_session``. The session has no BYPASSRLS and carries the
``pulse.org_id`` GUC, so RLS narrows every read/write to the active org's
clients. A forgotten ``where org_id = ...`` therefore cannot leak across
tenants — the database refuses (USING on reads, WITH CHECK on inserts).

The route layer passes the active org's ``org_id`` on writes so the
INSERT satisfies the RLS WITH CHECK; reads don't need it because RLS
already scopes them.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_for_org(session: AsyncSession) -> list[dict[str, object]]:
    """List the active org's clients, ordered by name.

    RLS narrows ``clients`` to the active org. Used for the admin list's
    client grouping and the new-engagement autocomplete.

    Args:
        session: ``pulse_member`` session with ``pulse.org_id`` set.

    Returns:
        List of ``{id, name, created_at}`` dicts.
    """
    result = await session.execute(
        text(
            "select id::text as id, name, created_at "
            "from public.clients order by name"
        )
    )
    return [dict(r) for r in result.mappings().all()]


async def get_by_id(
    session: AsyncSession, client_id: str
) -> dict[str, object] | None:
    """Fetch one client by id, or ``None`` if it doesn't resolve.

    RLS hides clients from other orgs, so a cross-org id yields ``None``
    here even though the row exists in the table. A malformed UUID is
    guarded explicitly (instead of via a blanket ``except Exception``
    around the cast) so it also resolves to ``None`` without swallowing
    genuine DB errors.
    """
    try:
        uuid.UUID(client_id)
    except (ValueError, TypeError, AttributeError):
        return None
    result = await session.execute(
        text(
            "select id::text as id, name, created_at "
            "from public.clients where id = cast(:cid as uuid)"
        ),
        {"cid": client_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_or_create(
    session: AsyncSession, *, org_id: uuid.UUID | str, name: str
) -> dict[str, object]:
    """Return the client named ``name`` in ``org_id``, creating it if absent.

    Idempotent: ``insert … on conflict (org_id, name) do nothing`` never
    duplicates a ``(org, name)`` pair, then a follow-up SELECT returns the
    row (whether it was just inserted or already existed). Scoped to the
    active org by RLS — the WITH CHECK on insert and the USING on the
    subsequent select both narrow to ``pulse.org_id``.

    ``org_id`` comes from the resolved membership, never the wire body —
    RLS WITH CHECK would reject any other value anyway.

    Args:
        session: ``pulse_member`` session with ``pulse.org_id`` set.
        org_id: Owning organization UUID (the active org).
        name: Client/company name (already validated at the route layer).

    Returns:
        ``{id, name, created_at}`` row dict for the resolved client.
    """
    await session.execute(
        text(
            "insert into public.clients (org_id, name) "
            "values (cast(:org as uuid), :n) "
            "on conflict (org_id, name) do nothing"
        ),
        {"org": str(org_id), "n": name},
    )
    # Filter the read by org_id too — RLS already scopes a pulse_member
    # session, but the explicit predicate keeps the function correct on a
    # BYPASSRLS session (e.g. owner-role test seeds, or pulse_admin) where
    # another org could hold a row with the same name.
    result = await session.execute(
        text(
            "select id::text as id, name, created_at "
            "from public.clients "
            "where org_id = cast(:org as uuid) and name = :n"
        ),
        {"org": str(org_id), "n": name},
    )
    return dict(result.mappings().one())
