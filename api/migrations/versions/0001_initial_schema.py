"""initial schema: clients, cards, responses, uploads, users, oauth_identities + RLS

Revision ID: 0001
Revises:
Create Date: 2026-05-13

This migration ports the original Supabase schema, dropping PostgREST-specific
bits and adding the user/oauth tables for the new operator auth model.

The migration uses raw SQL because Alembic autogenerate cannot represent RLS
policies, triggers, helper functions, or column-scoped grants.
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create extension if not exists \"pgcrypto\";")

    # ── Client-facing tables (ported from supabase/schema.sql) ────────────
    op.execute(
        """
        create table public.clients (
            id              uuid primary key default gen_random_uuid(),
            name            text not null,
            org_name        text,
            engagement_name text,
            token           text not null unique,
            brief           text,
            created_at      timestamptz not null default now(),
            last_active_at  timestamptz
        );
        """
    )

    op.execute(
        """
        create table public.cards (
            id              uuid primary key default gen_random_uuid(),
            client_id       uuid not null references public.clients(id) on delete cascade,
            order_index     integer not null,
            category        text not null,
            title           text not null,
            context         text not null,
            question        text not null,
            response_type   text not null
                check (response_type in (
                    'confirm-edit','single-select','multi-select','short-text',
                    'long-text','file-upload','document-link','contact-share'
                )),
            options         jsonb,
            default_value   text,
            skip_allowed    boolean not null default true,
            attachment_path text,
            created_at      timestamptz not null default now(),
            unique (client_id, order_index)
        );
        """
    )

    op.execute(
        """
        create table public.responses (
            id             uuid primary key default gen_random_uuid(),
            card_id        uuid not null references public.cards(id) on delete cascade,
            client_id      uuid not null references public.clients(id) on delete cascade,
            state          text not null
                check (state in ('not_started','viewed','answered','skipped','needs_edit')),
            response_value jsonb,
            viewed_at      timestamptz,
            answered_at    timestamptz,
            created_at     timestamptz not null default now(),
            updated_at     timestamptz not null default now(),
            unique (card_id, client_id)
        );
        """
    )

    op.execute(
        """
        create table public.uploads (
            id              uuid primary key default gen_random_uuid(),
            card_id         uuid not null references public.cards(id) on delete cascade,
            client_id       uuid not null references public.clients(id) on delete cascade,
            file_name       text not null,
            file_size_bytes integer not null,
            storage_path    text not null,
            mime_type       text,
            uploaded_at     timestamptz not null default now()
        );
        """
    )

    # ── Indexes ────────────────────────────────────────────────────────────
    op.execute("create index cards_client_order_idx   on public.cards (client_id, order_index);")
    op.execute("create index responses_client_idx     on public.responses (client_id);")
    op.execute("create index uploads_client_card_idx  on public.uploads (client_id, card_id);")

    # ── updated_at trigger for responses ──────────────────────────────────
    op.execute(
        """
        create or replace function public.set_updated_at()
        returns trigger language plpgsql as $$
        begin
            new.updated_at = now();
            return new;
        end;
        $$;
        """
    )
    op.execute(
        """
        create trigger responses_set_updated_at
        before update on public.responses
        for each row execute function public.set_updated_at();
        """
    )

    # ── Operator auth tables (new) ────────────────────────────────────────
    op.execute(
        """
        create table public.users (
            id                uuid primary key default gen_random_uuid(),
            email             text not null unique,
            password_hash     text,
            name              text,
            is_admin          boolean not null default false,
            email_verified_at timestamptz,
            created_at        timestamptz not null default now(),
            last_login_at     timestamptz
        );
        """
    )

    op.execute(
        """
        create table public.oauth_identities (
            id               uuid primary key default gen_random_uuid(),
            user_id          uuid not null references public.users(id) on delete cascade,
            provider         text not null check (provider in ('google','microsoft')),
            provider_user_id text not null,
            created_at       timestamptz not null default now(),
            unique (provider, provider_user_id)
        );
        """
    )
    op.execute("create index oauth_identities_user_idx on public.oauth_identities (user_id);")

    # ── Helper functions ──────────────────────────────────────────────────
    # pulse_request_token() reads a session-local GUC set by the API
    # middleware via `SET LOCAL pulse.token = $1` before any query.
    op.execute(
        """
        create or replace function public.pulse_request_token()
        returns text language sql stable as $$
            select nullif(current_setting('pulse.token', true), '');
        $$;
        """
    )

    op.execute(
        """
        create or replace function public.pulse_request_client_id()
        returns uuid language sql stable as $$
            select id from public.clients
            where token = public.pulse_request_token()
            limit 1;
        $$;
        """
    )

    # ── Grants — only pulse_anon needs explicit grants. pulse_admin has
    # BYPASSRLS and gets full grants below for convenience. ─────────────
    op.execute("grant usage on schema public to pulse_anon, pulse_admin;")

    op.execute("grant execute on function public.pulse_request_token()      to pulse_anon, pulse_admin;")
    op.execute("grant execute on function public.pulse_request_client_id()  to pulse_anon, pulse_admin;")

    op.execute("grant select                       on public.clients   to pulse_anon;")
    op.execute("grant update (last_active_at)      on public.clients   to pulse_anon;")
    op.execute("grant select                       on public.cards     to pulse_anon;")
    op.execute("grant select, insert, update       on public.responses to pulse_anon;")
    op.execute("grant select, insert, delete       on public.uploads   to pulse_anon;")

    op.execute("grant all on all tables   in schema public to pulse_admin;")
    op.execute("grant all on all sequences in schema public to pulse_admin;")

    # ── Row Level Security ────────────────────────────────────────────────
    for tbl in ("clients", "cards", "responses", "uploads"):
        op.execute(f"alter table public.{tbl} enable row level security;")

    # clients
    op.execute(
        """
        create policy clients_self_read on public.clients
            for select to pulse_anon
            using (token = public.pulse_request_token());
        """
    )
    op.execute(
        """
        create policy clients_self_touch on public.clients
            for update to pulse_anon
            using (token = public.pulse_request_token())
            with check (token = public.pulse_request_token());
        """
    )

    # cards
    op.execute(
        """
        create policy cards_self_read on public.cards
            for select to pulse_anon
            using (client_id = public.pulse_request_client_id());
        """
    )

    # responses
    op.execute(
        """
        create policy responses_self_read on public.responses
            for select to pulse_anon
            using (client_id = public.pulse_request_client_id());
        """
    )
    op.execute(
        """
        create policy responses_self_insert on public.responses
            for insert to pulse_anon
            with check (client_id = public.pulse_request_client_id());
        """
    )
    op.execute(
        """
        create policy responses_self_update on public.responses
            for update to pulse_anon
            using (client_id = public.pulse_request_client_id())
            with check (client_id = public.pulse_request_client_id());
        """
    )

    # uploads
    op.execute(
        """
        create policy uploads_self_read on public.uploads
            for select to pulse_anon
            using (client_id = public.pulse_request_client_id());
        """
    )
    op.execute(
        """
        create policy uploads_self_insert on public.uploads
            for insert to pulse_anon
            with check (client_id = public.pulse_request_client_id());
        """
    )
    op.execute(
        """
        create policy uploads_self_delete on public.uploads
            for delete to pulse_anon
            using (client_id = public.pulse_request_client_id());
        """
    )


def downgrade() -> None:
    # RLS policies
    for policy, tbl in [
        ("uploads_self_delete",  "uploads"),
        ("uploads_self_insert",  "uploads"),
        ("uploads_self_read",    "uploads"),
        ("responses_self_update","responses"),
        ("responses_self_insert","responses"),
        ("responses_self_read",  "responses"),
        ("cards_self_read",      "cards"),
        ("clients_self_touch",   "clients"),
        ("clients_self_read",    "clients"),
    ]:
        op.execute(f"drop policy if exists {policy} on public.{tbl};")

    for tbl in ("uploads", "responses", "cards", "clients"):
        op.execute(f"alter table public.{tbl} disable row level security;")

    op.execute("drop function if exists public.pulse_request_client_id();")
    op.execute("drop function if exists public.pulse_request_token();")

    op.execute("drop trigger if exists responses_set_updated_at on public.responses;")
    op.execute("drop function if exists public.set_updated_at();")

    for tbl in ("oauth_identities", "users", "uploads", "responses", "cards", "clients"):
        op.execute(f"drop table if exists public.{tbl} cascade;")
