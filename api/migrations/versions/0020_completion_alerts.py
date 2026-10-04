"""Completion alerts: remember when we told the operator a respondent finished.

* ``recipients.completed_notified_at`` — set (by the BYPASSRLS completion
  job, ``pulse_api/completion.py``) when the engagement owner was emailed
  that this respondent answered or skipped every card. It doubles as the
  claim that stops a second email: the job only re-alerts when a card was
  added after this timestamp and the respondent finished that too.

Backfill: respondents who had already finished before this migration are
stamped now, so their first edit after the deploy doesn't send a weeks-late
"just finished" email.

No RLS/grant change: a plain column on an org-scoped table. ``pulse_anon``
only holds column-scoped UPDATE (``last_active_at``) on ``recipients``, so a
respondent can't set or clear it.
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table public.recipients add column completed_notified_at timestamptz;"
    )
    op.execute(
        """
        update public.recipients r set completed_notified_at = now()
        where (select count(*) from public.cards c
                 where c.engagement_id = r.engagement_id
                   and (c.recipient_id is null or c.recipient_id = r.id)) > 0
          and (select count(*) from public.responses rr
                 join public.cards c on c.id = rr.card_id
                 where rr.recipient_id = r.id
                   and rr.state in ('answered', 'skipped')
                   and (c.recipient_id is null or c.recipient_id = r.id))
              >= (select count(*) from public.cards c
                    where c.engagement_id = r.engagement_id
                      and (c.recipient_id is null or c.recipient_id = r.id));
        """
    )


def downgrade() -> None:
    op.execute(
        "alter table public.recipients drop column if exists completed_notified_at;"
    )
