"""MCP OAuth 2.1 authorization-server store

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-18

Backs the first-party OAuth 2.1 authorization server that lets Claude
Desktop's "Add custom connector" flow authenticate against Pulse. Three
tables, all app-layer gated (no RLS), mirroring ``api_keys`` (0003):

* ``oauth_clients`` — Dynamic Client Registration (RFC 7591) records.
  Claude self-registers a public client (``token_endpoint_auth_method =
  none``), so ``client_secret_hash`` is nullable.
* ``oauth_authorization_codes`` — single-use PKCE authorization codes
  minted by the consent page, bound to ``(user, org, client)``. Deleted
  on exchange; rejected after ``expires_at``.
* ``oauth_grants`` — the issued access + refresh token pair per
  authorization. Opaque tokens are stored as ``prefix`` + SHA-256
  ``hash`` exactly like API keys, so a grant is instantly revocable
  (``revoked_at``) and tokens never live on disk in plaintext.

No RLS: ``pulse_anon``/``pulse_member`` never touch these tables — the
OAuth provider + verifier always run on a ``pulse_admin`` (BYPASSRLS)
session because the OAuth/DCR/token paths must resolve clients and
grants across orgs before any tenant context exists. The tenant binding
lives in the ``org_id`` FK + the access token's ``org_id`` claim, which
flows into the existing ``pulse_member`` member session per tool call.

``downgrade()`` drops the three tables in dependency-safe order.
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── oauth_clients — DCR records ──────────────────────────────────────
    op.execute(
        """
        create table public.oauth_clients (
            id                         uuid primary key default gen_random_uuid(),
            client_id                  text unique not null,
            client_secret_hash         text null,
            redirect_uris              jsonb not null,
            grant_types                jsonb not null,
            response_types             jsonb not null,
            token_endpoint_auth_method text not null,
            client_name                text null,
            scope                      text null,
            created_at                 timestamptz not null default now()
        );
        """
    )

    # ── oauth_authorization_codes — single-use PKCE codes ────────────────
    op.execute(
        """
        create table public.oauth_authorization_codes (
            id                              uuid primary key default gen_random_uuid(),
            code_hash                       text unique not null,
            client_id                       text not null,
            user_id                         uuid not null references public.users(id) on delete cascade,
            org_id                          uuid not null references public.organizations(id) on delete cascade,
            redirect_uri                    text not null,
            redirect_uri_provided_explicitly boolean not null,
            code_challenge                  text not null,
            code_challenge_method           text not null default 'S256',
            scopes                          jsonb not null,
            resource                        text null,
            expires_at                      timestamptz not null,
            created_at                      timestamptz not null default now()
        );
        """
    )
    op.execute(
        "create index oauth_authorization_codes_code_hash_idx "
        "on public.oauth_authorization_codes (code_hash);"
    )

    # ── oauth_grants — issued access + refresh token pair ────────────────
    op.execute(
        """
        create table public.oauth_grants (
            id                  uuid primary key default gen_random_uuid(),
            access_prefix       char(8) not null,
            access_hash         text not null,
            access_expires_at   timestamptz not null,
            refresh_prefix      char(8) null,
            refresh_hash        text null,
            refresh_expires_at  timestamptz null,
            user_id             uuid not null references public.users(id) on delete cascade,
            org_id              uuid not null references public.organizations(id) on delete cascade,
            client_id           text not null,
            scopes              jsonb not null,
            resource            text null,
            revoked_at          timestamptz null,
            last_used_at        timestamptz null,
            created_at          timestamptz not null default now()
        );
        """
    )
    op.execute(
        "create index oauth_grants_access_prefix_idx "
        "on public.oauth_grants (access_prefix) where revoked_at is null;"
    )
    op.execute(
        "create index oauth_grants_refresh_prefix_idx "
        "on public.oauth_grants (refresh_prefix) where revoked_at is null;"
    )

    # ── grants — pulse_admin only, like api_keys ─────────────────────────
    op.execute(
        "grant select, insert, update, delete "
        "on public.oauth_clients to pulse_admin;"
    )
    op.execute(
        "grant select, insert, update, delete "
        "on public.oauth_authorization_codes to pulse_admin;"
    )
    op.execute(
        "grant select, insert, update, delete "
        "on public.oauth_grants to pulse_admin;"
    )


def downgrade() -> None:
    op.execute("drop table if exists public.oauth_grants cascade;")
    op.execute(
        "drop table if exists public.oauth_authorization_codes cascade;"
    )
    op.execute("drop table if exists public.oauth_clients cascade;")
