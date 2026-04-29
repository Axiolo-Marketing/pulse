-- Pulse v1 schema
-- Run this once against a fresh Supabase project (SQL Editor → New query → Run).
-- Idempotent: safe to re-run.

-- ──────────────────────────────────────────────────────────────────────────
-- Extensions
-- ──────────────────────────────────────────────────────────────────────────
create extension if not exists "pgcrypto";

-- ──────────────────────────────────────────────────────────────────────────
-- Tables
-- ──────────────────────────────────────────────────────────────────────────

create table if not exists public.clients (
  id               uuid primary key default gen_random_uuid(),
  name             text not null,
  org_name         text,
  engagement_name  text,
  token            text not null unique,
  created_at       timestamptz not null default now(),
  last_active_at   timestamptz
);

create table if not exists public.cards (
  id             uuid primary key default gen_random_uuid(),
  client_id      uuid not null references public.clients(id) on delete cascade,
  order_index    integer not null,
  category       text not null,
  title          text not null,
  context        text not null,
  question       text not null,
  response_type  text not null
    check (response_type in (
      'confirm-edit','single-select','multi-select','short-text',
      'long-text','file-upload','document-link','contact-share'
    )),
  options        jsonb,
  default_value  text,
  skip_allowed   boolean not null default true,
  created_at     timestamptz not null default now(),
  unique (client_id, order_index)
);

-- attachment_path is the path (relative to the site base) of an HTML
-- reference file to render inside a "View Active Reference" iframe modal.
-- Files live in pulse/public/deliverables/. Nullable: most cards have none.
alter table public.cards
  add column if not exists attachment_path text;

create table if not exists public.responses (
  id              uuid primary key default gen_random_uuid(),
  card_id         uuid not null references public.cards(id) on delete cascade,
  client_id       uuid not null references public.clients(id) on delete cascade,
  state           text not null
    check (state in ('not_started','viewed','answered','skipped','needs_edit')),
  response_value  jsonb,
  viewed_at       timestamptz,
  answered_at     timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (card_id, client_id)
);

create table if not exists public.uploads (
  id               uuid primary key default gen_random_uuid(),
  card_id          uuid not null references public.cards(id) on delete cascade,
  client_id        uuid not null references public.clients(id) on delete cascade,
  file_name        text not null,
  file_size_bytes  integer not null,
  storage_path     text not null,
  mime_type        text,
  uploaded_at      timestamptz not null default now()
);

-- ──────────────────────────────────────────────────────────────────────────
-- Indexes
-- ──────────────────────────────────────────────────────────────────────────

create index if not exists cards_client_order_idx
  on public.cards (client_id, order_index);

create index if not exists responses_client_idx
  on public.responses (client_id);

create index if not exists uploads_client_card_idx
  on public.uploads (client_id, card_id);

-- ──────────────────────────────────────────────────────────────────────────
-- updated_at trigger for responses
-- ──────────────────────────────────────────────────────────────────────────

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists responses_set_updated_at on public.responses;
create trigger responses_set_updated_at
before update on public.responses
for each row execute function public.set_updated_at();

-- ──────────────────────────────────────────────────────────────────────────
-- Row Level Security
--
-- The frontend uses the anon key. Each request also sends the user's
-- token in an x-pulse-token request header. Policies look up that
-- header via PostgREST's request.headers GUC and only return rows whose
-- client_id matches a clients row with that token. Without the header,
-- the anon role sees nothing.
-- ──────────────────────────────────────────────────────────────────────────

alter table public.clients   enable row level security;
alter table public.cards     enable row level security;
alter table public.responses enable row level security;
alter table public.uploads   enable row level security;

-- Helper: token presented in this request, or NULL.
create or replace function public.pulse_request_token()
returns text
language sql
stable
as $$
  select nullif(
    coalesce(
      current_setting('request.headers', true)::jsonb->>'x-pulse-token',
      ''
    ),
    ''
  );
$$;

-- Helper: client id for the presented token, or NULL.
create or replace function public.pulse_request_client_id()
returns uuid
language sql
stable
as $$
  select id
  from public.clients
  where token = public.pulse_request_token()
  limit 1;
$$;

-- Grants. Default Supabase grants on public schema cover anon, but be
-- explicit so this schema is self-contained.
grant usage on schema public to anon, authenticated;
grant select on public.clients   to anon, authenticated;
-- Column-scoped update on clients: anon can only touch last_active_at,
-- never the token or anything else, even with a valid RLS check.
grant update (last_active_at) on public.clients to anon, authenticated;
grant select on public.cards     to anon, authenticated;
grant select, insert, update on public.responses to anon, authenticated;
grant select, insert on public.uploads to anon, authenticated;

-- ─── clients: read your own row by token ─────────────────────────────────
drop policy if exists clients_self_read on public.clients;
create policy clients_self_read
  on public.clients for select
  to anon, authenticated
  using (token = public.pulse_request_token());

drop policy if exists clients_self_touch on public.clients;
create policy clients_self_touch
  on public.clients for update
  to anon, authenticated
  using (token = public.pulse_request_token())
  with check (token = public.pulse_request_token());

-- ─── cards: read cards belonging to your client ──────────────────────────
drop policy if exists cards_self_read on public.cards;
create policy cards_self_read
  on public.cards for select
  to anon, authenticated
  using (client_id = public.pulse_request_client_id());

-- ─── responses: read/write your own ──────────────────────────────────────
drop policy if exists responses_self_read on public.responses;
create policy responses_self_read
  on public.responses for select
  to anon, authenticated
  using (client_id = public.pulse_request_client_id());

drop policy if exists responses_self_insert on public.responses;
create policy responses_self_insert
  on public.responses for insert
  to anon, authenticated
  with check (client_id = public.pulse_request_client_id());

drop policy if exists responses_self_update on public.responses;
create policy responses_self_update
  on public.responses for update
  to anon, authenticated
  using (client_id = public.pulse_request_client_id())
  with check (client_id = public.pulse_request_client_id());

-- ─── uploads: read/write your own ────────────────────────────────────────
drop policy if exists uploads_self_read on public.uploads;
create policy uploads_self_read
  on public.uploads for select
  to anon, authenticated
  using (client_id = public.pulse_request_client_id());

drop policy if exists uploads_self_insert on public.uploads;
create policy uploads_self_insert
  on public.uploads for insert
  to anon, authenticated
  with check (client_id = public.pulse_request_client_id());

-- ──────────────────────────────────────────────────────────────────────────
-- Storage bucket
-- ──────────────────────────────────────────────────────────────────────────

insert into storage.buckets (id, name, public)
values ('pulse-uploads', 'pulse-uploads', false)
on conflict (id) do nothing;

-- Storage policies: file paths are {client_id}/{card_id}/{uuid}-{filename},
-- so the first path segment is the client id. Match it against the
-- authenticated token's client.

drop policy if exists pulse_uploads_self_read on storage.objects;
create policy pulse_uploads_self_read
  on storage.objects for select
  to anon, authenticated
  using (
    bucket_id = 'pulse-uploads'
    and (storage.foldername(name))[1] = public.pulse_request_client_id()::text
  );

drop policy if exists pulse_uploads_self_insert on storage.objects;
create policy pulse_uploads_self_insert
  on storage.objects for insert
  to anon, authenticated
  with check (
    bucket_id = 'pulse-uploads'
    and (storage.foldername(name))[1] = public.pulse_request_client_id()::text
  );
