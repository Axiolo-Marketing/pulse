# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Pulse is a mobile-first decision/validation tool. A consultant sends a single secure URL to a client; the client taps through a deck of pre-populated decision cards on their phone, confirms/corrects/uploads, and progress saves continuously. The operator manages engagements in `/admin/` and exports responses to ClickUp.

`SPEC.md` is the canonical product spec (data model, response-type semantics, ClickUp export format, operator runbook). Visual-identity sections may lag the actual codebase tokens — the CSS in `src/styles/` is authoritative for brand.

The design document for the migration off Supabase to the current stack lives at `~/.claude/plans/now-that-you-know-inherited-allen.md`. It covers the **why** behind nearly every architectural choice. Read it before reopening anything load-bearing.

## Commands

Everything goes through Docker Compose at the project root. The Makefile is the canonical interface:

```bash
make dev                       # docker compose up --build
make down                      # docker compose down
make test                      # pytest inside the backend container (80% coverage gate)
make migrate                   # alembic upgrade head against the dev DB
make makemigration m='msg'     # alembic revision --autogenerate
make backend-shell             # sh inside the backend container
make seed-dev                  # idempotent dev admin user (dev@example.com / dev-admin-password)
make reset-test-db             # drop + recreate pulse_test (rarely needed — see below)
```

`make seed-dev` creates a verified admin user in the dev DB so you can sign in to `/admin/` immediately. Idempotent — running it twice is fine; override defaults via `DEV_ADMIN_EMAIL` / `DEV_ADMIN_PASSWORD` / `DEV_ADMIN_NAME`. Use this instead of hand-running SQL when you need to click through the UI.

`make reset-test-db` exists for the branch-switching edge case: if a feature branch added migration `0002` and you applied it to `pulse_test` (via `make test`), then switched back to `main` where that revision doesn't exist, the next `make test` fails with `Can't locate revision`. The `_migrate` fixture auto-recovers from this — drops + recreates the DB and retries once — but if you want to do it manually, this target is the same operation.

Service ports default to standard (5432/8000/4321) but the local `.env` remaps to `55432/58000/14321` because this dev machine has other Compose stacks on those ports. Override via `DB_HOST_PORT` / `BACKEND_HOST_PORT` / `FRONTEND_HOST_PORT` in `.env`.

There is no host-side `npm` or `python` install required for local development — everything lives in containers. **Production builds run on the VPS**: the Ansible playbook `git pull`s the repo at `/opt/pulse/source`, then runs `uv sync` (backend) and `npm ci && npm run build` (frontend) there. The shared VPS therefore has `nodejs` + `npm` installed via apt (`state: present` only, no third-party repos). This pattern matches the other Axiolo deploys (`image-compressor`, `sitechecker`, `octoping`).

## Architecture: Astro frontend → FastAPI → Postgres (RLS preserved)

Pulse used to be a static Astro site talking directly to Supabase. It now has a real backend. The shape:

- **`src/pages/index.astro` + `src/scripts/app.ts`** — client-facing card deck. **Auth is unchanged from the user's perspective**: a 16-hex-char token in `?t=` becomes the `X-Pulse-Token` header on every API call. The frontend has zero notion of database connections.
- **`src/pages/admin.astro` + `src/scripts/admin.ts`** — operator console. Email/password + Google OAuth + Microsoft 365 OAuth + signed-cookie sessions. The old SHA-256-password-hash-in-bundle is gone.
- **`src/lib/api.ts`** — the only data-layer module. Exposes `clientApi` (token-authed) + `authApi` + `adminApi` (cookie-authed). Both scripts import from here; **no module imports from anywhere else for HTTP**.
- **`api/`** — FastAPI app (Python 3.13, SQLModel ORM, asyncpg, Alembic). All endpoints under `/api/`.
- **Postgres** — same 4 tables as before (`clients`, `cards`, `responses`, `uploads`) plus 2 new (`users`, `oauth_identities`). All 9 RLS policies preserved.

The frontend and backend communicate over HTTP, but **RLS is still the multi-tenant backstop**: the FastAPI client middleware does `SET LOCAL pulse.token = $1` on a `pulse_anon` DB connection before every client-facing query. A bug in a route handler can't leak across tenants because the database refuses.

### The role-flip pattern (read this before writing tests)

Three Postgres roles:
- `pulse_owner` — schema owner; runs migrations; bypasses RLS by virtue of ownership.
- `pulse_anon` — no `BYPASSRLS`. The middleware (`get_anon_session` in `api/pulse_api/db.py`) connects as this role and sets the `pulse.token` GUC per request. RLS policies fire.
- `pulse_admin` — `BYPASSRLS`. Used by admin routes after the session-cookie auth gate (`get_current_admin`).

Tests can't easily use three separate connections (they wouldn't see each other's uncommitted seed data inside the rollback transaction). Instead, `tests/conftest.py` opens **one** connection as the owner role and flips it mid-transaction via `SET LOCAL ROLE pulse_anon` + `select set_config('pulse.token', ...)`. The override of `get_anon_session` in `client` fixture does exactly this; tests that want to exercise RLS directly (`tests/test_rls_isolation.py`) call the `become_anon(conn, token=...)` helper after seeding.

**Three gotchas the test pattern catches that you'll re-discover otherwise:**

1. **`SET LOCAL pulse.token = :t` does NOT accept bound params** — Postgres `SET` doesn't take `$1`. Use `select set_config('pulse.token', :t, true)`. Same bug nearly shipped to production; see `db.py` `get_anon_session`.
2. **`pytest-asyncio` defaults to a fresh event loop per test**, which breaks session-scoped SQLAlchemy engines (asyncpg callbacks bound to the wrong loop). Fixed via `asyncio_default_fixture_loop_scope = "session"` + `asyncio_default_test_loop_scope = "session"` in `pyproject.toml`.
3. **`coverage.py` doesn't follow into greenlet workers** — SQLAlchemy async runs ORM ops via `_greenlet_spawn`, so without `concurrency = ["thread", "greenlet"]` in `[tool.coverage.run]`, route + repo coverage gets under-reported by ~15%. Tests pass but the gate looks misleadingly low.

## Auth subsystems (two of them, share zero code)

- **Client** (the consultant's customer): magic URL `?t=<16-hex>`. Frontend sends it as `X-Pulse-Token`. Backend middleware sets `pulse.token` on a `pulse_anon` connection; RLS does the rest.
- **User** (operator — Tom, future teammates): email+password OR Google OAuth OR Microsoft 365 OAuth, signed-cookie session via `itsdangerous`. Session middleware (`get_current_admin`) loads the user record on a `pulse_admin` connection. `is_admin=true` required for `/api/admin/*` routes.

Token primitives all use `itsdangerous.URLSafeTimedSerializer` with per-purpose salts (`pulse-session`, `pulse-email-verify`, `pulse-password-reset`, `pulse-oauth-state-google`, `pulse-oauth-state-microsoft`). A token signed for one purpose can never redeem as another even with the same `SESSION_SECRET` — explicit tests lock this in (`tests/unit/test_tokens.py`).

OAuth state: cookie-based CSRF. The authorize endpoint generates a random state, signs it as a `oauth_state_{provider}` cookie, builds the provider URL with the same state. The callback verifies the cookie matches the URL state before doing any work.

## Database

Schema in `api/migrations/versions/0001_initial_schema.py`. All raw SQL via `op.execute()` because Alembic autogen can't represent RLS / triggers / helper functions / column-scoped grants. SQLModel classes in `api/pulse_api/models/` are the canonical type sources for future autogen migrations.

The `db-init` directory contains `01-pulse-roles.sql` — runs once on a fresh DB volume to create the `pulse_anon` + `pulse_admin` roles + the `pulse_test` database. In production this is done by the Ansible `postgres-pulse` role from vaulted vars.

Adding a card response type means: (1) update the `response_type` CHECK constraint in a new Alembic migration, (2) extend the renderer in `src/scripts/app.ts`, (3) extend the admin display formatter in `admin.ts`, (4) extend `src/lib/status-suggest.ts` for ClickUp status. SPEC §4 has the canonical list + `response_value` shapes.

## File uploads

Local-disk under `settings.upload_dir` (`/var/lib/pulse/uploads/` in prod). Path convention `{client_id}/{card_id}/{uuid}-{filename}`. The trust boundary is the `client_id` prefix — `api/pulse_api/storage.py` reconstructs paths from authenticated state (`pulse_request_client_id()`), never from the request body.

`resolve_within_upload_dir()` is the traversal defense: refuses absolute paths, `..` traversal, empty strings, and anything that resolves outside the upload root. Every disk read/write goes through it.

Tests get an autouse `tmp_uploads_dir` fixture that monkeypatches `settings.upload_dir` to a per-test tempdir, so files never leak across tests or to the dev volume.

## Active References (HTML deliverables)

`public/deliverables/<slug>.html` are static deliverables that cards can link to via `cards.attachment_path`. The client renders them in a sandboxed iframe modal (`sandbox="allow-scripts"`, no same-origin). Drop the file, push, wire the path from the admin Edit form — no code change.

## Brand system

Axiolo brand. Design tokens in `src/styles/pulse.css` (`--primary` `#2960F6`, `--ink` navy, `--warning`, Plus Jakarta Sans). `src/styles/admin.css` extends them. The Axiolo design system reference lives at `~/projects/axiolo/axiolo-branding/axiolo-design-system.md`.

## Tests

Pytest only, inside the backend container. Run `make test` (or `docker compose exec backend uv run pytest`). The suite has 232 tests at ~92% coverage, with the gate enforced at 80% via `pyproject.toml`. Three layers:

- **Unit tests** in `api/tests/unit/` — pure functions: argon2 password hashing, signed-cookie sessions, token roundtrip + salt isolation, storage path validation. No DB.
- **Endpoint tests** in `api/tests/test_*.py` — `httpx.AsyncClient` with `ASGITransport(app)`, each test wrapped in a transaction that rolls back at teardown. Provider HTTP (OAuth) mocked via `respx`.
- **RLS isolation** in `tests/test_rls_isolation.py` — direct SQL via `become_anon()`, no FastAPI. Proves the database-layer backstop independent of the application layer.

Fixtures of note: `client_authed` (token-authed httpx client), `admin_authed` (admin-session-authed httpx client), `seed_client` / `other_seeded_client` / `seed_cards` / `seed_user` / `seed_admin_user` / `captured_emails` / `tmp_uploads_dir`.

## Deployment

Production runs on a **shared Debian VPS** (other apps coexist on the same host). Ansible playbook in `deploy/`. See `deploy/README.md` for the full runbook.

**Critical constraint, baked into every role:** Ansible only performs narrow shared-host setup: install missing apt packages with `state: present` (no upgrades, no third-party repositories — `nodejs` + `npm` come from Debian apt, not NodeSource), and start/enable Postgres + nginx. The other roles (`postgres-pulse`, `backend`, `frontend`, `nginx-site`, `tls`) are scoped to their own paths/units/DB/roles only. First-run command is always `ansible-playbook deploy.yml --check --diff` so the operator can read every diff before applying.

If you're editing Ansible: anything outside prerequisite package installation/service start and Pulse's owned paths (`/opt/pulse/`, `/etc/pulse/`, `/var/www/pulse/`, `/var/lib/pulse/`, `/etc/nginx/sites-available/pulse`, `pulse-api.service`, `pulse_*` DB roles, the `pulse` database) is a bug — even if the playbook syntax-checks clean. Pre-existing other apps depend on the rest of the box being untouched.

The repo is cloned on the VPS via the operator's `~/.ssh/github_deploy_key` — the same SSH key already used by `image-compressor`, `sitechecker`, and `octoping`. It has access to all Axiolo-Marketing repos, so no per-repo Deploy Key is needed. The preflight role copies it to `/etc/pulse/deploy_key` (0600, pulse-owned), and the backend role's `ansible.builtin.git` task uses `key_file:` against that path.

## Obsolete files still in the tree

Two top-level dirs from the pre-migration Supabase setup are unused but not deleted (would require explicit operator authorization):
- `scripts/` — `apply-sql.mjs` + `verify.mjs` used the old supabase-js + `pg` deps. The SQL they applied lives in Alembic migration `0001_initial_schema.py` now.
- `supabase/` — `schema.sql` + `seed.sql` are the source-of-truth-now-ported schema.

Safe to `rm -rf scripts/ supabase/` when ready.
