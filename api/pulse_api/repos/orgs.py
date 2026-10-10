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

# The org row the member-scoped routes read back. The webhook secret is
# reduced to a boolean here so it can never reach a response by accident.
_ORG_COLUMNS = (
    "id::text as id, name, slug, logo_path, branding, "
    "reactive_cards_allowed, webhook_url, "
    "(webhook_secret is not null) as webhook_secret_set, created_at"
)


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
        ``{id, name, slug, logo_path, branding, reactive_cards_allowed,
        created_at}`` or ``None``. ``branding`` is the JSONB dict
        (asyncpg decodes it to a ``dict``) or ``None`` when unset.
    """
    result = await session.execute(
        text(
            f"select {_ORG_COLUMNS} "
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
            f"returning {_ORG_COLUMNS}"
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


async def set_branding(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    branding: dict | None,
) -> dict[str, object] | None:
    """Set or clear the ``branding`` JSONB column on the org row.

    RLS WITH CHECK on the org-scoped session refuses any other ``id`` so
    a forgotten predicate in the SQL can't update a different org. Pass
    ``branding=None`` to store SQL NULL (reset the deck to its built-in
    defaults).

    The dict is serialized to a JSON string and cast to ``jsonb`` so
    asyncpg can bind it without a server-side type-inference ambiguity —
    the same pattern :func:`pulse_api.audit.record_audit` uses for its
    ``metadata`` column.

    Args:
        session: ``pulse_member`` session with ``pulse.org_id`` set.
        org_id: UUID of the active org.
        branding: Branding override dict, or ``None`` to clear it.

    Returns:
        Refreshed row dict
        (``{id, name, slug, logo_path, branding, created_at}``), or
        ``None`` if the org was not found.
    """
    import json

    encoded = json.dumps(branding) if branding else None
    result = await session.execute(
        text(
            "update public.organizations "
            "set branding = cast(:b as jsonb) "
            "where id = cast(:o as uuid) "
            f"returning {_ORG_COLUMNS}"
        ),
        {"b": encoded, "o": str(org_id)},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def set_webhook(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    url: str | None,
    secret: str | None,
    keep_secret: bool = False,
) -> dict[str, object] | None:
    """Set or clear the org's outbound webhook (``pulse_api/webhooks.py``).

    ``keep_secret=True`` updates only the URL and leaves the stored secret
    as it is (the owner changed the URL without re-pasting the secret).
    Pass ``url=None, secret=None`` to clear both. RLS WITH CHECK on the
    org-scoped session refuses any other ``id``.

    Returns:
        Refreshed row dict (secret reduced to ``webhook_secret_set``), or
        ``None`` if the org was not found.
    """
    secret_sql = "webhook_secret" if keep_secret else "cast(:s as text)"
    result = await session.execute(
        text(
            "update public.organizations "
            f"set webhook_url = cast(:u as text), webhook_secret = {secret_sql} "
            "where id = cast(:o as uuid) "
            f"returning {_ORG_COLUMNS}"
        ),
        {"u": url, "o": str(org_id), **({} if keep_secret else {"s": secret})},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


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
            "  and revoked_at is null "
            "  and expires_at > now()"
        ),
        {"o": str(org_id)},
    )
    return int(result.scalar() or 0)


async def list_all_with_summary(
    session: AsyncSession, *, limit: int = 50
) -> list[dict[str, object]]:
    """List every organization with member/invite counts + top owner emails.

    Superadmin-only — uses a BYPASSRLS session (``pulse_admin``) so the
    result spans every org in the database, ordered by ``created_at``
    descending (newest first). The ``owner_emails`` column is a
    denormalized top-3 owners (oldest joiners first), joined to a single
    comma-separated string so the UI can render it without an extra
    round-trip per row.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        limit: Maximum number of org rows to return.

    Returns:
        List of dicts with keys ``id``, ``name``, ``slug``,
        ``created_at``, ``member_count``, ``pending_invite_count``,
        ``owner_emails`` (``list[str]``), ``reactive_cards_allowed``.
    """
    result = await session.execute(
        text(
            """
            select
                o.id::text as id,
                o.name,
                o.slug,
                o.created_at,
                o.reactive_cards_allowed,
                coalesce((
                    select count(*)::int
                    from public.organization_memberships m
                    where m.org_id = o.id
                ), 0) as member_count,
                coalesce((
                    select count(*)::int
                    from public.organization_invites i
                    where i.org_id = o.id
                      and i.accepted_at is null
                      and i.revoked_at is null
                      and i.expires_at > now()
                ), 0) as pending_invite_count,
                coalesce((
                    select array_agg(t.email order by t.created_at)
                    from (
                        select u.email, m.created_at
                        from public.organization_memberships m
                        join public.users u on u.id = m.user_id
                        where m.org_id = o.id and m.role = 'owner'
                        order by m.created_at
                        limit 3
                    ) as t
                ), array[]::text[]) as owner_emails
            from public.organizations o
            order by o.created_at desc
            limit :lim
            """
        ),
        {"lim": int(limit)},
    )
    rows: list[dict[str, object]] = []
    for r in result.mappings().all():
        d = dict(r)
        # asyncpg surfaces text[] as a list[str]; normalize to plain list.
        emails = d.get("owner_emails") or []
        d["owner_emails"] = list(emails)
        rows.append(d)
    return rows


async def get_by_id(
    session: AsyncSession, org_id: uuid.UUID | str
) -> dict[str, object] | None:
    """Fetch an org by id, regardless of the caller's active org.

    Uses BYPASSRLS — superadmin routes need to inspect orgs they aren't
    members of.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        org_id: UUID of the target org.

    Returns:
        ``{id, name, slug, logo_path, reactive_cards_allowed, created_at}``
        or ``None``.
    """
    try:
        as_uuid = uuid.UUID(str(org_id))
    except (TypeError, ValueError):
        return None
    result = await session.execute(
        text(
            "select id::text as id, name, slug, logo_path, "
            "reactive_cards_allowed, created_at "
            "from public.organizations where id = cast(:o as uuid)"
        ),
        {"o": str(as_uuid)},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def set_reactive_cards_allowed(
    session: AsyncSession, org_id: uuid.UUID | str, *, allowed: bool
) -> dict[str, object] | None:
    """Superadmin-only: set the org-level reactive-cards gate.

    This is the second of the three reactive-cards feature gates (see
    ``pulse_api.reactive`` module docstring) — an org member can only
    flip their own engagement's ``reactive_cards_enabled`` toggle to
    ``True`` when this is also ``True`` (enforced by
    ``reactive.ensure_org_allowed`` at the ``PATCH
    /api/admin/engagements/{id}`` and MCP ``pulse_update_engagement``
    call sites). BYPASSRLS — a superadmin operates across tenants by
    definition.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        org_id: UUID of the target org.
        allowed: New value for ``reactive_cards_allowed``.

    Returns:
        ``{id, name, slug, logo_path, reactive_cards_allowed,
        created_at}`` for the updated row, or ``None`` if no org with
        that id existed.
    """
    result = await session.execute(
        text(
            "update public.organizations set reactive_cards_allowed = :a "
            "where id = cast(:o as uuid) "
            "returning id::text as id, name, slug, logo_path, "
            "reactive_cards_allowed, created_at"
        ),
        {"a": allowed, "o": str(org_id)},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def find_by_slug(
    session: AsyncSession, slug: str
) -> dict[str, object] | None:
    """Return the org row whose slug matches, or ``None``.

    Used by the superadmin create-org duplicate-slug gate. BYPASSRLS
    because the slug uniqueness check spans every tenant.

    Args:
        session: ``pulse_admin`` session.
        slug: Lower-case URL-safe slug.

    Returns:
        ``{id, name, slug}`` row dict, or ``None``.
    """
    result = await session.execute(
        text(
            "select id::text as id, name, slug "
            "from public.organizations where slug = :s limit 1"
        ),
        {"s": slug},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def create_org(
    session: AsyncSession, *, name: str, slug: str
) -> dict[str, object]:
    """Insert a new organization row and return it.

    Caller commits. BYPASSRLS because the row by definition doesn't yet
    belong to any active-org context.

    Args:
        session: ``pulse_admin`` session.
        name: Display name (already validated).
        slug: Lower-case URL-safe slug (already validated + uniqueness
            checked).

    Returns:
        ``{id, name, slug, created_at}`` row dict.
    """
    result = await session.execute(
        text(
            "insert into public.organizations (name, slug) "
            "values (:n, :s) "
            "returning id::text as id, name, slug, created_at, logo_path"
        ),
        {"n": name, "s": slug},
    )
    return dict(result.mappings().one())


async def client_count(
    session: AsyncSession, org_id: uuid.UUID | str
) -> int:
    """Return the number of ``clients`` rows for ``org_id``.

    Used by the superadmin delete-org safety gate — deleting an org that
    has clients would cascade-wipe customer data, so the route refuses
    with 409 when this is non-zero.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        org_id: UUID of the org to check.

    Returns:
        Integer count of clients in the org.
    """
    result = await session.execute(
        text(
            "select count(*)::int from public.engagements "
            "where org_id = cast(:o as uuid)"
        ),
        {"o": str(org_id)},
    )
    return int(result.scalar() or 0)


async def delete_org(
    session: AsyncSession, org_id: uuid.UUID | str
) -> bool:
    """Hard-delete an organization and its memberships + invites.

    Order matters: explicitly remove memberships, then invites, then
    audit logs, then the org row itself. The FK on
    ``users.last_active_org_id`` is ``on delete set null`` so it
    self-cleans. Tenant tables (``clients``, ``cards``, etc.) are FK'd
    ``on delete cascade``, but the route enforces a stricter "no
    clients" precondition before this is called — see
    :func:`client_count`.

    Caller commits.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        org_id: UUID of the org to delete.

    Returns:
        ``True`` if the org row was removed, ``False`` if no org with
        that id existed.
    """
    as_str = str(org_id)
    # Memberships first — there's a unique (org_id, user_id) so a stray
    # row would block reuse of the slug + email pair.
    await session.execute(
        text(
            "delete from public.organization_memberships "
            "where org_id = cast(:o as uuid)"
        ),
        {"o": as_str},
    )
    # Invites (pending and historical) — no cascade from organizations
    # would touch these on its own without on-delete-cascade, but we
    # have it. Explicit delete keeps the order deterministic.
    await session.execute(
        text(
            "delete from public.organization_invites "
            "where org_id = cast(:o as uuid)"
        ),
        {"o": as_str},
    )
    # Audit logs are scoped to the org too — they would cascade, but
    # mention them in the order chain for the next person reading this.
    await session.execute(
        text(
            "delete from public.audit_logs where org_id = cast(:o as uuid)"
        ),
        {"o": as_str},
    )
    result = await session.execute(
        text(
            "delete from public.organizations "
            "where id = cast(:o as uuid)"
        ),
        {"o": as_str},
    )
    return result.rowcount > 0


async def delete_impact(
    session: AsyncSession, org_id: uuid.UUID | str
) -> dict[str, int]:
    """What deleting this org would erase — shown to the superadmin before
    they confirm. Runs on the BYPASSRLS session (counts across the org)."""
    row = (
        await session.execute(
            text(
                """
                select
                  (select count(*) from public.clients where org_id = cast(:o as uuid))      as clients,
                  (select count(*) from public.engagements where org_id = cast(:o as uuid))  as engagements,
                  (select count(*) from public.cards where org_id = cast(:o as uuid))        as questions,
                  (select count(*) from public.recipients where org_id = cast(:o as uuid))   as respondents,
                  (select count(*) from public.responses where org_id = cast(:o as uuid))    as answers,
                  (select count(*) from public.uploads where org_id = cast(:o as uuid))      as files,
                  (select count(*) from public.organization_memberships
                     where org_id = cast(:o as uuid))                                        as members,
                  (select count(*) from public.organization_invites
                     where org_id = cast(:o as uuid) and accepted_at is null
                       and revoked_at is null)                                               as pending_invites,
                  (select count(*) from public.api_keys where org_id = cast(:o as uuid))     as api_keys
                """
            ),
            {"o": str(org_id)},
        )
    ).mappings().one()
    return {k: int(v) for k, v in row.items()}


async def delete_org_and_data(
    session: AsyncSession, org_id: uuid.UUID | str
) -> tuple[bool, list[str]]:
    """Erase an org and everything in it. Returns ``(deleted, file_paths)``
    where ``file_paths`` are the on-disk uploads (answer files, voice notes,
    the org logo) for the caller to remove AFTER committing.

    Order matters because a few FKs to ``organizations`` are RESTRICT
    (engagements, cards, responses, uploads, api_keys): engagements go
    first — their cascade removes cards, recipients, responses, uploads and
    card generations — then API keys, then :func:`delete_org`, whose own
    cascade takes clients, contacts, memberships, invites, OAuth grants and
    the org's audit log. User accounts are kept. Caller commits.
    """
    as_str = str(org_id)
    paths = [
        r[0]
        for r in (
            await session.execute(
                text(
                    "select storage_path from public.uploads "
                    "where org_id = cast(:o as uuid)"
                ),
                {"o": as_str},
            )
        ).all()
    ]
    logo = (
        await session.execute(
            text(
                "select logo_path from public.organizations "
                "where id = cast(:o as uuid)"
            ),
            {"o": as_str},
        )
    ).scalar_one_or_none()
    if logo:
        paths.append(str(logo))
    await session.execute(
        text("delete from public.engagements where org_id = cast(:o as uuid)"),
        {"o": as_str},
    )
    await session.execute(
        text("delete from public.api_keys where org_id = cast(:o as uuid)"),
        {"o": as_str},
    )
    deleted = await delete_org(session, org_id)
    return deleted, paths
