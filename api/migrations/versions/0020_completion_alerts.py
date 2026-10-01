"""Completion alerts: remember when we told the operator a respondent finished.

* ``recipients.completed_notified_at`` — set (by the BYPASSRLS completion
  job, ``pulse_api/completion.py``) when the engagement owner was emailed
  that this respondent answered or skipped every card. It doubles as the
  claim that stops a second email: the job only re-alerts when a card was
  added after this timestamp and the respondent finished that too.

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


def downgrade() -> None:
    op.execute(
        "alter table public.recipients drop column if exists completed_notified_at;"
    )
