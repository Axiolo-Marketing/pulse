"""Repository helpers for the `organizations` table.

The caller's session role determines what rows are visible:

* ``pulse_member`` (the org-scoped session) only sees the row whose
  ``id = pulse.org_id``. Use this for owner-only mutations on the org
  the caller is currently scoped to.
* ``pulse_admin`` (BYPASSRLS) sees every row. Used for cross-org reads
  such as ``list_orgs_for_user`` which by definition spans every org
  the user belongs to.

All functions take an explicit ``session`` argument — the route layer
picks which session role to inject based on the operation's semantics.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_orgs_for_user(
    session: AsyncSession, user_id: uuid.UUID | str
) -> list[dict[str, object]]:
    """List every org the user has a membership in.

    Joins ``organization_memberships`` and ``organizations`` and returns
    one dict per membership, deterministically ordered by org name so
    the UI can render a stable list. Uses a BYPASSRLS session because
    the operation spans every org the user belongs to.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        user_id: UUID of the user whose memberships to enumerate.

    Returns:
        List of dicts with keys ``id``, ``name``, ``slug``, ``role``,
        ``logo_path``. Empty list when the user has no memberships.
    """
    result = await session.execute(
        text(
            "select o.id::text as id, o.name, o.slug, "
            "       m.role as role, o.logo_path "
            "from public.organization_memberships m "
            "join public.organizations o on o.id = m.org_id "
            "where m.user_id = cast(:u as uuid) "
            "order by o.name"
        ),
        {"u": str(user_id)},
    )
    return [dict(row) for row in result.mappings().all()]


async def is_member_of(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | str,
    org_id: uuid.UUID | str,
) -> bool:
    """Return True iff a membership row exists for ``(user_id, org_id)``.

    Uses a BYPASSRLS session because the operation is intentionally
    cross-org — the caller is asking "could this user switch into this
    org?" against an org other than the currently-active one.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        user_id: UUID of the user.
        org_id: UUID of the target org.

    Returns:
        True if the membership exists, False otherwise.
    """
    result = await session.execute(
        text(
            "select 1 from public.organization_memberships "
            "where user_id = cast(:u as uuid) "
            "  and org_id  = cast(:o as uuid) limit 1"
        ),
        {"u": str(user_id), "o": str(org_id)},
    )
    return result.scalar() is not None


async def set_last_active_org(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | str,
    org_id: uuid.UUID | str,
) -> None:
    """Update ``users.last_active_org_id`` to ``org_id``.

    Caller commits. Used by the ``POST /api/me/switch-org`` route to
    persist the switch so the next login lands in the right org.

    Args:
        session: ``pulse_admin`` session.
        user_id: UUID of the user whose pointer to update.
        org_id: UUID of the org to point at.
    """
    await session.execute(
        text(
            "update public.users set last_active_org_id = cast(:o as uuid) "
            "where id = cast(:u as uuid)"
        ),
        {"o": str(org_id), "u": str(user_id)},
    )


async def clear_last_active_org_if_match(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | str,
    org_id: uuid.UUID | str,
) -> None:
    """Reset ``users.last_active_org_id`` to NULL iff it currently equals
    ``org_id``. Used after a member is removed from the org they had as
    their pointer — leaving a dangling reference would 403 their next
    sign-in until they explicitly switched.

    Args:
        session: ``pulse_admin`` session.
        user_id: UUID of the affected user.
        org_id: UUID of the org being removed; only clears if pointing here.
    """
    await session.execute(
        text(
            "update public.users set last_active_org_id = null "
            "where id = cast(:u as uuid) "
            "  and last_active_org_id = cast(:o as uuid)"
        ),
        {"u": str(user_id), "o": str(org_id)},
    )


async def get_for_member(
    session: AsyncSession, org_id: uuid.UUID | str
) -> dict[str, object] | None:
    """Fetch the active org's name + slug + logo.

    Runs under the org-scoped (``pulse_member``) session so RLS narrows
    to the active org by construction. Returns ``None`` if no row
    matches (which would imply the GUC and the membership don't agree —
    treat as 404 at the route layer).

    Args:
        session: ``pulse_member`` session with ``pulse.org_id`` set.
        org_id: UUID of the active org (matches the GUC).

    Returns:
        ``{id, name, slug, logo_path, created_at}`` or ``None``.
    """
    result = await session.execute(
        text(
            "select id::text as id, name, slug, logo_path, created_at "
            "from public.organizations where id = cast(:o as uuid)"
        ),
        {"o": str(org_id)},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def update_name(
    session: AsyncSession, *, org_id: uuid.UUID | str, name: str
) -> dict[str, object] | None:
    """Update the org's display name.

    RLS WITH CHECK on the org-scoped session refuses any other ``id``
    so a forgotten predicate in the SQL can't update a different org.

    Args:
        session: ``pulse_member`` session.
        org_id: UUID of the org to update.
        name: New name (already validated at the route layer).

    Returns:
        Refreshed row dict, or ``None`` if the org was not found.
    """
    result = await session.execute(
        text(
            "update public.organizations set name = :n "
            "where id = cast(:o as uuid) "
            "returning id::text as id, name, slug, logo_path, created_at"
        ),
        {"n": name, "o": str(org_id)},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def set_logo_path(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    logo_path: str | None,
) -> None:
    """Set or clear the ``logo_path`` column on the org row.

    Args:
        session: ``pulse_member`` session.
        org_id: UUID of the active org.
        logo_path: Relative path under ``settings.upload_dir`` or None.
    """
    await session.execute(
        text(
            "update public.organizations set logo_path = :p "
            "where id = cast(:o as uuid)"
        ),
        {"p": logo_path, "o": str(org_id)},
    )


async def member_count(
    session: AsyncSession, org_id: uuid.UUID | str
) -> int:
    """Return the number of memberships in this org.

    Runs under the org-scoped session — RLS narrows the count to the
    active org automatically.

    Args:
        session: ``pulse_member`` session.
        org_id: UUID of the active org (matches the GUC; passed to keep
            the SQL self-documenting).

    Returns:
        Integer count of memberships.
    """
    result = await session.execute(
        text(
            "select count(*)::int from public.organization_memberships "
            "where org_id = cast(:o as uuid)"
        ),
        {"o": str(org_id)},
    )
    return int(result.scalar() or 0)


async def pending_invite_count(
    session: AsyncSession, org_id: uuid.UUID | str
) -> int:
    """Return the number of pending, non-expired invites for this org.

    Args:
        session: ``pulse_member`` session.
        org_id: UUID of the active org.

    Returns:
        Integer count of pending invites.
    """
    result = await session.execute(
        text(
            "select count(*)::int from public.organization_invites "
            "where org_id = cast(:o as uuid) "
            "  and accepted_at is null "
            "  and expires_at > now()"
        ),
        {"o": str(org_id)},
    )
    return int(result.scalar() or 0)
