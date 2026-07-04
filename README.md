# Pulse

Mobile-first decision and validation tool. A consultant sends a single secure
URL to a client; the client taps through a deck of pre-populated decision
cards on their phone, confirms or corrects, uploads documents where needed,
and progress saves automatically.

As of v3.0, Pulse runs as an invite-only multi-tenant SaaS. Any number of
consulting orgs can sign in, each with their own clients, members, invites,
API keys, and audit log — isolated at the database via Postgres RLS, with the
application layer reinforcing the same scope through a session-driven
`pulse.org_id` GUC.

## What lives where

| File / dir | Purpose |
|---|---|
| `SPEC.md` | Canonical product spec — data model, response types, multi-tenant model, API surface, deployment notes. Read this first if you're new. |
| `CLAUDE.md` | Engineering guide — architecture invariants, the role-flip pattern, the three test gotchas, conventions for new code. |
| `.claude/multi-tenant-workflow.md` | The sub-agent orchestration contract used to build the multi-tenant migration. Reference for future large initiatives. |
| `deploy/README.md` | Production deployment runbook (Ansible on a shared Debian VPS). |
| `api/` | FastAPI backend — Python 3.13, SQLModel, asyncpg, Alembic. |
| `src/` | Astro frontend — v1 (vanilla TypeScript, current default) and v2 (React + Tailwind + shadcn/ui, opt-in) ship side by side during the migration. |
| `api/migrations/versions/` | Alembic migrations. `0001` (initial port from Supabase) → `0015` (latest, multi-respondent recipients). |
| `api/db-init/` | Postgres role bootstrap SQL — runs once on a fresh DB volume. |
| `public/deliverables/` | Static HTML "active references" that cards can link to via `attachment_path`. |

## Quick start (local dev)

Everything runs in Docker Compose. There is no host-side Python or npm install.

```bash
make dev          # docker compose up --build
make migrate      # alembic upgrade head against the dev DB
make seed-dev     # create the dev admin user — dev@example.com / dev-admin-password
make test         # pytest inside the backend container (80% coverage gate)
make down         # docker compose down
```

Sign in at <http://localhost:14321/admin/> with the seeded credentials. The
client-facing deck lives at the root (`/`) and is reached via a 16-hex token
appended as `?t=…` — copy a real one from the admin engagement detail page.

Service ports default to standard (5432 / 8000 / 4321) but the local `.env`
remaps to `55432 / 58000 / 14321` because this dev machine has other Compose
stacks. Override via `DB_HOST_PORT`, `BACKEND_HOST_PORT`, `FRONTEND_HOST_PORT`.

## Stack at a glance

- **Backend**: FastAPI · Python 3.13 · SQLModel · asyncpg · Alembic
- **Database**: Self-hosted Postgres 16 with four roles (`pulse_owner`, `pulse_anon`, `pulse_member`, `pulse_admin`) and RLS as the multi-tenant backstop
- **Frontend**: Astro 5 · vanilla TypeScript · brand tokens in `src/styles/pulse.css`
- **Auth**: signed-cookie sessions via `itsdangerous`; per-`(user, org)` API keys (`Authorization: Bearer pulse_<key>`); Google + Microsoft OAuth (invite-only — no auto-signup)
- **MCP**: FastMCP mounted at `/api/mcp/`, same Bearer-key auth
- **Deploy**: Ansible playbook in `deploy/`, deployed to a shared Debian VPS

## Multi-tenant invariants

1. **`pulse_member` Postgres role has no `BYPASSRLS`.** Operator endpoints run on this role with `pulse.org_id` set per request. A forgotten `where org_id = ...` in a handler cannot leak across tenants — the database refuses.
2. **`pulse.org_id` is a GUC, never a bound param to `SET`.** Use `select set_config('pulse.org_id', :id, true)`. The `SET LOCAL pulse.org_id = $1` form silently fails because Postgres `SET` doesn't accept bound params (this bug nearly shipped during the v2 build for `pulse.token`).
3. **Invites store only the SHA-256 hash of the signed token.** The raw token lives in the email link and the OAuth state cookie — never in the DB.
4. **Audit writes happen in the same transaction as the user action.** A failed action rolls back the audit row too.

## Onboarding the first prod superadmin

The migration that introduces multi-tenancy (`0004_multi_tenant.py`) reads
`SUPERADMIN_EMAILS` at execution time and promotes the named users to
`is_superadmin = true`. Set the env var on the VPS **before** running
`alembic upgrade head` for the first time. If you forget, run a one-shot
`UPDATE users SET is_superadmin = true WHERE lower(email) = ...` against the
prod DB.

After that, the superadmin signs in to `/admin/`, navigates to
`/admin/#superadmin`, creates an org, and emails the owner the invite link.
The owner clicks → sets a password (or signs in via Google / Microsoft) →
lands in their org's admin view.

## Contributing

Read `CLAUDE.md` end-to-end before opening a PR — especially the
"role-flip pattern" and "three gotchas" sections. They describe failure modes
that aren't obvious from the code alone and that the test suite is set up to
catch.

When the work is large enough to warrant decomposition (anything spanning
auth + schema + UI), `.claude/multi-tenant-workflow.md` is the sub-agent
orchestration pattern that produced the v2→v3 migration in 6 stacked PRs. It
defines: when to spawn the UI/UX supervisor pre-design vs. post-build, how the
test author should parametrize (collapsing N hand-written cases into ~3
functions per file), and what the code reviewer's confidence threshold is.

## License

Internal Axiolo project. Source-available to maintainers; not licensed for
external use.
