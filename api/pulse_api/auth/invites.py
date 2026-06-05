"""Shared invite-acceptance helpers used by both the OAuth callback
and the password-based ``/api/invites/{token}/accept`` route.

Single source of truth for "attach this user to this invite's org":
inserts the membership (idempotent), points
``users.last_active_org_id`` at the new org, stamps
``organization_invites.accepted_at``, and records a ``member.join``
audit row. Used by:

* ``routes.oauth._attach_invite_to_user`` — OAuth-then-accept flow.
* ``routes.invites.accept_invite``        — password-based accept.

The audit hook lives here, not at the call site, so password-based and
OAuth-based acceptance produce identical activity entries.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.audit import record_audit
from pulse_api.repos import invites as invites_repo
from pulse_api.repos import memberships as memberships_repo
from pulse_api.repos import orgs as orgs_repo


async def attach_invite_to_user(
    session: AsyncSession,
    *,
    invite: dict,
    user_id: uuid.UUID | str,
) -> None:
    """Apply an invite to a user.

    Idempotent on ``(org_id, user_id)``: the membership insert is a
    no-op if the user is already in the org (multi-org owners who are
    re-invited stay where they were). Always moves
    ``last_active_org_id`` so the user lands in the new org on their
    next request.

    The ``accepted_at`` stamp is applied via
    ``invites_repo.accept_atomically`` — a no-op when another tab/path
    already claimed the invite. The OAuth callback's
    ``_find_pending_invite`` uses ``FOR UPDATE SKIP LOCKED`` so this
    helper never races against itself; this branch is just defensive
    for the second-tab edge case in the password-based flow where the
    caller has already claimed the row via
    ``find_invite_by_token_hash(for_update=True)``.

    A ``member.join`` audit row is written before the
    ``accept_atomically`` stamp so the activity feed shows the join
    event in the new org's history. The actor is the joining user
    themselves — they performed the action that triggered the row.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS). Invite acceptance
            spans the user, the membership table, and the invite table —
            all cross-org by definition.
        invite: Row dict with at least ``id``, ``org_id``, ``role``.
        user_id: UUID of the user to attach.
    """
    await memberships_repo.add_membership(
        session,
        org_id=invite["org_id"],
        user_id=user_id,
        role=str(invite["role"]),
    )
    await orgs_repo.set_last_active_org(
        session, user_id=user_id, org_id=invite["org_id"]
    )
    await record_audit(
        session,
        org_id=invite["org_id"],
        user_id=user_id,
        action="member.join",
        target_type="member",
        target_id=str(user_id),
        metadata={
            "invite_id": str(invite["id"]),
            "role": str(invite["role"]),
        },
    )
    await invites_repo.accept_atomically(session, invite["id"])
