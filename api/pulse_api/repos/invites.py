"""Repository helpers for `organization_invites`.

Token storage rule: the raw signed token NEVER lives in the DB. Only its
SHA-256 hash (column ``token_hash``) is persisted. Resolution paths take
the raw token, hash it, and look up the row — matches the API-key pattern.

Two role contexts hit these helpers:

* The org-scoped (``pulse_member``) session — for the owner-gated list /
  create / revoke endpoints. RLS narrows automatically to the active org.
* The BYPASSRLS (``pulse_admin``) session — for the public token-resolve
  + accept endpoints. The token IS the auth; the route layer has no org
  context until after the token resolves to one.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.tokens import issue_token
from pulse_api.config import settings


def hash_invite_token(raw_token: str) -> str:
    """Return the SHA-256 hex of the raw signed invite token.

    Matches the storage hash format on disk (``token_hash`` column).

    Args:
        raw_token: The signed token from
            ``issue_token('org-invite', {invite_id: ...})``.

    Returns:
        64-char lowercase hex digest.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def list_pending_invite_rows(
    session: AsyncSession, org_id: uuid.UUID | str
) -> list[dict[str, object]]:
    """List pending (non-expired, unaccepted) invites for ``org_id``.

    Returns bare invite rows — no join into ``users`` — so this query
    can run cleanly on a ``pulse_member`` session.

    Args:
        session: ``pulse_member`` session, RLS-scoped to ``org_id``.
        org_id: UUID of the active org.

    Returns:
        List of ``{id, email, role, created_at, expires_at,
        invited_by_user_id}`` dicts ordered by ``created_at`` desc.
    """
    result = await session.execute(
        text(
            "select id::text as id, email, role, "
            "       created_at, expires_at, "
            "       invited_by_user_id::text as invited_by_user_id "
            "from public.organization_invites "
            "where org_id = cast(:o as uuid) "
            "  and accepted_at is null "
            "  and revoked_at is null "
            "  and expires_at > now() "
            "order by created_at desc"
        ),
        {"o": str(org_id)},
    )
    return [dict(row) for row in result.mappings().all()]


async def find_pending_invite_for_email(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    email: str,
) -> dict[str, object] | None:
    """Return the pending invite for ``(org_id, email)``, if any.

    Pending = ``accepted_at is null and revoked_at is null and
    expires_at > now()``. Used by the create-invite duplicate-detection
    gate.

    Args:
        session: ``pulse_member`` session.
        org_id: UUID of the active org.
        email: Lower-cased invitee email.

    Returns:
        Row dict or ``None``.
    """
    result = await session.execute(
        text(
            "select id::text as id, email, role, expires_at "
            "from public.organization_invites "
            "where org_id = cast(:o as uuid) "
            "  and lower(email) = lower(:e) "
            "  and accepted_at is null "
            "  and revoked_at is null "
            "  and expires_at > now() "
            "limit 1"
        ),
        {"o": str(org_id), "e": email},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def create_invite(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str,
    email: str,
    role: str,
    invited_by_user_id: uuid.UUID | str,
    expires_at: datetime,
) -> tuple[dict[str, object], str]:
    """Insert a new invite row and return ``(row, raw_token)``.

    Two-step: insert with a placeholder hash to get the ``id``, sign the
    real token over ``{invite_id}``, then update the hash. The raw
    token is returned to the caller so it can be embedded in the email
    link, but is never persisted.

    Caller commits.

    Args:
        session: ``pulse_member`` session — RLS WITH CHECK enforces
            ``org_id = pulse.org_id``.
        org_id: UUID of the active org.
        email: Lower-cased invitee email.
        role: ``"owner"`` or ``"member"``.
        invited_by_user_id: UUID of the user creating the invite.
        expires_at: Hard expiry (naive UTC).

    Returns:
        ``(row_dict, raw_token)`` — the dict has the same keys as a
        list-pending row, and the raw token is the signed value to
        embed in the invite link.
    """
    # Step 1: insert with a placeholder hash. The hash column has a
    # unique constraint, so seed it with the new row id (also unique).
    row = (
        await session.execute(
            text(
                "insert into public.organization_invites "
                "(org_id, email, role, token_hash, invited_by_user_id, "
                " expires_at) "
                "values (cast(:o as uuid), :e, :r, "
                "        encode(sha256(random()::text::bytea), 'hex'), "
                "        cast(:b as uuid), :x) "
                "returning id::text as id, email, role, "
                "          created_at, expires_at"
            ),
            {
                "o": str(org_id),
                "e": email.lower(),
                "r": role,
                "b": str(invited_by_user_id),
                "x": expires_at,
            },
        )
    ).mappings().one()

    # Step 2: sign the real token, swap the placeholder hash in.
    raw_token = issue_token("org-invite", {"invite_id": row["id"]})
    real_hash = hash_invite_token(raw_token)
    await session.execute(
        text(
            "update public.organization_invites "
            "set token_hash = :h "
            "where id = cast(:i as uuid)"
        ),
        {"h": real_hash, "i": row["id"]},
    )
    return dict(row), raw_token


async def find_invite_by_token_hash(
    session: AsyncSession,
    token_hash: str,
    *,
    for_update: bool = False,
) -> dict[str, object] | None:
    """Resolve a token hash to an invite row joined to its org.

    Cross-org by definition — the public invite-accept endpoint has no
    org context until the lookup succeeds. Must run under the BYPASSRLS
    (``pulse_admin``) session.

    Args:
        session: ``pulse_admin`` session.
        token_hash: SHA-256 hex of the raw token.
        for_update: When True, append ``FOR UPDATE`` so the matching
            invite row is row-locked for the rest of the transaction.
            Two simultaneous acceptance requests both calling with
            ``for_update=True`` then serialize at the DB layer — the
            second blocks until the first commits, and the conditional
            ``accept_atomically`` UPDATE on the slow path returns False.
            Without this, both could pass the "still pending" check and
            race into user creation / password set.

    Returns:
        ``{id, org_id, org_name, email, role, expires_at, accepted_at,
        revoked_at, token_hash, invited_by_user_id}`` dict or ``None``
        if no row matches the hash.
    """
    base_sql = (
        "select i.id::text as id, i.org_id::text as org_id, "
        "       o.name as org_name, i.email, i.role, "
        "       i.expires_at, i.accepted_at, i.revoked_at, "
        "       i.token_hash, "
        "       i.invited_by_user_id::text as invited_by_user_id "
        "from public.organization_invites i "
        "join public.organizations o on o.id = i.org_id "
        "where i.token_hash = :h "
        "limit 1"
    )
    if for_update:
        # FOR UPDATE OF i — lock the invite row, not the joined org row
        # (we don't mutate orgs from this path and locking it would
        # serialize unrelated invite-accept traffic across the org).
        base_sql = base_sql + " for update of i"
    result = await session.execute(text(base_sql), {"h": token_hash})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def accept_atomically(
    session: AsyncSession, invite_id: uuid.UUID | str
) -> bool:
    """Conditionally stamp ``accepted_at = now()`` on a still-pending invite.

    Returns ``True`` iff a row was actually updated — that is, the
    invite was neither already accepted nor revoked at the moment the
    UPDATE ran. Combined with ``find_invite_by_token_hash(...,
    for_update=True)`` at the top of the acceptance route, this gives
    the two-tab race the only safe outcome: the first request wins,
    the second sees ``False`` and bails before doing any user-creating
    side effects.

    Args:
        session: ``pulse_admin`` session (cross-org by definition for
            the public accept path).
        invite_id: UUID of the invite to claim.

    Returns:
        ``True`` if this call claimed the invite, ``False`` if it was
        already accepted or revoked (or simply not present).
    """
    result = await session.execute(
        text(
            "update public.organization_invites "
            "set accepted_at = now() "
            "where id = cast(:i as uuid) "
            "  and accepted_at is null "
            "  and revoked_at is null "
            "returning id"
        ),
        {"i": str(invite_id)},
    )
    return result.first() is not None


async def revoke_pending(
    session: AsyncSession,
    *,
    invite_id: uuid.UUID | str,
    org_id: uuid.UUID | str,
) -> bool:
    """Revoke a pending invite by stamping ``revoked_at = now()``.

    Distinct from acceptance: a revoked invite resolves to
    ``status = "revoked"`` on the public endpoint, so the recipient
    sees an actionable "this invite was revoked" message instead of
    the misleading "already used" UI a stamped ``accepted_at`` would
    have rendered. The ``WHERE accepted_at IS NULL AND revoked_at IS
    NULL`` guard ensures a revoke can't undo a successful acceptance
    nor stomp a prior revoke.

    Args:
        session: ``pulse_member`` session.
        invite_id: UUID of the invite to revoke.
        org_id: UUID of the active org — RLS already enforces this,
            but passing it makes the SQL self-documenting and stops a
            future BYPASSRLS reuse from accidentally cross-org-revoking.

    Returns:
        ``True`` if a pending row was revoked, ``False`` if no match
        (already accepted, already revoked, expired, wrong org, or
        unknown id).
    """
    result = await session.execute(
        text(
            "update public.organization_invites "
            "set revoked_at = now() "
            "where id = cast(:i as uuid) "
            "  and org_id = cast(:o as uuid) "
            "  and accepted_at is null "
            "  and revoked_at is null"
        ),
        {"i": str(invite_id), "o": str(org_id)},
    )
    return result.rowcount > 0


def invite_status(invite: dict[str, object]) -> str:
    """Return a status label for an invite row.

    Pure function — no DB. Precedence:

    1. ``"revoked"``  — ``revoked_at is not null`` (checked first so
       a row that ended up with both timestamps set during a future
       backfill still surfaces as revoked).
    2. ``"accepted"`` — ``accepted_at is not null``.
    3. ``"expired"``  — past ``expires_at`` and not yet
       accepted/revoked.
    4. ``"pending"``  — none of the above.

    Args:
        invite: Row dict with at least ``accepted_at``, ``revoked_at``
            and ``expires_at``.

    Returns:
        Status string.
    """
    if invite.get("revoked_at") is not None:
        return "revoked"
    if invite.get("accepted_at") is not None:
        return "accepted"
    expires_at = invite.get("expires_at")
    if isinstance(expires_at, datetime):
        now = _utcnow()
        # Postgres returns timestamptz as tz-aware; the migration uses
        # timestamptz on this column. Normalise both sides to UTC-aware
        # so the comparison works regardless of which the driver chose.
        if expires_at.tzinfo is None:
            expires_aware = expires_at.replace(tzinfo=now.tzinfo)
        else:
            expires_aware = expires_at
        if expires_aware <= now:
            return "expired"
    return "pending"


def _utcnow() -> datetime:
    """Return current UTC time as a tz-aware datetime (UTC)."""
    from datetime import timezone

    return datetime.now(timezone.utc)


# Re-export the spec setting so the route layer can reach it via the
# repo without importing config separately.
INVITE_MAX_AGE_SECONDS = settings.invite_token_max_age_seconds
