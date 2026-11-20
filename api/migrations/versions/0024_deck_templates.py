"""Deck templates: reusable, org-scoped snapshots of an engagement's deck.

* ``deck_templates`` (NEW): ``id, org_id, name, description, cards, settings,
  created_by, created_at, updated_at``. ``cards`` is the deck in order (a
  JSON array of operator-authored cards); ``settings`` is the engagement
  toggles worth carrying over. Unique per org on ``lower(name)``. Org-scoped
  RLS for ``pulse_member`` (verbatim ``client_contacts`` policy); no anon
  access — the respondent deck never reads templates.
* ``engagements.template_id`` (NEW): provenance, which template an
  engagement was started from (``on delete set null`` so deleting a
  template never touches engagements). ``pulse_member`` already has table
  level insert/update on ``engagements``, so no grant changes are needed.
  ``pulse_anon`` can read the column on its own engagement row (it has
  table-level select there) but has no access to ``deck_templates``.
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table public.deck_templates (
            id          uuid primary key default gen_random_uuid(),
            org_id      uuid not null references public.organizations(id) on delete cascade,
            name        text not null check (btrim(name) <> ''),
            description text,
            cards       jsonb not null default '[]'::jsonb,
            settings    jsonb not null default '{}'::jsonb,
            created_by  uuid references public.users(id) on delete set null,
            created_at  timestamptz not null default now(),
            updated_at  timestamptz not null default now()
        );
        """
    )
    op.execute(
        "create unique index deck_templates_org_name_uniq "
        "on public.deck_templates (org_id, lower(name));"
    )
    op.execute(
        "create index deck_templates_org_idx on public.deck_templates (org_id);"
    )
    op.execute(
        "grant select, insert, update, delete on public.deck_templates "
        "to pulse_member;"
    )
    op.execute("grant all on public.deck_templates to pulse_admin;")
    op.execute("alter table public.deck_templates enable row level security;")
    op.execute(
        """
        create policy deck_templates_member_scope on public.deck_templates
            for all to pulse_member
            using (org_id = public.pulse_request_org_id())
            with check (org_id = public.pulse_request_org_id());
        """
    )

    op.execute(
        "alter table public.engagements "
        "add column template_id uuid "
        "references public.deck_templates(id) on delete set null;"
    )


def downgrade() -> None:
    op.execute("alter table public.engagements drop column if exists template_id;")
    op.execute(
        "drop policy if exists deck_templates_member_scope on public.deck_templates;"
    )
    op.execute("drop table if exists public.deck_templates cascade;")
