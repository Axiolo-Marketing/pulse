"""multi-tenant schema + data migration

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-03

PR 1 of the multi-tenant refactor. Adds the four new tables that model
organizations (organizations, organization_memberships,
organization_invites, audit_logs), threads `org_id` onto every existing
tenant table, backfills the pre-existing Axiolo rows onto an
auto-created Axiolo org, installs the new `pulse_member` role + the
`pulse.org_id` GUC helper, and wires org-scoped RLS policies for the
member role.

Scope decisions baked into this revision (resolved by the orchestrator
during PR 1 planning):

- ``clients.org_id`` is set NOT NULL inside this revision because every
  pre-existing row gets backfilled and the admin route that creates new
  clients is updated in this same PR.
- ``cards``, ``responses``, ``uploads``, ``api_keys``, ``audit_logs``
  keep ``org_id`` nullable for now. PR 2 makes them NOT NULL after the
  route handlers are taught to set ``org_id`` explicitly. To minimize
  fallout we install a column DEFAULT on ``responses`` and ``uploads``
  that reads from the ``pulse.org_id`` GUC, so client-facing INSERTs
  continue to populate ``org_id`` once the request middleware sets the
  GUC (it does — see ``api/pulse_api/db.py`` ``get_anon_session``).
- ``users.is_admin`` is INTENTIONALLY NOT DROPPED here. Dropping it
  requires the auth/session refactor (replace ``get_current_admin``
  with ``get_current_org_member``) which is PR 2 work. The data
  migration still backfills owner memberships for everyone with
  ``is_admin = true`` so PR 2 can flip the column drop without
  re-deriving who should be an owner.

Data migration is one-way: ``downgrade()`` removes the schema additions
but does NOT remove the Axiolo org row or owner memberships. Reverting
in production would require a separate cleanup script.
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _superadmin_emails() -> list[str]:
    """Whitespace- or comma-separated emails from the env var.

    Reads ``SUPERADMIN_EMAILS`` directly because the migration runs
    inside Alembic, which has no app-Settings instance, and because the
    same env var is consumed by ``make seed-dev`` for symmetry.
    """
    raw = os.environ.get("SUPERADMIN_EMAILS", "").strip()
    if not raw:
        return []
    parts = [p for p in raw.replace(",", " ").split() if p]
    return [p.strip().lower() for p in parts]


def upgrade() -> None:
    # ── 1. New tenant-tree tables ─────────────────────────────────────
    op.execute(
        """
        create table public.organizations (
            id          uuid primary key default gen_random_uuid(),
            name        text not null,
            slug        text not null unique,
            logo_path   text,
            created_at  timestamptz not null default now()
        );
        """
    )
    op.execute(
        """
        create table public.organization_memberships (
            id          uuid primary key default gen_random_uuid(),
            org_id      uuid not null references public.organizations(id) on delete cascade,
            user_id     uuid not null references public.users(id)         on delete cascade,
            role        text not null check (role in ('owner', 'member')),
            created_at  timestamptz not null default now(),
            unique (org_id, user_id)
        );
        """
    )
    op.execute(
        "create index organization_memberships_user_idx "
        "on public.organization_memberships (user_id);"
    )
    op.execute(
        """
        create table public.organization_invites (
            id                  uuid primary key default gen_random_uuid(),
            org_id              uuid not null references public.organizations(id) on delete cascade,
            email               text not null,
            role                text not null check (role in ('owner', 'member')),
            token_hash          text not null unique,
            invited_by_user_id  uuid references public.users(id) on delete set null,
            expires_at          timestamptz not null,
            accepted_at         timestamptz,
            created_at          timestamptz not null default now()
        );
        """
    )
    op.execute(
        "create index organization_invites_org_email_idx "
        "on public.organization_invites (org_id, email) "
        "where accepted_at is null;"
    )
    op.execute(
        """
        create table public.audit_logs (
            id           uuid primary key default gen_random_uuid(),
            org_id       uuid not null references public.organizations(id) on delete cascade,
            user_id      uuid          references public.users(id)         on delete set null,
            action       text not null,
            target_type  text,
            target_id    text,
            metadata     jsonb,
            created_at   timestamptz not null default now()
        );
        """
    )

    # ── 2. Extend users ───────────────────────────────────────────────
    # NOTE: is_admin is NOT dropped here — see module docstring.
    op.execute(
        "alter table public.users "
        "add column is_superadmin boolean not null default false;"
    )
    op.execute(
        "alter table public.users "
        "add column last_active_org_id uuid references public.organizations(id) on delete set null;"
    )

    # ── 3. Add nullable org_id on existing tenant tables ──────────────
    for tbl in ("clients", "cards", "responses", "uploads", "api_keys"):
        op.execute(
            f"alter table public.{tbl} "
            f"add column org_id uuid references public.organizations(id) on delete restrict;"
        )

    # ── 4. Seed the Axiolo org ────────────────────────────────────────
    op.execute(
        """
        insert into public.organizations (name, slug)
        values ('Axiolo', 'axiolo');
        """
    )

    # ── 5. Backfill org_id on every existing tenant row ───────────────
    for tbl in ("clients", "cards", "responses", "uploads", "api_keys"):
        op.execute(
            f"update public.{tbl} "
            f"set org_id = (select id from public.organizations where slug = 'axiolo') "
            f"where org_id is null;"
        )

    # ── 6. Owner memberships for every existing is_admin user ─────────
    op.execute(
        """
        insert into public.organization_memberships (org_id, user_id, role)
        select
            (select id from public.organizations where slug = 'axiolo'),
            u.id,
            'owner'
        from public.users u
        where u.is_admin = true
          and not exists (
              select 1 from public.organization_memberships m
              where m.user_id = u.id
                and m.org_id = (select id from public.organizations where slug = 'axiolo')
          );
        """
    )

    # ── 7. Set last_active_org_id for the same users ──────────────────
    op.execute(
        """
        update public.users
        set last_active_org_id = (select id from public.organizations where slug = 'axiolo')
        where is_admin = true
          and last_active_org_id is null;
        """
    )

    # ── 8. Promote configured superadmin emails ───────────────────────
    emails = _superadmin_emails()
    if emails:
        op.get_bind().execute(
            sa.text(
                "update public.users set is_superadmin = true "
                "where lower(email) = any(:emails)"
            ),
            {"emails": emails},
        )

    # ── 9. clients.org_id NOT NULL ────────────────────────────────────
    op.execute("alter table public.clients alter column org_id set not null;")

    # ── 10. Partial / org-scoped indexes ──────────────────────────────
    op.execute("create index clients_org_idx          on public.clients   (org_id);")
    op.execute("create index cards_org_idx            on public.cards     (org_id);")
    op.execute("create index responses_org_idx        on public.responses (org_id);")
    op.execute("create index uploads_org_idx          on public.uploads   (org_id);")
    op.execute("create index api_keys_org_idx         on public.api_keys  (org_id);")
    op.execute("create index audit_logs_org_created_idx on public.audit_logs (org_id, created_at desc);")

    # ── 11. Column DEFAULT for client-facing INSERTs ──────────────────
    # Reads pulse.org_id GUC set by get_anon_session. Cards / api_keys
    # get no default — the admin route handler sets them explicitly in
    # PR 2 once the auth refactor lands.
    op.execute(
        "alter table public.responses "
        "alter column org_id set default nullif(current_setting('pulse.org_id', true), '')::uuid;"
    )
    op.execute(
        "alter table public.uploads "
        "alter column org_id set default nullif(current_setting('pulse.org_id', true), '')::uuid;"
    )

    # ── 12. pulse.org_id helper function ──────────────────────────────
    op.execute(
        """
        create or replace function public.pulse_request_org_id()
        returns uuid language sql stable as $$
            select nullif(current_setting('pulse.org_id', true), '')::uuid;
        $$;
        """
    )

    # ── 13. pulse_member role (idempotent) + grants ───────────────────
    op.execute(
        """
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = 'pulse_member') then
            create role pulse_member with login password 'devpass' nosuperuser nobypassrls;
          end if;
        end
        $$;
        """
    )

    op.execute("grant usage on schema public to pulse_member;")
    op.execute("grant execute on function public.pulse_request_org_id() to pulse_anon, pulse_admin, pulse_member;")

    # pulse_member needs full row-level access — RLS narrows it to the
    # rows whose org_id matches the GUC.
    for tbl in (
        "organizations",
        "organization_memberships",
        "organization_invites",
        "audit_logs",
        "clients",
        "cards",
        "responses",
        "uploads",
        "api_keys",
    ):
        op.execute(
            f"grant select, insert, update, delete on public.{tbl} to pulse_member;"
        )

    # Re-grant ALL on the new tables to pulse_admin (the existing
    # "grant all on all tables" in 0001 doesn't cover tables created
    # after that grant ran).
    op.execute("grant all on all tables   in schema public to pulse_admin;")
    op.execute("grant all on all sequences in schema public to pulse_admin;")

    # ── 14. RLS policies for pulse_member (org-scoped) ────────────────
    for tbl in (
        "organizations",
        "organization_memberships",
        "organization_invites",
        "audit_logs",
        "clients",
        "cards",
        "responses",
        "uploads",
        "api_keys",
    ):
        op.execute(f"alter table public.{tbl} enable row level security;")

    # `organizations`: the active org_id must equal the row's id.
    op.execute(
        """
        create policy organizations_member_scope on public.organizations
            for all to pulse_member
            using (id = public.pulse_request_org_id())
            with check (id = public.pulse_request_org_id());
        """
    )

    # Every other org-scoped table: org_id must equal the GUC.
    for tbl in (
        "organization_memberships",
        "organization_invites",
        "audit_logs",
        "clients",
        "cards",
        "responses",
        "uploads",
        "api_keys",
    ):
        op.execute(
            f"""
            create policy {tbl}_member_scope on public.{tbl}
                for all to pulse_member
                using (org_id = public.pulse_request_org_id())
                with check (org_id = public.pulse_request_org_id());
            """
        )


def downgrade() -> None:
    # Reverse order. Note: the Axiolo organization row and the owner
    # memberships are NOT removed — the data migration is one-way. A
    # production rollback would need a separate cleanup script.

    # ── Drop pulse_member policies ────────────────────────────────────
    for tbl in (
        "api_keys",
        "uploads",
        "responses",
        "cards",
        "clients",
        "audit_logs",
        "organization_invites",
        "organization_memberships",
        "organizations",
    ):
        op.execute(f"drop policy if exists {tbl}_member_scope on public.{tbl};")
    op.execute("drop policy if exists organizations_member_scope on public.organizations;")

    # ── Disable RLS on new tables (leave existing tables' RLS on) ─────
    for tbl in (
        "audit_logs",
        "organization_invites",
        "organization_memberships",
        "organizations",
    ):
        op.execute(f"alter table public.{tbl} disable row level security;")

    # ── Drop column DEFAULTs ──────────────────────────────────────────
    op.execute("alter table public.responses alter column org_id drop default;")
    op.execute("alter table public.uploads   alter column org_id drop default;")

    # ── Drop the helper function ──────────────────────────────────────
    op.execute("drop function if exists public.pulse_request_org_id();")

    # ── Drop pulse_member role (safe to drop after policies gone) ─────
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'pulse_member') then
            -- Revoke first so DROP ROLE doesn't fail on lingering object grants.
            revoke all on all tables    in schema public from pulse_member;
            revoke all on all sequences in schema public from pulse_member;
            revoke usage on schema public               from pulse_member;
            drop role pulse_member;
          end if;
        end
        $$;
        """
    )

    # ── Drop indexes added in this revision ───────────────────────────
    for idx in (
        "audit_logs_org_created_idx",
        "api_keys_org_idx",
        "uploads_org_idx",
        "responses_org_idx",
        "cards_org_idx",
        "clients_org_idx",
    ):
        op.execute(f"drop index if exists public.{idx};")

    # ── clients.org_id NOT NULL → NULL again ──────────────────────────
    op.execute("alter table public.clients alter column org_id drop not null;")

    # ── Drop org_id columns ───────────────────────────────────────────
    for tbl in ("api_keys", "uploads", "responses", "cards", "clients"):
        op.execute(f"alter table public.{tbl} drop column if exists org_id;")

    # ── users column rollback ─────────────────────────────────────────
    op.execute("alter table public.users drop column if exists last_active_org_id;")
    op.execute("alter table public.users drop column if exists is_superadmin;")

    # ── Drop new tables (cascade also drops their FKs) ────────────────
    for tbl in (
        "audit_logs",
        "organization_invites",
        "organization_memberships",
        "organizations",
    ):
        op.execute(f"drop table if exists public.{tbl} cascade;")
