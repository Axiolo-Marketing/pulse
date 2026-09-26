"""Client contacts: a saved people list per client.

Operators keep adding the same client-side people to engagement after
engagement. ``client_contacts`` remembers them per client so the
respondent picker can type-ahead from the list instead of retyping emails.

* ``client_contacts`` (NEW): ``id, org_id, client_id, email, name, role,
  created_at`` — unique per client on ``lower(email)``. Org-scoped RLS for
  ``pulse_member`` (verbatim ``clients`` policy); no anon access (the deck
  never reads contacts).
* Backfill: every existing recipient email becomes a contact of its
  engagement's client (latest non-null name wins), so the type-ahead has
  data on day one.

Adding a recipient upserts its client contact from then on (route layer).
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table public.client_contacts (
            id          uuid primary key default gen_random_uuid(),
            org_id      uuid not null references public.organizations(id) on delete cascade,
            client_id   uuid not null references public.clients(id) on delete cascade,
            email       text not null,
            name        text,
            role        text,
            created_at  timestamptz not null default now()
        );
        """
    )
    op.execute(
        "create unique index client_contacts_client_email_uniq "
        "on public.client_contacts (client_id, lower(email));"
    )
    op.execute(
        "create index client_contacts_org_idx on public.client_contacts (org_id);"
    )

    # Grants mirror ``clients`` (0013): pulse_member row-level via RLS;
    # pulse_admin (BYPASSRLS) re-granted ALL since the table is new.
    op.execute(
        "grant select, insert, update, delete on public.client_contacts "
        "to pulse_member;"
    )
    op.execute("grant all on public.client_contacts to pulse_admin;")

    op.execute("alter table public.client_contacts enable row level security;")
    op.execute(
        """
        create policy client_contacts_member_scope on public.client_contacts
            for all to pulse_member
            using (org_id = public.pulse_request_org_id())
            with check (org_id = public.pulse_request_org_id());
        """
    )

    # Backfill from existing recipients: one contact per (client, email),
    # keeping the most recent non-null name.
    op.execute(
        """
        insert into public.client_contacts (org_id, client_id, email, name)
        select distinct on (e.client_id, lower(r.email))
               r.org_id, e.client_id, r.email, r.name
          from public.recipients r
          join public.engagements e on e.id = r.engagement_id
         where r.email is not null and btrim(r.email) <> ''
         order by e.client_id, lower(r.email),
                  (r.name is null), r.created_at desc;
        """
    )


def downgrade() -> None:
    op.execute(
        "drop policy if exists client_contacts_member_scope on public.client_contacts;"
    )
    op.execute("drop table if exists public.client_contacts cascade;")
