"""Repository helpers for `recipients` — the per-respondent rows that own
each engagement's magic-link tokens. Admin CRUD runs on the ``pulse_member``
session (RLS scopes by ``org_id``); the reminder job reads these on a
BYPASSRLS session. Each recipient also carries its own answered/total
progress, computed from its ``responses`` against the engagement's cards.
"""
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Base recipient columns + a per-recipient progress rollup (answered/skipped
# vs the engagement's card count). The progress subqueries are correlated on
# the recipient + its engagement so two recipients on one engagement report
# independent progress.
_RECIPIENT_SELECT = """
    select
      r.id::text                                      as id,
      r.engagement_id::text                           as engagement_id,
      r.email, r.name, r.token,
      r.last_active_at, r.invited_at, r.last_reminded_at,
      r.reminder_count, r.unsubscribed_at, r.created_at,
      (select count(*) from public.responses rr
         where rr.recipient_id = r.id
           and rr.state in ('answered', 'skipped'))::int   as completed_count,
      (select count(*) from public.cards cd
         where cd.engagement_id = r.engagement_id)::int    as total_cards
    from public.recipients r
"""


async def list_for_engagement(session: AsyncSession, engagement_id: str) -> list[dict]:
    """Every recipient on one engagement, oldest first, each with its own
    progress rollup. RLS (member session) keeps this in-org."""
    try:
        result = await session.execute(
            text(
                f"{_RECIPIENT_SELECT} where r.engagement_id = cast(:eid as uuid) "
                "order by r.created_at"
            ),
            {"eid": engagement_id},
        )
    except Exception:
        return []
    return [dict(r) for r in result.mappings().all()]


async def email_exists(
    session: AsyncSession, *, engagement_id: str, email: str
) -> bool:
    """True if a recipient with this email (case-insensitive) already exists
    on the engagement — the route maps that to a 409 rather than tripping
    the partial unique index mid-transaction."""
    result = await session.execute(
        text(
            "select 1 from public.recipients "
            "where engagement_id = cast(:eid as uuid) and lower(email) = lower(:email)"
        ),
        {"eid": engagement_id, "email": email},
    )
    return result.scalar() is not None


async def add(
    session: AsyncSession,
    *,
    engagement_id: str,
    org_id: str,
    email: str,
    name: str | None,
) -> dict | None:
    """Mint a recipient (with its own 16-hex token) under ``engagement_id``.
    ``org_id`` comes from the active membership, never the wire body — the
    member RLS WITH CHECK rejects any other org anyway. Returns the new row
    (with a freshly minted token + zeroed progress), or None if the insert
    failed (e.g. a bad engagement_id)."""
    token = secrets.token_hex(8)
    # A fresh recipient has zero responses, so the progress rollup is
    # computed inline in RETURNING (``completed_count`` = 0, ``total_cards``
    # from the engagement's cards). RETURNING — not a same-statement SELECT —
    # because a data-modifying CTE's inserted row isn't visible to an outer
    # SELECT in the same query (both run against the pre-INSERT snapshot).
    try:
        result = await session.execute(
            text(
                "insert into public.recipients "
                "  (engagement_id, org_id, email, name, token) "
                "values (cast(:eid as uuid), cast(:org as uuid), :email, :name, :token) "
                "returning id::text, engagement_id::text, email, name, token, "
                "  last_active_at, invited_at, last_reminded_at, reminder_count, "
                "  unsubscribed_at, created_at, 0 as completed_count, "
                "  (select count(*) from public.cards cd "
                "     where cd.engagement_id = cast(:eid as uuid))::int as total_cards"
            ),
            {
                "eid": engagement_id,
                "org": org_id,
                "email": email,
                "name": name,
                "token": token,
            },
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def remove(
    session: AsyncSession, *, engagement_id: str, recipient_id: str
) -> dict | None:
    """Delete a recipient (FK cascade wipes its responses/uploads). Returns
    the deleted row (for the audit log + on-disk file cleanup) or None when
    no match in the active org."""
    try:
        result = await session.execute(
            text(
                "delete from public.recipients "
                "where id = cast(:rid as uuid) and engagement_id = cast(:eid as uuid) "
                "returning id::text, email, name"
            ),
            {"rid": recipient_id, "eid": engagement_id},
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def list_pending_invites(
    session: AsyncSession, engagement_id: str
) -> list[dict]:
    """Recipients on this engagement who can be invited but haven't been —
    a non-null email and ``invited_at is null``. Returns ``{id, email,
    name, token}`` for each, for the send-invites route to email + stamp."""
    result = await session.execute(
        text(
            "select id::text, email, name, token from public.recipients "
            "where engagement_id = cast(:eid as uuid) "
            "  and email is not null and invited_at is null "
            "order by created_at"
        ),
        {"eid": engagement_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def mark_invited(session: AsyncSession, recipient_ids: list[str]) -> None:
    """Stamp ``invited_at = now()`` on the given recipients (idempotent —
    only sets it where still null, so a re-run never moves the timestamp)."""
    if not recipient_ids:
        return
    await session.execute(
        text(
            "update public.recipients set invited_at = now() "
            "where id = any(cast(:ids as uuid[])) and invited_at is null"
        ),
        {"ids": recipient_ids},
    )


async def list_upload_paths_for_recipient(
    session: AsyncSession, recipient_id: str
) -> list[str]:
    """storage_path values for a recipient's uploads, fetched BEFORE the
    delete so the route can remove the on-disk files after the FK cascade."""
    try:
        result = await session.execute(
            text(
                "select storage_path from public.uploads "
                "where recipient_id = cast(:rid as uuid)"
            ),
            {"rid": recipient_id},
        )
    except Exception:
        return []
    return [row[0] for row in result.all()]
