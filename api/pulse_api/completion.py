"""Completion alerts — email the engagement owner when a respondent finishes.

A respondent has *finished* when every card they can see (the shared deck plus
any AI follow-ups generated for them) has an ``answered`` or ``skipped``
response. The moment that first happens, the engagement's owner
(``engagements.created_by``, if still a member of the org; otherwise the org's
owners) gets one email saying who finished and linking to the engagement.

Trigger: ``routes/client_api.py::save_response`` runs the cheap
``alert_due`` check on the request's own session (which already sees the
save it just made) and, only when it passes, calls ``schedule_completion_check``.
Scheduling follows ``reactive.schedule_generation``: a detached
``asyncio.create_task`` with a strong-reference registry, never FastAPI
``BackgroundTasks`` — the response row only commits at ``get_anon_session``
teardown, so the job retries its claim briefly while that commit lands.

AI follow-ups: when the same save also scheduled a reactive generation, the
job first waits for it. If it adds cards to the respondent's deck they are no
longer finished, the claim doesn't match, and no email goes out until they
answer those too.

Claim: ``recipients.completed_notified_at`` (migration 0020), set by a
conditional UPDATE that re-checks completeness, so a recipient is alerted
once — again only if the operator later adds cards and they finish those as
well. Uses ``clock_timestamp()`` rather than ``now()`` so the comparison with
``cards.created_at`` holds inside a single long transaction too.

All DB access uses the BYPASSRLS ``admin_engine`` on short sessions; emails
are sent with no session open. Failures are logged and never surface to the
respondent.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import email as email_module
from pulse_api.auth.email_messages import respondent_finished_email
from pulse_api.config import settings
from pulse_api.db import admin_engine

logger = logging.getLogger(__name__)

# The claim retries while the triggering request's commit lands (normally a
# few ms) — same budget as the reactive engine's context-load retry.
_CLAIM_MAX_ATTEMPTS = 10
_CLAIM_RETRY_SECONDS = 0.3
# Upper bound on waiting for a reactive generation scheduled by the same save.
_GENERATION_WAIT_SECONDS = 120.0

_pending_tasks: set[asyncio.Task] = set()

# Cards the recipient can see / of those, how many they've answered or
# skipped. Shared by the in-request check and the claim.
_VISIBLE_CARDS = (
    "(select count(*) from public.cards c "
    "  where c.engagement_id = r.engagement_id "
    "    and (c.recipient_id is null or c.recipient_id = r.id))"
)
_DONE_CARDS = (
    "(select count(*) from public.responses rr "
    "  join public.cards c on c.id = rr.card_id "
    "  where rr.recipient_id = r.id "
    "    and rr.state in ('answered', 'skipped') "
    "    and (c.recipient_id is null or c.recipient_id = r.id))"
)

# Finished, and not already alerted for the cards they can see now.
_DUE = (
    f"{_VISIBLE_CARDS} > 0 "
    f"and {_DONE_CARDS} >= {_VISIBLE_CARDS} "
    "and (r.completed_notified_at is null "
    "     or r.completed_notified_at < (select max(c.created_at) from public.cards c "
    "          where c.engagement_id = r.engagement_id "
    "            and (c.recipient_id is null or c.recipient_id = r.id)))"
)

IS_DUE_SQL = f"select {_DUE} from public.recipients r where r.id = cast(:rid as uuid)"

CLAIM_SQL = (
    "update public.recipients r set completed_notified_at = clock_timestamp() "
    f"where r.id = cast(:rid as uuid) and {_DUE} "
    "returning r.id"
)


async def alert_due(session: AsyncSession, recipient_id: str) -> bool:
    """Cheap pre-check on the caller's session: finished and not yet alerted.
    RLS-scoped is fine — the respondent can see their own recipient row,
    cards and responses, and the session already sees the save just made."""
    result = await session.execute(text(IS_DUE_SQL), {"rid": recipient_id})
    return bool(result.scalar_one_or_none())


@asynccontextmanager
async def _admin_session() -> AsyncIterator[AsyncSession]:
    """Short-lived BYPASSRLS session. Its own seam so tests can bind it to
    the rolled-back test connection (same as ``reactive._admin_session``)."""
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


def schedule_completion_check(
    recipient_id: str, *, after: asyncio.Task | None = None
) -> None:
    """Fire-and-forget ``run_completion_check``. Pass ``after`` (the reactive
    generation task scheduled by the same save) to wait for it first."""
    task = asyncio.create_task(run_completion_check(recipient_id, after=after))
    _pending_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _pending_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("completion: task escaped unexpectedly", exc_info=exc)

    task.add_done_callback(_done)


async def wait_for_pending_checks() -> None:
    """Test helper: block until every scheduled completion check finishes."""
    while _pending_tasks:
        await asyncio.gather(*list(_pending_tasks), return_exceptions=True)


async def _claim(recipient_id: str) -> bool:
    async with _admin_session() as session:
        claimed = (
            await session.execute(text(CLAIM_SQL), {"rid": recipient_id})
        ).scalar_one_or_none()
        await session.commit()
    return claimed is not None


async def _load_alert(recipient_id: str) -> tuple[dict, list[dict]] | None:
    """The recipient + engagement details for the email, and who gets it:
    the engagement's creator while they're still a member of the org, else
    every owner of the org."""
    async with _admin_session() as session:
        row = (
            await session.execute(
                text(
                    "select r.email, r.name, r.engagement_id::text as engagement_id, "
                    "       r.org_id::text as org_id, e.engagement_name, "
                    "       e.created_by::text as created_by, cl.name as client_name, "
                    "       (select count(*) from public.responses rr "
                    "          where rr.recipient_id = r.id and rr.state = 'answered') "
                    "         as answered, "
                    "       (select count(*) from public.responses rr "
                    "          where rr.recipient_id = r.id and rr.state = 'skipped') "
                    "         as skipped "
                    "from public.recipients r "
                    "join public.engagements e on e.id = r.engagement_id "
                    "left join public.clients cl on cl.id = e.client_id "
                    "where r.id = cast(:rid as uuid)"
                ),
                {"rid": recipient_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        owners: list[dict] = []
        if row["created_by"] is not None:
            owners = [
                dict(o)
                for o in (
                    await session.execute(
                        text(
                            "select u.email, u.name from public.users u "
                            "join public.organization_memberships m "
                            "  on m.user_id = u.id and m.org_id = cast(:org as uuid) "
                            "where u.id = cast(:uid as uuid)"
                        ),
                        {"org": row["org_id"], "uid": row["created_by"]},
                    )
                ).mappings().all()
            ]
        if not owners:
            owners = [
                dict(o)
                for o in (
                    await session.execute(
                        text(
                            "select u.email, u.name from public.users u "
                            "join public.organization_memberships m on m.user_id = u.id "
                            "where m.org_id = cast(:org as uuid) and m.role = 'owner' "
                            "order by u.email"
                        ),
                        {"org": row["org_id"]},
                    )
                ).mappings().all()
            ]
    return dict(row), owners


def engagement_admin_url(engagement_id: str) -> str:
    """The operator's link to the engagement page (the ``/admin/`` shell
    forwards the hash to v2)."""
    return f"{settings.frontend_base_url.rstrip('/')}/admin/#/client/{engagement_id}"


async def run_completion_check(
    recipient_id: str, *, after: asyncio.Task | None = None
) -> None:
    """Alert the engagement owner if ``recipient_id`` just finished. Never
    raises: anything unexpected is logged."""
    try:
        uuid.UUID(recipient_id)
    except (ValueError, TypeError, AttributeError):
        return
    try:
        if after is not None:
            try:
                await asyncio.wait_for(asyncio.shield(after), _GENERATION_WAIT_SECONDS)
            except Exception:
                logger.warning("completion: waiting on generation for %s failed", recipient_id)

        claimed = False
        for attempt in range(_CLAIM_MAX_ATTEMPTS):
            if await _claim(recipient_id):
                claimed = True
                break
            if attempt < _CLAIM_MAX_ATTEMPTS - 1:
                await asyncio.sleep(_CLAIM_RETRY_SECONDS)
        if not claimed:
            return

        loaded = await _load_alert(recipient_id)
        if loaded is None:
            return
        info, owners = loaded
        if not owners:
            logger.warning("completion: no one to alert for recipient %s", recipient_id)
            return
        subject, body = respondent_finished_email(
            respondent_name=info["name"],
            respondent_email=info["email"],
            engagement_name=info["engagement_name"],
            client_name=info["client_name"],
            answered=int(info["answered"]),
            skipped=int(info["skipped"]),
            engagement_url=engagement_admin_url(info["engagement_id"]),
        )
        for owner in owners:
            await email_module.send_email(owner["email"], subject, body)
        logger.info(
            "completion: recipient %s finished; alerted %d owner(s)",
            recipient_id,
            len(owners),
        )
    except Exception:
        logger.exception("completion: check failed for recipient %s", recipient_id)
