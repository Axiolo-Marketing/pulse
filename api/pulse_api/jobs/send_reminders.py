"""Daily reminder sender — emails invited recipients who haven't finished.

Driven by cron (see ``deploy/roles/backend/templates/pulse-reminders.cron.j2``):

    cd /opt/pulse/source/api \\
      && set -a && . /etc/pulse/pulse.env && set +a \\
      && /opt/pulse/venv/bin/python -m pulse_api.jobs.send_reminders

In dev:

    docker compose exec backend uv run python -m pulse_api.jobs.send_reminders

No-op unless ``REMINDERS_ENABLED=true``. Opens a BYPASSRLS admin session
(the work is deliberately cross-tenant), selects every recipient due a
reminder, sends best-effort, and commits the ``reminder_count`` /
``last_reminded_at`` bump per recipient so a crash mid-run can only ever
re-send the one in flight (never the whole batch).
"""
import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import email as email_module
from pulse_api import reminders as reminders_lib
from pulse_api.auth.email_messages import engagement_reminder_email
from pulse_api.config import settings
from pulse_api.db import admin_engine
from pulse_api.repos import recipients as recipients_repo


async def run() -> int:
    """Send all due reminders; return the number sent."""
    if not settings.reminders_enabled:
        return 0

    sent = 0
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        due = await recipients_repo.list_due_reminders(
            session,
            inactivity_days=settings.reminder_inactivity_days,
            cadence_days=settings.reminder_cadence_days,
            max_reminders=settings.reminder_max,
        )
        for r in due:
            subject, body = engagement_reminder_email(
                deck_url=reminders_lib.deck_url(r["token"]),
                org_name=str(r["org_name"]),
                recipient_name=r.get("name"),
                engagement_name=r.get("engagement_name"),
                unsubscribe_url=reminders_lib.unsubscribe_url(r["id"]),
            )
            # send_email is best-effort (never raises); mark + commit after
            # so the cadence/cap advance even if delivery is silently dropped.
            await email_module.send_email(r["email"], subject, body)
            await recipients_repo.mark_reminded(session, r["id"])
            await session.commit()
            sent += 1
    return sent


def main() -> int:
    sent = asyncio.run(run())
    sys.stdout.write(f"pulse reminders: sent {sent}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
