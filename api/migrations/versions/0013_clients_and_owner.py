"""Real Client entity + engagement owner; remove folders

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-23

PR1 (``0012``) renamed the misnamed ``clients`` table to ``engagements``.
This revision introduces a REAL ``clients`` (company) entity that owns
many engagements, stamps each engagement with its creating user
(``created_by``), and removes the just-shipped folders feature
(``engagement_groups`` + ``engagements.group_id``).

Shape after this migration:

* ``clients`` (NEW): ``id, org_id (FK NOT NULL), name, created_at`` with a
  ``unique (org_id, name)`` so a company name is unique within an org but
  may repeat across orgs. Org-scoped RLS — copied verbatim from the
  ``engagement_groups`` policy in ``0010`` (a ``pulse_member`` policy
  scoping every row to ``org_id = public.pulse_request_org_id()``), with
  ``pulse_member`` row grants + ``pulse_admin`` ALL (BYPASSRLS path).
* ``engagements``: gains ``client_id`` (FK → clients, NOT NULL after
  backfill) + ``created_by`` (FK → users, nullable). Drops ``group_id``
  (folders gone) and ``name`` (the client owns the name now).
* ``engagement_groups``: dropped.

Backfill (data migration):
  1. One ``clients`` row per distinct ``(org_id, name)`` already on the
     engagements. ``on conflict do nothing`` makes the insert idempotent.
  2. Point each engagement at its matching client via ``(org_id, name)``.
  3. Backfill ``created_by`` from the original create audit row
     (``action = 'client.create'``, ``target_id = engagement.id::text`` —
     ``audit_logs.target_id`` is ``text``, so the engagement id is cast).
     NULL where no such audit row exists.

``downgrade()`` is **best-effort** for the dropped data: it re-adds
``engagements.name`` (backfilled from the linked client), re-creates the
``engagement_groups`` table + ``group_id`` FK, and drops
``client_id`` / ``created_by`` / ``clients``. The original folder
assignments and any owner attribution cannot be recovered — they're gone
once this migration runs forward.
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. New real clients table (org-scoped, copy of the 0010 shape) ──
    op.execute(
        """
        create table public.clients (
            id          uuid primary key default gen_random_uuid(),
            org_id      uuid not null references public.organizations(id) on delete cascade,
            name        text not null,
            created_at  timestamptz not null default now(),
            unique (org_id, name)
        );
        """
    )
    op.execute("create index clients_org_idx on public.clients (org_id);")

    # Grants mirror engagement_groups (0010): pulse_member gets row-level
    # access scoped by RLS; pulse_admin (BYPASSRLS) re-granted ALL since
    # the table didn't exist when the blanket grants ran.
    op.execute(
        "grant select, insert, update, delete on public.clients to pulse_member;"
    )
    op.execute("grant all on public.clients to pulse_admin;")

    # RLS — verbatim org-scoped member policy (same as engagement_groups).
    op.execute("alter table public.clients enable row level security;")
    op.execute(
        """
        create policy clients_member_scope on public.clients
            for all to pulse_member
            using (org_id = public.pulse_request_org_id())
            with check (org_id = public.pulse_request_org_id());
        """
    )

    # Anon read path: the client deck (`pulse_anon`) joins ``clients`` to
    # render the customer-facing name on ``/api/me``. Mirror the
    # ``organizations`` anon pattern from 0007 — a column-scoped SELECT
    # grant + a SELECT policy narrowing to the request's org
    # (``pulse_request_org_id()``). The deck only ever resolves the one
    # token-bound engagement, whose ``client_id`` lives in that same org,
    # so this never widens cross-tenant visibility.
    op.execute(
        "grant select (id, name) on public.clients to pulse_anon;"
    )
    op.execute(
        """
        create policy clients_anon_read on public.clients
            for select to pulse_anon
            using (org_id = public.pulse_request_org_id());
        """
    )

    # ── 2. New engagement columns (both nullable for the backfill) ─────
    op.execute(
        "alter table public.engagements "
        "add column client_id uuid references public.clients(id);"
    )
    op.execute(
        "alter table public.engagements "
        "add column created_by uuid references public.users(id);"
    )

    # ── 3. Backfill ────────────────────────────────────────────────────
    # 3a. One client per distinct (org_id, name) already on engagements.
    op.execute(
        "insert into public.clients (org_id, name) "
        "select distinct org_id, name from public.engagements "
        "on conflict (org_id, name) do nothing;"
    )
    # 3b. Link each engagement to its matching client.
    op.execute(
        "update public.engagements e set client_id = c.id "
        "from public.clients c "
        "where c.org_id = e.org_id and c.name = e.name;"
    )
    # 3c. Owner from the original create audit (target_id is text).
    op.execute(
        "update public.engagements e set created_by = ("
        "  select al.user_id from public.audit_logs al "
        "  where al.action = 'client.create' "
        "    and al.target_id = e.id::text "
        "  order by al.created_at asc limit 1"
        ");"
    )
    # 3d. Now every engagement has a client — enforce it.
    op.execute(
        "alter table public.engagements alter column client_id set not null;"
    )

    # ── 4. Drop folders + the now-redundant engagement name ────────────
    op.execute("alter table public.engagements drop column group_id;")
    op.execute("drop table public.engagement_groups cascade;")
    op.execute("alter table public.engagements drop column name;")


def downgrade() -> None:
    # Best-effort: the dropped folder assignments and owner attribution
    # are unrecoverable. We restore the SCHEMA so a re-run of 0013 can
    # backfill again, and re-derive engagements.name from the client.

    # ── 4'. Re-add engagements.name, backfill from the linked client ──
    op.execute("alter table public.engagements add column name text;")
    op.execute(
        "update public.engagements e set name = c.name "
        "from public.clients c where c.id = e.client_id;"
    )
    # Engagements with a NULL client_id are impossible post-upgrade (NOT
    # NULL), but guard anyway so the SET NOT NULL below never trips.
    op.execute(
        "update public.engagements set name = 'Unknown' where name is null;"
    )
    op.execute("alter table public.engagements alter column name set not null;")

    # ── 1'. Re-create engagement_groups (0010 shape) + group_id FK ────
    op.execute(
        """
        create table public.engagement_groups (
            id          uuid primary key default gen_random_uuid(),
            org_id      uuid not null references public.organizations(id) on delete cascade,
            name        text not null,
            created_at  timestamptz not null default now()
        );
        """
    )
    op.execute(
        "create index engagement_groups_org_idx "
        "on public.engagement_groups (org_id);"
    )
    op.execute(
        "grant select, insert, update, delete "
        "on public.engagement_groups to pulse_member;"
    )
    op.execute("grant all on public.engagement_groups to pulse_admin;")
    op.execute(
        "alter table public.engagement_groups enable row level security;"
    )
    op.execute(
        """
        create policy engagement_groups_member_scope on public.engagement_groups
            for all to pulse_member
            using (org_id = public.pulse_request_org_id())
            with check (org_id = public.pulse_request_org_id());
        """
    )
    op.execute(
        "alter table public.engagements "
        "add column group_id uuid null "
        "references public.engagement_groups(id) on delete set null;"
    )

    # ── 2'. Drop the columns + the clients table ──────────────────────
    op.execute("alter table public.engagements drop column created_by;")
    op.execute("alter table public.engagements drop column client_id;")
    op.execute("drop policy if exists clients_anon_read on public.clients;")
    op.execute("drop policy if exists clients_member_scope on public.clients;")
    op.execute("drop index if exists public.clients_org_idx;")
    op.execute("drop table if exists public.clients cascade;")
