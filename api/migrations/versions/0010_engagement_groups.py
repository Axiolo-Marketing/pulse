"""Engagement folders — ``engagement_groups`` table + ``clients.group_id``

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-22

Flat folders for the operator's engagement list. A folder is a per-org
``engagement_groups`` row; an engagement points at one folder via the
nullable ``clients.group_id`` FK. The FK is ``on delete set null`` so
deleting a folder **ungroups** its engagements — it never deletes them.
A NULL ``group_id`` is the implicit "Ungrouped" bucket.

The table is an ordinary org-scoped child table, so it follows the exact
multi-tenant RLS shape ``clients`` got in migration 0004: a
``pulse_member`` policy that narrows every row to
``org_id = public.pulse_request_org_id()``, plus full row-level grants to
``pulse_member`` (RLS does the scoping). ``pulse_admin`` keeps BYPASSRLS
and is re-granted ALL so cross-org/superadmin paths reach the new table.

``downgrade()`` drops the column first (so the FK is gone), then the
table.
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. New per-org folders table ──────────────────────────────────
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

    # ── 2. Nullable FK on clients (deleting a folder ungroups) ─────────
    op.execute(
        "alter table public.clients "
        "add column group_id uuid null "
        "references public.engagement_groups(id) on delete set null;"
    )

    # ── 3. Grants — mirror what clients gets in 0004 ──────────────────
    # pulse_member gets full row-level access; RLS narrows it to the
    # active org's rows. pulse_admin (BYPASSRLS) is re-granted ALL since
    # the 0001/0004 "grant all on all tables" ran before this table
    # existed.
    op.execute(
        "grant select, insert, update, delete "
        "on public.engagement_groups to pulse_member;"
    )
    op.execute("grant all on public.engagement_groups to pulse_admin;")

    # ── 4. RLS — same org-scoped member policy as clients (0004) ──────
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


def downgrade() -> None:
    op.execute(
        "drop policy if exists engagement_groups_member_scope "
        "on public.engagement_groups;"
    )
    # Drop the column first so the FK from clients is gone before the
    # referenced table is dropped.
    op.execute("alter table public.clients drop column if exists group_id;")
    op.execute("drop index if exists public.engagement_groups_org_idx;")
    op.execute("drop table if exists public.engagement_groups cascade;")
