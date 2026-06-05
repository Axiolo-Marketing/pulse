"""Repository helpers for ``audit_logs``.

Reads only. Writes go through :func:`pulse_api.audit.record_audit` so
the action enum and shape stay in one place.

Two-pass actor enrichment
-------------------------

The activity endpoint returns each entry's actor as ``{user_id, email,
name}``. The ``pulse_member`` role has SELECT on ``audit_logs`` but
NOT on ``users`` (see ``api/migrations/versions/0004_multi_tenant.py``
grants), so the route does the same two-pass dance as the members
listing:

1. ``list_for_org`` on the ``pulse_member`` session — RLS scopes the
   read to the active org; returns bare rows with ``user_id`` but no
   joined display fields.
2. ``memberships.list_user_display_fields`` on the BYPASSRLS
   ``pulse_admin`` session — looks up emails/names by user_id.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_for_org(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    limit: int,
    cursor: tuple[datetime, uuid.UUID | str] | None = None,
    actor_user_id: uuid.UUID | str | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    """List audit entries for ``org_id`` in reverse-chronological order.

    Cursor pagination: pass the previous page's last
    ``(created_at, id)`` to skip past already-shown rows. Both fields
    are needed because two audit rows can share ``created_at`` (e.g.,
    ``superadmin.create_org`` writes ``org.create`` and ``member.invite``
    in the same transaction). The predicate
    ``(created_at, id) < (:cursor_ts, :cursor_id)`` mirrors the
    ``ORDER BY created_at DESC, id DESC`` sort so no row straddles a
    page boundary.

    The ``where org_id = ...`` predicate is defensive — the RLS policy
    on a ``pulse_member`` session already narrows to the active org —
    but it's cheap and keeps the query correct on any session role.

    Args:
        session: ``pulse_member`` session (RLS-scoped) or ``pulse_admin``.
        org_id: UUID of the org whose entries we want.
        limit: Max rows to return. The caller is responsible for clamping
            the request value into a sane range.
        cursor: ``(created_at, id)`` of the previous page's last row.
            Strict ``<`` (lexicographic) so we don't re-yield it.
        actor_user_id: Optional filter — only entries where
            ``user_id = :actor``.
        action: Optional exact-match filter on the action enum string.

    Returns:
        List of dicts with keys ``id``, ``created_at``, ``user_id``,
        ``action``, ``target_type``, ``target_id``, ``metadata``.
    """
    clauses: list[str] = ["org_id = cast(:org_id as uuid)"]
    params: dict[str, Any] = {"org_id": str(org_id), "limit": int(limit)}
    if cursor is not None:
        cursor_ts, cursor_id = cursor
        clauses.append(
            "(created_at, id) < (:cursor_ts, cast(:cursor_id as uuid))"
        )
        params["cursor_ts"] = cursor_ts
        params["cursor_id"] = str(cursor_id)
    if actor_user_id is not None:
        clauses.append("user_id = cast(:actor_user_id as uuid)")
        params["actor_user_id"] = str(actor_user_id)
    if action is not None:
        clauses.append("action = :action")
        params["action"] = action

    where_sql = " and ".join(clauses)
    result = await session.execute(
        text(
            f"select id::text, created_at, "
            f"       user_id::text as user_id, "
            f"       action, target_type, target_id, "
            f"       metadata "
            f"from public.audit_logs "
            f"where {where_sql} "
            f"order by created_at desc, id desc "
            f"limit :limit"
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]
