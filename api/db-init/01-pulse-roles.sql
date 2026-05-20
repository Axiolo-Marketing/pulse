-- Runs once when the Postgres container first initializes a fresh data dir.
-- Mounted into /docker-entrypoint-initdb.d/ by docker-compose. Executes as
-- the Postgres superuser (POSTGRES_USER from .env).
--
-- Creates the two restricted DB roles used by the FastAPI app:
--   pulse_anon  — used by client-facing routes; RLS applies
--   pulse_admin — used by admin routes; bypasses RLS
--
-- In production these roles + their passwords are created by Ansible's
-- postgres-pulse role from vaulted vars, not from this file.

create extension if not exists "pgcrypto";

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'pulse_anon') then
    create role pulse_anon with login password 'devpass' nosuperuser nobypassrls;
  end if;

  if not exists (select 1 from pg_roles where rolname = 'pulse_admin') then
    create role pulse_admin with login password 'devpass' nosuperuser bypassrls;
  end if;
end
$$;

-- Separate test database. Tests run Alembic against this DB once per session,
-- then wrap each test in a transaction that rolls back, so the dev pulse DB
-- stays untouched. Owned by the same superuser; pulse_anon/pulse_admin can
-- connect via the same passwords as the dev DB.
select 'create database pulse_test owner pulse'
where not exists (select 1 from pg_database where datname = 'pulse_test')
\gexec
