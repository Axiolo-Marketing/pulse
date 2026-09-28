"""Repository helpers for ``client_contacts`` — the saved people list per
client that powers the respondent type-ahead.

Runs on the org-scoped ``pulse_member`` session: RLS narrows every read and
write to the active org, so a contact from another tenant is invisible even
if its id is guessed. Writes pass the active membership's ``org_id`` so the
INSERT satisfies the RLS WITH CHECK.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_COLUMNS = (
    "id::text as id, client_id::text as client_id, email, name, role, created_at"
)


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def list_for_client(
    session: AsyncSession, client_id: str
) -> list[dict[str, object]]:
    """A client's contacts, ordered by display name (name, else email)."""
    if not _valid_uuid(client_id):
        return []
    result = await session.execute(
        text(
            f"select {_COLUMNS} from public.client_contacts "
            "where client_id = cast(:cid as uuid) "
            "order by lower(coalesce(name, email))"
        ),
        {"cid": client_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def upsert(
    session: AsyncSession,
    *,
    org_id: str,
    client_id: str,
    email: str,
    name: str | None = None,
    role: str | None = None,
) -> dict[str, object] | None:
    """Save a contact on ``client_id``, or update the existing one with the
    same email (case-insensitive). A ``None`` name/role never clears a value
    already on file — re-adding someone by email alone keeps their name.
    Returns the resulting row, or None for a malformed id."""
    if not (_valid_uuid(org_id) and _valid_uuid(client_id)):
        return None
    result = await session.execute(
        text(
            "insert into public.client_contacts as cc "
            "  (org_id, client_id, email, name, role) "
            "values (cast(:org as uuid), cast(:cid as uuid), :email, :name, :role) "
            "on conflict (client_id, lower(email)) do update set "
            "  name = coalesce(excluded.name, cc.name), "
            "  role = coalesce(excluded.role, cc.role) "
            f"returning {_COLUMNS}"
        ),
        {
            "org": org_id,
            "cid": client_id,
            "email": email,
            "name": name,
            "role": role,
        },
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def remove(
    session: AsyncSession, *, client_id: str, contact_id: str
) -> dict[str, object] | None:
    """Delete one contact. Returns the deleted row (for the audit log) or
    None when nothing matched in the active org. Engagement recipients are
    independent rows and are not touched."""
    if not (_valid_uuid(client_id) and _valid_uuid(contact_id)):
        return None
    result = await session.execute(
        text(
            "delete from public.client_contacts "
            "where id = cast(:id as uuid) and client_id = cast(:cid as uuid) "
            "returning id::text, email"
        ),
        {"id": contact_id, "cid": client_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None
