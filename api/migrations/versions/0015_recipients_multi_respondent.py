"""Multi-respondent engagements: recipients own the token + answers

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-25

Introduces ``public.recipients`` — a per-respondent entity that owns the
magic-link token, the recipient's email, per-recipient activity
(``last_active_at``), and invite/reminder state. ``responses`` + ``uploads``
gain a ``recipient_id`` and are scoped to the recipient; ``cards`` stay
engagement-level (the shared question set). ``pulse_request_engagement_id()``
is re-pointed to resolve token → recipient → engagement, so the ``cards``
anon policy (``engagement_id = pulse_request_engagement_id()``) is unchanged
and every recipient of an engagement still sees the same cards.

Backfill keeps existing single-link engagements working: one *legacy*
recipient per engagement carries the old token (``email`` NULL), and the
existing responses/uploads are attributed to it. The token +
``last_active_at`` columns then move off ``engagements`` onto the recipient.

Hand-written — autogen can't represent RLS / helper functions / grants.
``downgrade()`` reverses the structure best-effort; it assumes one
recipient per engagement (collapses each engagement to its earliest
recipient's token), so per-recipient data created after the upgrade is
lost on downgrade. Restore the pre-migration snapshot for a clean revert.
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. recipients table ────────────────────────────────────────────
    op.execute(
        """
        create table public.recipients (
            id               uuid primary key default gen_random_uuid(),
            engagement_id    uuid not null references public.engagements(id) on delete cascade,
            org_id           uuid not null references public.organizations(id) on delete cascade,
            email            text,
            name             text,
            token            text not null unique,
            last_active_at   timestamptz,
            invited_at       timestamptz,
            last_reminded_at timestamptz,
            reminder_count   int not null default 0,
            unsubscribed_at  timestamptz,
            created_at       timestamptz not null default now()
        );
        """
    )
    op.execute("create index recipients_engagement_idx on public.recipients (engagement_id);")
    # A recipient email is unique within an engagement (legacy recipients have
    # NULL email and are exempt from the constraint).
    op.execute(
        "create unique index recipients_engagement_email_idx "
        "on public.recipients (engagement_id, lower(email)) where email is not null;"
    )

    # ── 2. backfill one legacy recipient per engagement (carries old token) ─
    op.execute(
        """
        insert into public.recipients
            (engagement_id, org_id, token, last_active_at, invited_at)
        select id, org_id, token, last_active_at, created_at
        from public.engagements;
        """
    )

    # ── 3. helper functions: add recipient resolver, re-point engagement ─
    op.execute(
        """
        create or replace function public.pulse_request_recipient_id()
        returns uuid language sql stable as $$
            select id from public.recipients
            where token = public.pulse_request_token()
            limit 1;
        $$;
        """
    )
    op.execute(
        """
        create or replace function public.pulse_request_engagement_id()
        returns uuid language sql stable as $$
            select engagement_id from public.recipients
            where token = public.pulse_request_token()
            limit 1;
        $$;
        """
    )

    # ── 4. recipients RLS + grants (mirror the 0013 clients block) ──────
    op.execute("alter table public.recipients enable row level security;")
    op.execute(
        """
        create policy recipients_member_scope on public.recipients
            for all to pulse_member
            using (org_id = public.pulse_request_org_id())
            with check (org_id = public.pulse_request_org_id());
        """
    )
    op.execute(
        """
        create policy recipients_self_read on public.recipients
            for select to pulse_anon
            using (token = public.pulse_request_token());
        """
    )
    op.execute(
        """
        create policy recipients_self_touch on public.recipients
            for update to pulse_anon
            using (token = public.pulse_request_token())
            with check (token = public.pulse_request_token());
        """
    )
    op.execute("grant select, insert, update, delete on public.recipients to pulse_member;")
    op.execute("grant all on public.recipients to pulse_admin;")
    op.execute("grant select on public.recipients to pulse_anon;")
    op.execute("grant update (last_active_at) on public.recipients to pulse_anon;")

    # ── 5. responses gain recipient_id (backfill → NOT NULL) ────────────
    op.execute(
        "alter table public.responses add column recipient_id uuid "
        "references public.recipients(id) on delete cascade;"
    )
    op.execute(
        "update public.responses res set recipient_id = r.id "
        "from public.recipients r where r.engagement_id = res.engagement_id;"
    )
    op.execute("alter table public.responses alter column recipient_id set not null;")
    op.execute(
        "alter table public.responses alter column recipient_id "
        "set default public.pulse_request_recipient_id();"
    )
    op.execute("alter table public.responses drop constraint responses_card_id_client_id_key;")
    op.execute(
        "alter table public.responses "
        "add constraint responses_card_id_recipient_id_key unique (card_id, recipient_id);"
    )
    # Swap anon policies to the recipient grain.
    op.execute("drop policy responses_self_read on public.responses;")
    op.execute("drop policy responses_self_insert on public.responses;")
    op.execute("drop policy responses_self_update on public.responses;")
    op.execute(
        """
        create policy responses_self_read on public.responses
            for select to pulse_anon
            using (recipient_id = public.pulse_request_recipient_id());
        """
    )
    op.execute(
        """
        create policy responses_self_insert on public.responses
            for insert to pulse_anon
            with check (recipient_id = public.pulse_request_recipient_id());
        """
    )
    op.execute(
        """
        create policy responses_self_update on public.responses
            for update to pulse_anon
            using (recipient_id = public.pulse_request_recipient_id())
            with check (recipient_id = public.pulse_request_recipient_id());
        """
    )

    # ── 6. uploads gain recipient_id (backfill → NOT NULL) ──────────────
    op.execute(
        "alter table public.uploads add column recipient_id uuid "
        "references public.recipients(id) on delete cascade;"
    )
    op.execute(
        "update public.uploads up set recipient_id = r.id "
        "from public.recipients r where r.engagement_id = up.engagement_id;"
    )
    op.execute("alter table public.uploads alter column recipient_id set not null;")
    op.execute(
        "alter table public.uploads alter column recipient_id "
        "set default public.pulse_request_recipient_id();"
    )
    op.execute("drop policy uploads_self_read on public.uploads;")
    op.execute("drop policy uploads_self_insert on public.uploads;")
    op.execute("drop policy uploads_self_delete on public.uploads;")
    op.execute(
        """
        create policy uploads_self_read on public.uploads
            for select to pulse_anon
            using (recipient_id = public.pulse_request_recipient_id());
        """
    )
    op.execute(
        """
        create policy uploads_self_insert on public.uploads
            for insert to pulse_anon
            with check (recipient_id = public.pulse_request_recipient_id());
        """
    )
    op.execute(
        """
        create policy uploads_self_delete on public.uploads
            for delete to pulse_anon
            using (recipient_id = public.pulse_request_recipient_id());
        """
    )

    # ── 7. engagements: reminders toggle; re-point self_read; drop token + last_active_at ─
    op.execute(
        "alter table public.engagements add column reminders_enabled boolean not null default true;"
    )
    op.execute("drop policy engagements_self_touch on public.engagements;")
    op.execute("drop policy engagements_self_read on public.engagements;")
    op.execute(
        """
        create policy engagements_self_read on public.engagements
            for select to pulse_anon
            using (id = public.pulse_request_engagement_id());
        """
    )
    # token + last_active_at now live on recipients (the unique index on
    # token drops automatically with the column).
    op.execute("alter table public.engagements drop column token;")
    op.execute("alter table public.engagements drop column last_active_at;")


def downgrade() -> None:
    # Best-effort reverse; collapses each engagement to its earliest
    # recipient's token (per-recipient data created post-upgrade is lost).

    # ── 7'. engagements: restore token + last_active_at ─────────────────
    op.execute("alter table public.engagements add column token text;")
    op.execute("alter table public.engagements add column last_active_at timestamptz;")
    op.execute(
        """
        update public.engagements e set
            token = sub.token,
            last_active_at = sub.last_active_at
        from (
            select distinct on (engagement_id)
                   engagement_id, token, last_active_at
            from public.recipients
            order by engagement_id, created_at
        ) sub
        where sub.engagement_id = e.id;
        """
    )
    op.execute("alter table public.engagements alter column token set not null;")
    op.execute("alter table public.engagements add constraint clients_token_key unique (token);")
    op.execute("drop policy engagements_self_read on public.engagements;")
    op.execute(
        """
        create policy engagements_self_read on public.engagements
            for select to pulse_anon
            using (token = public.pulse_request_token());
        """
    )
    op.execute(
        """
        create policy engagements_self_touch on public.engagements
            for update to pulse_anon
            using (token = public.pulse_request_token())
            with check (token = public.pulse_request_token());
        """
    )
    op.execute("grant update (last_active_at) on public.engagements to pulse_anon;")
    op.execute("alter table public.engagements drop column reminders_enabled;")

    # ── 3'. re-point helper back to engagements, drop recipient resolver ─
    op.execute(
        """
        create or replace function public.pulse_request_engagement_id()
        returns uuid language sql stable as $$
            select id from public.engagements
            where token = public.pulse_request_token()
            limit 1;
        $$;
        """
    )

    # ── 6'. uploads: restore engagement-grain policies, drop recipient_id ─
    op.execute("drop policy uploads_self_read on public.uploads;")
    op.execute("drop policy uploads_self_insert on public.uploads;")
    op.execute("drop policy uploads_self_delete on public.uploads;")
    op.execute("alter table public.uploads alter column recipient_id drop default;")
    op.execute("alter table public.uploads drop column recipient_id;")
    op.execute(
        """
        create policy uploads_self_read on public.uploads
            for select to pulse_anon
            using (engagement_id = public.pulse_request_engagement_id());
        """
    )
    op.execute(
        """
        create policy uploads_self_insert on public.uploads
            for insert to pulse_anon
            with check (engagement_id = public.pulse_request_engagement_id());
        """
    )
    op.execute(
        """
        create policy uploads_self_delete on public.uploads
            for delete to pulse_anon
            using (engagement_id = public.pulse_request_engagement_id());
        """
    )

    # ── 5'. responses: restore engagement-grain unique + policies ───────
    op.execute("drop policy responses_self_read on public.responses;")
    op.execute("drop policy responses_self_insert on public.responses;")
    op.execute("drop policy responses_self_update on public.responses;")
    op.execute(
        "alter table public.responses drop constraint responses_card_id_recipient_id_key;"
    )
    op.execute("alter table public.responses alter column recipient_id drop default;")
    op.execute("alter table public.responses drop column recipient_id;")
    op.execute(
        "alter table public.responses "
        "add constraint responses_card_id_client_id_key unique (card_id, engagement_id);"
    )
    op.execute(
        """
        create policy responses_self_read on public.responses
            for select to pulse_anon
            using (engagement_id = public.pulse_request_engagement_id());
        """
    )
    op.execute(
        """
        create policy responses_self_insert on public.responses
            for insert to pulse_anon
            with check (engagement_id = public.pulse_request_engagement_id());
        """
    )
    op.execute(
        """
        create policy responses_self_update on public.responses
            for update to pulse_anon
            using (engagement_id = public.pulse_request_engagement_id())
            with check (engagement_id = public.pulse_request_engagement_id());
        """
    )

    # ── 1'. drop recipient resolver + recipients table ──────────────────
    op.execute("drop function if exists public.pulse_request_recipient_id();")
    op.execute("drop table public.recipients;")
