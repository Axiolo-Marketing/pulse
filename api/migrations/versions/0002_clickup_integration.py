"""ClickUp integration: per-user OAuth token (encrypted), per-engagement
list_id, per-card task_id back-reference, per-response cached upstream
status, and a `clickup_workspaces` table for per-workspace webhook
metadata (signing secret encrypted).

All secret-bearing columns suffixed `_enc` and store Fernet ciphertext —
see `pulse_api.crypto`. Plaintext secrets never land in the database.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Per-user OAuth token (encrypted) + ClickUp identity ───────────────
    op.execute(
        """
        alter table public.users
          add column clickup_access_token_enc text,
          add column clickup_user_id          text;
        """
    )

    # ── Per-workspace webhook metadata ─────────────────────────────────────
    # ClickUp's webhook_secret is per-subscription; we register one webhook
    # per (operator, workspace) and store the returned secret encrypted.
    op.execute(
        """
        create table public.clickup_workspaces (
            id                  uuid primary key default gen_random_uuid(),
            user_id             uuid not null references public.users(id) on delete cascade,
            workspace_id        text not null,
            workspace_name      text,
            webhook_id          text,
            webhook_secret_enc  text,
            created_at          timestamptz not null default now(),
            unique (user_id, workspace_id)
        );
        """
    )
    op.execute(
        "create index clickup_workspaces_workspace_id_idx "
        "on public.clickup_workspaces (workspace_id);"
    )

    # ── Per-engagement target list ────────────────────────────────────────
    op.execute(
        """
        alter table public.clients
          add column clickup_list_id   text,
          add column clickup_list_name text;
        """
    )

    # ── Per-card back-reference for idempotent updates ────────────────────
    op.execute("alter table public.cards add column clickup_task_id text;")
    op.execute(
        "create index cards_clickup_task_id_idx on public.cards (clickup_task_id) "
        "where clickup_task_id is not null;"
    )

    # ── Per-response cached upstream status (set by webhook) ──────────────
    op.execute(
        """
        alter table public.responses
          add column clickup_status            text,
          add column clickup_status_updated_at timestamptz;
        """
    )

    # ── Grants. pulse_admin (BYPASSRLS) gets the new tables/columns via
    # its existing `grant all on all tables` from migration 0001. RLS rules
    # on the existing tables already cover the new columns (RLS is
    # row-level, not column-level). pulse_anon should NEVER see the new
    # secret-bearing columns — explicitly revoke just in case.
    op.execute("grant all on public.clickup_workspaces to pulse_admin;")
    # pulse_anon was granted SELECT on clients in 0001; it should NOT see
    # ClickUp list metadata (admin-only concept). Revoke + re-grant only
    # the original columns.
    op.execute("revoke all on public.clients from pulse_anon;")
    op.execute("grant select (id, name, org_name, engagement_name, token, brief, created_at, last_active_at) on public.clients to pulse_anon;")
    op.execute("grant update (last_active_at) on public.clients to pulse_anon;")
    # cards: same treatment — pulse_anon should not see clickup_task_id.
    op.execute("revoke all on public.cards from pulse_anon;")
    op.execute("grant select (id, client_id, order_index, category, title, context, question, response_type, options, default_value, skip_allowed, attachment_path, created_at) on public.cards to pulse_anon;")
    # responses: pulse_anon should not see ClickUp status fields.
    op.execute("revoke all on public.responses from pulse_anon;")
    op.execute("grant select (id, card_id, client_id, state, response_value, viewed_at, answered_at, created_at, updated_at) on public.responses to pulse_anon;")
    op.execute("grant insert (card_id, client_id, state, response_value, viewed_at, answered_at) on public.responses to pulse_anon;")
    op.execute("grant update (state, response_value, viewed_at, answered_at, updated_at) on public.responses to pulse_anon;")


def downgrade() -> None:
    # Reverse-order. Drop the new columns + table; restore the original
    # whole-table grants from 0001.
    op.execute("alter table public.responses drop column if exists clickup_status_updated_at;")
    op.execute("alter table public.responses drop column if exists clickup_status;")
    op.execute("drop index if exists cards_clickup_task_id_idx;")
    op.execute("alter table public.cards drop column if exists clickup_task_id;")
    op.execute("alter table public.clients drop column if exists clickup_list_name;")
    op.execute("alter table public.clients drop column if exists clickup_list_id;")
    op.execute("drop index if exists clickup_workspaces_workspace_id_idx;")
    op.execute("drop table if exists public.clickup_workspaces;")
    op.execute("alter table public.users drop column if exists clickup_user_id;")
    op.execute("alter table public.users drop column if exists clickup_access_token_enc;")

    # Restore the original grants from 0001.
    op.execute("grant select on public.clients to pulse_anon;")
    op.execute("grant update (last_active_at) on public.clients to pulse_anon;")
    op.execute("grant select on public.cards to pulse_anon;")
    op.execute("grant select, insert, update on public.responses to pulse_anon;")
