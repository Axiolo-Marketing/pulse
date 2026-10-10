"""Outbound webhook per organization (Reba signals), plus a first-open marker.

* ``organizations.webhook_url`` / ``organizations.webhook_secret`` — where
  ``pulse_api/webhooks.py`` POSTs respondent events (deck opened, card
  answered, contact shared, deck completed) and the HMAC key it signs them
  with. Delivery is off unless both are set. Owner-managed through
  ``PUT/DELETE /api/orgs/me/webhook``; the secret is never returned by any
  read API. ``pulse_member`` already holds a table-level grant on
  ``organizations`` (0004), and ``pulse_anon``'s grant there is
  column-scoped (0007: id, name, logo_path, branding), so respondents can't
  read either column.
* ``recipients.first_opened_at`` — set once, by the BYPASSRLS webhook job,
  the first time a respondent opens their deck. It is the claim that makes
  ``deck_opened`` fire once per recipient ever, whether or not a webhook was
  configured at the time. ``pulse_anon`` only holds column-scoped UPDATE
  (``last_active_at``) on ``recipients``, so a respondent can't set it.

Backfill: respondents who already used their deck (any activity or any
response row) are stamped now, so configuring a webhook later doesn't send a
burst of stale "opened" events.
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.organizations add column webhook_url text;")
    op.execute("alter table public.organizations add column webhook_secret text;")
    op.execute(
        "alter table public.organizations add constraint organizations_webhook_url_https "
        "check (webhook_url is null or webhook_url like 'https://%');"
    )
    op.execute("alter table public.recipients add column first_opened_at timestamptz;")
    op.execute(
        """
        update public.recipients r set first_opened_at = coalesce(
            r.last_active_at,
            (select min(coalesce(rr.viewed_at, rr.created_at))
               from public.responses rr where rr.recipient_id = r.id),
            now())
        where r.last_active_at is not null
           or exists (select 1 from public.responses rr where rr.recipient_id = r.id);
        """
    )


def downgrade() -> None:
    op.execute("alter table public.recipients drop column if exists first_opened_at;")
    op.execute(
        "alter table public.organizations drop constraint if exists "
        "organizations_webhook_url_https;"
    )
    op.execute("alter table public.organizations drop column if exists webhook_secret;")
    op.execute("alter table public.organizations drop column if exists webhook_url;")
