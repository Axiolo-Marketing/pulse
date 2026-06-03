"""per-user API keys

Revision ID: 0003
Revises: 0001
Create Date: 2026-06-03

Adds the `api_keys` table that backs per-user Bearer-header auth — the
foundation for non-browser callers (CLI, CI, MCP). Keys live on the user,
not on engagements; the bearer path converges on the same `users` row the
session cookie path resolves to and reuses the existing `is_admin` gate.

No RLS on this table — the application auth layer is sufficient because
keys carry no tenant rows themselves, and `pulse_anon` never touches them
(the bearer path always runs on a `pulse_admin` session).
"""
from alembic import op

revision = "0003"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table public.api_keys (
            id           uuid primary key default gen_random_uuid(),
            user_id      uuid not null references public.users(id) on delete cascade,
            prefix       char(8) not null,
            key_hash     text not null,
            label        text not null,
            last_used_at timestamptz null,
            revoked_at   timestamptz null,
            created_at   timestamptz not null default now()
        );
        """
    )
    op.execute(
        "create index api_keys_prefix_idx on public.api_keys (prefix) "
        "where revoked_at is null;"
    )
    op.execute(
        "create index api_keys_user_id_idx on public.api_keys (user_id);"
    )
    op.execute(
        "grant select, insert, update, delete on public.api_keys to pulse_admin;"
    )


def downgrade() -> None:
    op.execute("drop table if exists public.api_keys cascade;")
