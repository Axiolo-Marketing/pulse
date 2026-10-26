"""Shared answers: one answer per question that every respondent can edit.

For collaborative client documents (page blocks), an engagement can switch to
``shared_answers``: respondents no longer answer separately — they all edit
the same answers, overwriting each other, and every change is logged.

* ``engagements.shared_answers`` — the mode switch (default off; the deck's
  one-answer-each behaviour is unchanged).
* ``shared_answers`` — the current answer per card: value, who last changed
  it, when.
* ``answer_events`` — append-only audit trail: every save, with the
  respondent (and a name/email snapshot that survives their removal) and
  what they set (whole text, or the grid cells they changed).

Respondents never touch these tables directly: the embed API resolves their
token on the RLS-scoped anon session, then reads/writes on the BYPASSRLS
admin engine with that engagement id. Members read (and clear, on reset)
within their org; nobody updates or deletes events except by cascade.
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table public.engagements "
        "add column shared_answers boolean not null default false;"
    )
    op.execute(
        """
        create table public.shared_answers (
            card_id       uuid primary key references public.cards(id) on delete cascade,
            engagement_id uuid not null references public.engagements(id) on delete cascade,
            org_id        uuid not null references public.organizations(id) on delete cascade,
            value         jsonb not null,
            updated_by    uuid references public.recipients(id) on delete set null,
            updated_at    timestamptz not null default clock_timestamp()
        );
        """
    )
    op.execute(
        "create index shared_answers_engagement_idx on public.shared_answers (engagement_id);"
    )
    op.execute(
        """
        create table public.answer_events (
            id            uuid primary key default gen_random_uuid(),
            card_id       uuid not null references public.cards(id) on delete cascade,
            engagement_id uuid not null references public.engagements(id) on delete cascade,
            org_id        uuid not null references public.organizations(id) on delete cascade,
            recipient_id  uuid references public.recipients(id) on delete set null,
            actor_label   text not null,
            change        jsonb,
            created_at    timestamptz not null default clock_timestamp()
        );
        """
    )
    op.execute(
        "create index answer_events_engagement_idx "
        "on public.answer_events (engagement_id, created_at);"
    )
    for table in ("shared_answers", "answer_events"):
        op.execute(f"grant select, delete on public.{table} to pulse_member;")
        op.execute(f"grant all on public.{table} to pulse_admin;")
        op.execute(f"alter table public.{table} enable row level security;")
        op.execute(
            f"""
            create policy {table}_member_scope on public.{table}
                for all to pulse_member
                using (org_id = public.pulse_request_org_id())
                with check (org_id = public.pulse_request_org_id());
            """
        )


def downgrade() -> None:
    for table in ("answer_events", "shared_answers"):
        op.execute(f"drop policy if exists {table}_member_scope on public.{table};")
        op.execute(f"drop table if exists public.{table};")
    op.execute("alter table public.engagements drop column if exists shared_answers;")
