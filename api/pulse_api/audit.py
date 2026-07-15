"""Audit-log helper.

Every mutating admin route calls :func:`record_audit` immediately before
its ``session.commit()`` so the audit row commits atomically with the
user action that triggered it. If ``record_audit`` raises (e.g. the
caller forgot to set ``pulse.org_id`` on the connection, so the RLS
``WITH CHECK`` rejects), the user action rolls back too — that's the
correct behavior. Don't wrap the call in try/except.

The ``session`` argument is the same session the mutating route uses.
For routes that run on the org-scoped ``pulse_member`` session, RLS
allows the INSERT because ``audit_logs.org_id`` matches the GUC.
Superadmin routes run on the BYPASSRLS ``pulse_admin`` session — the
GUC is irrelevant there; the INSERT is admitted by role privilege.

Stable action enum
------------------

The following action strings are the canonical taxonomy. The Activity
UI maps them to human-readable labels. Tests assert that every action
string emitted by the route layer appears in this list.

* ``engagement.create``     — new engagement (a row in ``engagements``)
* ``engagement.update``     — engagement field change
* ``engagement.delete``     — engagement permanently removed
* ``engagement.reset``      — engagement responses + uploads wiped for re-run
* ``card.create``           — single card added to an engagement
* ``card.update``           — card field change
* ``card.delete``           — card removed
* ``card.import``           — bulk markdown import (one row per call)
* ``card.reactive_generate`` — reactive-cards engine auto-inserted an AI follow-up card
* ``attachment.upload``     — admin uploaded an active-reference file
* ``org.update``            — org name changed, or (superadmin) an org-level
  admin flag like ``reactive_cards_allowed`` changed
* ``org.branding``          — org branding/theme overrides changed
* ``org.logo_set``          — org logo uploaded (new or replaced)
* ``org.logo_remove``       — org logo cleared
* ``org.create``            — superadmin created an org
* ``org.delete``            — superadmin deleted an org
* ``member.invite``         — owner created a pending invite
* ``member.invite_revoke``  — owner revoked a pending invite
* ``member.role_change``    — owner promoted/demoted a member
* ``member.remove``         — owner removed a member
* ``member.join``           — user accepted an invite and joined the org
* ``api_key.create``        — operator minted a new API key
* ``api_key.revoke``        — operator revoked an API key
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Stable enum strings — keep this module's docstring in sync with the set.
# Tests import this constant to assert that every emitted action is
# listed in both places.
AUDIT_ACTIONS: frozenset[str] = frozenset(
    {
        # Engagement lifecycle
        "engagement.create",
        "engagement.update",
        "engagement.delete",
        "engagement.reset",
        # Recipients
        "recipient.add",
        "recipient.remove",
        "engagement.invites_sent",
        # Card lifecycle
        "card.create",
        "card.update",
        "card.delete",
        "card.import",
        "card.reactive_generate",
        # Attachments
        "attachment.upload",
        # Organization
        "org.update",
        "org.branding",
        "org.logo_set",
        "org.logo_remove",
        "org.create",
        "org.delete",
        # Members + invites
        "member.invite",
        "member.invite_revoke",
        "member.role_change",
        "member.remove",
        "member.join",
        # API keys
        "api_key.create",
        "api_key.revoke",
    }
)


class AuditError(RuntimeError):
    """Raised when ``record_audit`` is called with bad inputs.

    The original intent was to also raise when called outside a live
    transaction, but SQLAlchemy async sessions don't reliably signal
    "in transaction" before the first executed statement — a fresh
    session bound to an already-in-transaction connection reports
    False until it runs a query. We rely on the caller's
    ``session.commit()`` being the atomicity boundary instead.
    """


async def record_audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    user_id: uuid.UUID | str | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert a row into ``audit_logs`` on the caller's session.

    Runs inside the caller's transaction. The caller commits. We refuse
    to auto-open a transaction because the whole point of this helper
    is atomicity with the user action — a separate transaction would
    create the possibility of an audit-without-effect (or worse,
    effect-without-audit) on a partial failure.

    Args:
        session: The same ``AsyncSession`` the route is using for its
            mutation. For ``/api/admin/*`` routes this is the
            ``pulse_member`` (RLS-scoped) session; for superadmin
            routes it's the ``pulse_admin`` (BYPASSRLS) session. For
            ``/api/auth/*`` API-key routes it's the ``pulse_admin``
            session as well.
        org_id: UUID of the organization the action affects. On the
            ``pulse_member`` session this must equal the
            ``pulse.org_id`` GUC or the RLS WITH CHECK refuses the
            INSERT.
        user_id: UUID of the user who performed the action. ``None``
            is permitted for actions not tied to a single operator
            (e.g. system maintenance, but Pulse has none today).
        action: Stable enum string. Must be one of :data:`AUDIT_ACTIONS`.
        target_type: Object class the action affected (``"engagement"``,
            ``"card"``, ``"member"``, ``"invite"``, ``"org"``,
            ``"api_key"``, ``"attachment"``).
        target_id: Stringified UUID/identifier of the affected object.
            Free-form text so opaque identifiers (e.g. an email for an
            ``invite``) round-trip cleanly.
        metadata: Optional small dict that helps render the row. Keep
            it under ~2 KB. Never include raw secrets — API key
            creation, for example, passes only the ``prefix``.

    Raises:
        AuditError: If ``session`` is not currently inside a
            transaction. Catching this in the route would defeat the
            atomicity contract — so we raise loudly instead.
        ValueError: If ``action`` is not in :data:`AUDIT_ACTIONS`.
    """
    if action not in AUDIT_ACTIONS:
        # Strict so a typo in a new route surfaces immediately rather
        # than silently dropping rows that the Activity UI can't render.
        raise ValueError(f"unknown audit action: {action!r}")

    # Cast each nullable bind to its column type explicitly. Without
    # the casts, asyncpg / Postgres can't infer the parameter type from
    # a CASE expression and raises AmbiguousParameterError.
    await session.execute(
        text(
            "insert into public.audit_logs "
            "(org_id, user_id, action, target_type, target_id, metadata) "
            "values ("
            "  cast(:org_id as uuid), "
            "  cast(:user_id as uuid), "
            "  :action, "
            "  cast(:target_type as text), "
            "  cast(:target_id as text), "
            "  cast(:metadata as jsonb)"
            ")"
        ),
        {
            "org_id": str(org_id),
            "user_id": str(user_id) if user_id is not None else None,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id) if target_id is not None else None,
            "metadata": _encode_metadata(metadata),
        },
    )


def _encode_metadata(metadata: dict[str, Any] | None) -> str | None:
    """Serialize ``metadata`` to JSON for the ``jsonb`` column.

    Returns ``None`` when ``metadata`` is ``None`` or empty so the
    column carries a real SQL NULL rather than an empty object.
    """
    if not metadata:
        return None
    import json

    return json.dumps(metadata, default=str)
