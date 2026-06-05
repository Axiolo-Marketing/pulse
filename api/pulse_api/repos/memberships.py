"""Repository helpers for `organization_memberships`.

Two role contexts use these helpers:

* The org-scoped (``pulse_member``) session — RLS narrows automatically
  to the active org. Owner-gated mutations (role change, remove member)
  run here so a forgotten predicate cannot leak across orgs.
* Cross-org checks (e.g. "is the last owner clearing themselves?")
  also run on the org-scoped session because they are always against
  the active org — never a different org.

All functions take an explicit ``session`` argument; the route layer
picks the right one.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_membership_rows(
    session: AsyncSession, org_id: uuid.UUID | str
) -> list[dict[str, object]]:
    """List bare membership rows for ``org_id`` without user join.

    Runs cleanly on a ``pulse_member`` session because the query stays
    inside the ``organization_memberships`` table.

    Args:
        session: ``pulse_member`` session, RLS-scoped to ``org_id``.
        org_id: UUID of the active org.

    Returns:
        List of ``{user_id, role, joined_at}`` dicts ordered by
        ``created_at``.
    """
    result = await session.execute(
        text(
            "select user_id::text as user_id, role, "
            "       created_at as joined_at "
            "from public.organization_memberships "
            "where org_id = cast(:o as uuid) "
            "order by created_at"
        ),
        {"o": str(org_id)},
    )
    return [dict(row) for row in result.mappings().all()]


async def list_user_display_fields(
    session: AsyncSession, user_ids: list[str]
) -> dict[str, dict[str, object]]:
    """Fetch ``{email, name}`` per ``user_id`` for a list of user UUIDs.

    Used by the org-members listing flow: the membership rows come
    from a ``pulse_member`` session (RLS-scoped); the user display
    fields come from a ``pulse_admin`` session via this function.

    Args:
        session: ``pulse_admin`` session.
        user_ids: List of UUID strings.

    Returns:
        ``{user_id: {email, name}}`` map. user_ids with no matching
        user row are omitted (membership pointing at a removed user).
    """
    if not user_ids:
        return {}
    result = await session.execute(
        text(
            "select id::text as user_id, email, name "
            "from public.users where id = any(cast(:ids as uuid[]))"
        ),
        {"ids": user_ids},
    )
    return {
        str(u["user_id"]): {"email": u["email"], "name": u["name"]}
        for u in result.mappings().all()
    }


async def get_membership(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    user_id: uuid.UUID | str,
) -> dict[str, object] | None:
    """Return the ``(org_id, user_id)`` membership row, or None.

    Args:
        session: ``pulse_member`` session (RLS-scoped) or ``pulse_admin``.
        org_id: UUID of the org.
        user_id: UUID of the user.

    Returns:
        ``{id, org_id, user_id, role}`` dict or ``None`` if no row
        matches.
    """
    result = await session.execute(
        text(
            "select id::text as id, org_id::text as org_id, "
            "       user_id::text as user_id, role "
            "from public.organization_memberships "
            "where org_id = cast(:o as uuid) "
            "  and user_id = cast(:u as uuid)"
        ),
        {"o": str(org_id), "u": str(user_id)},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def count_owners(
    session: AsyncSession, org_id: uuid.UUID | str
) -> int:
    """Return the number of ``owner``-role memberships in ``org_id``.

    The "no removing the last owner" gate uses this. Runs under the
    org-scoped session so the count matches what the caller can see.

    Args:
        session: ``pulse_member`` session.
        org_id: UUID of the active org.

    Returns:
        Integer count of owner memberships.
    """
    result = await session.execute(
        text(
            "select count(*)::int from public.organization_memberships "
            "where org_id = cast(:o as uuid) and role = 'owner'"
        ),
        {"o": str(org_id)},
    )
    return int(result.scalar() or 0)


async def lock_owners(
    session: AsyncSession, org_id: uuid.UUID | str
) -> None:
    """Acquire row-level locks on every owner membership in ``org_id``.

    Run before the ``count_owners`` gate in demote/remove paths so two
    concurrent transactions that target *different* owner rows still
    serialize at the DB layer: the second transaction blocks on the
    first's locks until it commits, then re-reads the count. This
    closes the last-owner TOCTOU window — without it, both could pass
    a "owners == 2" check and end up at zero owners.

    No-op when there are no owners (the org has bigger problems in
    that case; the route layer's existing 404/409 gates handle it).

    Args:
        session: ``pulse_member`` session (the route runs the
            subsequent UPDATE/DELETE on the same session, so the lock
            must live on that connection).
        org_id: UUID of the active org.
    """
    await session.execute(
        text(
            "select id from public.organization_memberships "
            "where org_id = cast(:o as uuid) and role = 'owner' "
            "for update"
        ),
        {"o": str(org_id)},
    )


async def update_role(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    user_id: uuid.UUID | str,
    role: str,
) -> dict[str, object] | None:
    """Update a member's role within ``org_id``.

    Caller commits. Caller is responsible for the "at least one owner"
    invariant — call ``count_owners`` first when demoting an owner.

    Args:
        session: ``pulse_member`` session.
        org_id: UUID of the active org.
        user_id: UUID of the user whose role to change.
        role: ``"owner"`` or ``"member"``.

    Returns:
        Refreshed row dict, or ``None`` if no membership matched.
    """
    result = await session.execute(
        text(
            "update public.organization_memberships "
            "set role = :r "
            "where org_id = cast(:o as uuid) "
            "  and user_id = cast(:u as uuid) "
            "returning id::text as id, org_id::text as org_id, "
            "          user_id::text as user_id, role"
        ),
        {"r": role, "o": str(org_id), "u": str(user_id)},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def remove_member(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    user_id: uuid.UUID | str,
) -> bool:
    """Delete the ``(org_id, user_id)`` membership row.

    Returns ``True`` if a row was removed. Caller commits. Caller is
    responsible for the "at least one owner" invariant before calling.

    Args:
        session: ``pulse_member`` session.
        org_id: UUID of the active org.
        user_id: UUID of the user to remove.

    Returns:
        ``True`` if a membership row was deleted.
    """
    result = await session.execute(
        text(
            "delete from public.organization_memberships "
            "where org_id = cast(:o as uuid) "
            "  and user_id = cast(:u as uuid)"
        ),
        {"o": str(org_id), "u": str(user_id)},
    )
    return result.rowcount > 0


async def add_membership(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    user_id: uuid.UUID | str,
    role: str,
) -> None:
    """Insert a new membership row, no-op on conflict.

    Used by the invite-acceptance path. Idempotent on
    ``(org_id, user_id)`` so repeated acceptances of the same invite
    don't violate the unique constraint.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS) — invite acceptance
            runs before the user has a member-session connection.
        org_id: UUID of the org to add the user to.
        user_id: UUID of the user to add.
        role: Role to grant.
    """
    await session.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), :r) "
            "on conflict (org_id, user_id) do nothing"
        ),
        {"o": str(org_id), "u": str(user_id), "r": role},
    )


async def is_existing_user_membership(
    member_session: AsyncSession,
    admin_session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    email: str,
) -> bool:
    """Return True iff a user with ``email`` is a member of ``org_id``.

    Two-pass to avoid joining ``users`` from the ``pulse_member``
    session (no grant). Step 1: look up the user_id via admin session.
    Step 2: check the (org_id, user_id) membership row via the
    RLS-scoped member session.

    Args:
        member_session: ``pulse_member`` session, RLS-scoped.
        admin_session: ``pulse_admin`` session.
        org_id: UUID of the active org.
        email: Lower-cased email to check.

    Returns:
        ``True`` if a matching membership exists.
    """
    result = await admin_session.execute(
        text("select id::text from public.users where lower(email) = lower(:e)"),
        {"e": email},
    )
    user_id_row = result.scalar_one_or_none()
    if user_id_row is None:
        return False
    membership_result = await member_session.execute(
        text(
            "select 1 from public.organization_memberships "
            "where org_id = cast(:o as uuid) "
            "  and user_id = cast(:u as uuid) limit 1"
        ),
        {"o": str(org_id), "u": str(user_id_row)},
    )
    return membership_result.scalar() is not None
