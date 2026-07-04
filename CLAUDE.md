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

- **`src/pages/index.astro` + `src/scripts/app.ts`** — client-facing card deck. **Auth is unchanged from the user's perspective**: a 16-hex-char token in `?t=` becomes the `X-Pulse-Token` header on every API call. The frontend has zero notion of database connections. As of migration `0015` that token identifies a per-**recipient** row, not the engagement (see "Multi-respondent engagements" below) — the deck UI is otherwise unchanged.
- **`src/pages/admin.astro` + `src/scripts/admin.ts`** — operator console. Email/password + Google OAuth + Microsoft 365 OAuth + signed-cookie sessions. The old SHA-256-password-hash-in-bundle is gone.
- **`src/lib/api.ts`** — the only data-layer module. Exposes `clientApi` (token-authed) + `authApi` + `adminApi` (cookie-authed). Both scripts import from here; **no module imports from anywhere else for HTTP**.
- **`api/`** — FastAPI app (Python 3.13, SQLModel ORM, asyncpg, Alembic). All endpoints under `/api/`.
- **Postgres** — the tenant model is `organizations` → `clients` (real customer companies) → `engagements` (the shared card deck) → `recipients` (per-respondent magic-link tokens). `cards` hang off the engagement; `responses` + `uploads` hang off the **recipient** (each respondent answers independently). Plus `users` / `organization_memberships` / `oauth_identities` etc. for the operator side. RLS scopes every tenant table.

The frontend and backend communicate over HTTP, but **RLS is still the multi-tenant backstop**: the FastAPI client middleware does `SET LOCAL pulse.token = $1` on a `pulse_anon` DB connection before every client-facing query. A bug in a route handler can't leak across tenants because the database refuses.

### The role-flip pattern (read this before writing tests)

Four Postgres roles:
- `pulse_owner` — schema owner; runs migrations; bypasses RLS by virtue of ownership.
- `pulse_anon` — no `BYPASSRLS`. `get_anon_session` in `api/pulse_api/db.py` connects as this role and sets the `pulse.token` + `pulse.org_id` GUCs per request. RLS policies fire on every client-facing query.
- `pulse_member` — no `BYPASSRLS`. `get_org_scoped_session` connects as this role with `pulse.org_id` set to the active org. Owns the `/api/admin/*` surface — a forgotten `where org_id = ...` in a handler can't leak across tenants because RLS refuses.
- `pulse_admin` — `BYPASSRLS`. Reserved for `/api/superadmin/*` (PR 5), the OAuth/invite resolution paths that must cross orgs, and migrations.

Tests can't easily use four separate connections (they wouldn't see each other's uncommitted seed data inside the rollback transaction). Instead, `tests/conftest.py` opens **one** connection as the owner role and flips it mid-transaction via `SET LOCAL ROLE pulse_anon` (or `pulse_member`) + `select set_config('pulse.token', ...)` / `set_config('pulse.org_id', ...)`. The override of `get_anon_session` in the `client` fixture does exactly this; tests that want to exercise RLS directly (`tests/test_rls_isolation.py`, `tests/test_multi_tenant_isolation.py`) call the `become_anon(conn, token=...)` / `become_member(conn, org_id=...)` helpers after seeding.

**Three gotchas the test pattern catches that you'll re-discover otherwise:**

1. **`SET LOCAL pulse.token = :t` does NOT accept bound params** — Postgres `SET` doesn't take `$1`. Use `select set_config('pulse.token', :t, true)`. Same bug nearly shipped to production; see `db.py` `get_anon_session`.
2. **`pytest-asyncio` defaults to a fresh event loop per test**, which breaks session-scoped SQLAlchemy engines (asyncpg callbacks bound to the wrong loop). Fixed via `asyncio_default_fixture_loop_scope = "session"` + `asyncio_default_test_loop_scope = "session"` in `pyproject.toml`.
3. **`coverage.py` doesn't follow into greenlet workers** — SQLAlchemy async runs ORM ops via `_greenlet_spawn`, so without `concurrency = ["thread", "greenlet"]` in `[tool.coverage.run]`, route + repo coverage gets under-reported by ~15%. Tests pass but the gate looks misleadingly low.

## Auth subsystems (two of them, share zero code)

- **Client** (the consultant's customer): magic URL `?t=<16-hex>`. Frontend sends it as `X-Pulse-Token`. Backend middleware sets `pulse.token` and `pulse.org_id` (resolved from the **recipient** row that owns the token) on a `pulse_anon` connection; RLS does the rest. `pulse_request_engagement_id()` resolves token→recipient→engagement, so the shared `cards` policies are unchanged while `responses`/`uploads` scope to the recipient.
- **User** (operator — Tom, future teammates): email+password OR Google OAuth OR Microsoft 365 OAuth, signed-cookie session via `itsdangerous`. Session payload is `{user_id, active_org_id}`. Middleware (`get_current_org_member`) loads the `(user, membership)` pair, then `get_org_scoped_session` opens a `pulse_member` connection with `pulse.org_id` set. The `pulse_member` role has no BYPASSRLS, so a forgotten `where org_id = ...` in a handler can't leak cross-tenant. `users.is_admin` is gone — admin powers come from `organization_memberships.role = 'owner'`, and the cross-org superadmin tier comes from `users.is_superadmin`.

`get_current_user` resolves cookie OR `Authorization: Bearer pulse_<key>`. Cookie wins if both are present, EXCEPT for org attribution: a Bearer call always uses the key's `org_id`, never the cookie's. The corresponding deps:

- `get_current_user` — User only, no org context.
- `get_current_org_member` — `(user, membership)` for the active org.
- `require_owner` — 403 if membership.role != owner. Mount on owner-gated routes.
- `get_current_superadmin` — 403 unless `is_superadmin`. Uses `pulse_admin` (BYPASSRLS) by definition.
- `get_org_scoped_session` — yields the `pulse_member` session every `/api/admin/*` route depends on.

Active-org priority order: API key `org_id` → session `active_org_id` → `users.last_active_org_id` → 403. Pre-PR-2 cookies (no `active_org_id`) keep working by backfilling from the user row; the next sign-in mints a fresh cookie with the right shape.

Token primitives all use `itsdangerous.URLSafeTimedSerializer` with per-purpose salts (`pulse-session`, `pulse-email-verify`, `pulse-password-reset`, `pulse-oauth-state-google`, `pulse-oauth-state-microsoft`). A token signed for one purpose can never redeem as another even with the same `SESSION_SECRET` — explicit tests lock this in (`tests/unit/test_tokens.py`).

OAuth state: cookie-based CSRF. The authorize endpoint generates a random state, signs it as a `oauth_state_{provider}` cookie, builds the provider URL with the same state. The callback verifies the cookie matches the URL state before doing any work. **Self-signup is disabled**: when an OAuth callback resolves to an unknown email, the route looks up a pending `organization_invites` row for that email and accepts it transactionally (creates user + membership, marks the invite accepted). With no pending invite, the callback redirects to `/admin/?error=invitation_required` and creates no user.

### Multi-respondent engagements (recipients, invites, reminders)

An engagement is a shared deck of `cards`; each **recipient** (`recipients` table, migration `0015`) is one respondent with its own 16-hex `token`, email, per-recipient `last_active_at`, and invite/reminder state. Multiple recipients share one engagement's cards but answer independently — `responses`/`uploads` carry a `recipient_id` (unique `(card_id, recipient_id)`), so the operator sees who answered what. Existing single-link engagements were backfilled to one legacy recipient carrying the old token, so old links + answers still work.

- **Manage recipients** on the engagement detail page: add (mints a token), remove (FK-cascades their answers), per-recipient copy-link. Routes: `GET/POST /api/admin/engagements/{id}/recipients`, `DELETE …/recipients/{rid}`. MCP: `pulse_list_recipients` / `pulse_add_recipient`.
- **Invites** (automatic — no manual send step): adding a respondent emails them the deck link right away, via the `_send_pending_invites` helper in `admin_api.py` (fired from `add_recipient`, and from the card-creation routes so respondents added before the deck had questions get invited when the first card lands). It mails every recipient with an email + no `invited_at`, then stamps `invited_at` (no-op when the deck has no cards). `engagement_invite_email` in `auth/email_messages.py`.
- **Reminders** (scheduled): the daily `pulse_api.jobs.send_reminders` job (cron `/etc/cron.d/pulse-reminders`, BYPASSRLS `admin_engine`) nudges invited-but-unfinished recipients. Gated by `settings.reminders_enabled` (env `REMINDERS_ENABLED`, **off by default**) + a per-engagement `engagements.reminders_enabled` pause; eligibility honors inactivity/cadence/max day thresholds (all env-configurable). `recipients_repo.list_due_reminders` is the WHERE-clause that decides who's due.
- **Unsubscribe**: every invite/reminder email carries a signed per-recipient unsubscribe link (own salt `pulse-reminder-unsubscribe`, helpers in `pulse_api/reminders.py`). Public `POST /api/reminders/unsubscribe` (rate-limited, BYPASSRLS) + `src/pages/unsubscribe.astro` flip `unsubscribed_at`.

### Audit log + activity feed

Every mutating admin route writes a row to `audit_logs` via the small `record_audit(session, …)` helper in `api/pulse_api/audit.py`. The insert runs on the same session as the user action and commits atomically with it — a failed audit insert rolls the user action back too (correct behavior; don't try/except around it). Action names are a stable dotted enum (`client.create`, `member.invite`, `api_key.revoke`, etc.); the full set is the docstring of `audit.py` and the module-level `AUDIT_ACTIONS` frozenset. Tests assert both sides agree.

Read path is `GET /api/orgs/me/activity` — paginated, RLS-scoped, filterable by actor and action. The Activity tab on `/admin/#settings/activity` surfaces the feed inline; member-readable (no owner gate). Cross-org isolation is enforced by the same `pulse_member` + `pulse.org_id` policy as every other tenant table.

### API keys + MCP (non-browser callers)

Same operator identity, different credential. API keys are per-`(user, org)` Bearer tokens that let scripts and the MCP server reach the admin surface without holding a browser session.

- **Format**: `pulse_<32-hex>` (128 bits of entropy, `pulse_` prefix makes leaks greppable). On disk: SHA-256 of the raw key plus an indexed 8-char `prefix` column for cheap lookup. Constant-time hash compare. Argon2 is overkill — keys already have full entropy.
- **Scope**: each key is bound to an org (`api_keys.org_id`, NOT NULL post-0005). Bearer auth flips the request into a `pulse_member` session against the key's org — never the operator's session active org. This is what lets a developer hold one shell session against org A while running a script for org B via a key minted there.
- **Creation**: Settings page (`/admin/#settings`) → "API keys" section → Create. Defaults to the operator's active org; an `org_id` body field lets owners-of-multiple-orgs target a specific tenant (verified at create time). Raw key is shown **once** in the create modal; subsequent list views only show `pulse_<prefix>…`. Revoking sets `revoked_at` and stops the key immediately.
- **MCP endpoint**: mounted at `/api/mcp/` (trailing slash matters — FastMCP's `streamable_http_path="/"` resolves the sub-app's single route at the mount point; `/api/mcp` without the slash 307-redirects). Streamable HTTP transport (POST returns JSON or SSE depending on the call). Accepts **either** an `Authorization: Bearer pulse_<key>` API key **or** an OAuth access token (see "OAuth 2.1" below). Auth is enforced at the HTTP layer by the SDK's `RequireAuthMiddleware` — the **whole endpoint, including `tools/list`, requires a valid token** (an unauthenticated request 401s with the OAuth discovery challenge; that's correct for the connector flow, and legacy key callers always send their credential). `authenticate_request(ctx)` reads the already-validated `AccessToken` from context (`get_access_token()`) and returns `(user, org_id)` from its claims, so each tool opens a member-scoped session against that org.

Client config (Claude Code / claude.ai), production:

```json
{
  "mcpServers": {
    "pulse": {
      "url": "https://pulse.axiolo.com/api/mcp/",
      "headers": { "Authorization": "Bearer pulse_<key>" }
    }
  }
}
```

Local dev swaps the URL to `http://localhost:58000/api/mcp/`.

Design notes for the whole API-keys + MCP feature live at `~/.claude/plans/piped-noodling-cook.md` — read it before reopening anything load-bearing here.

#### OAuth 2.1 (Claude Desktop "custom connector")

Claude Desktop's connector flow speaks OAuth 2.1, not a static bearer key, so the MCP endpoint is **also** a first-party OAuth **authorization + resource server** (Anthropic `mcp` SDK's `OAuthAuthorizationServerProvider`). The two credential shapes — `pulse_<key>` and an OAuth access token — converge in one `PulseTokenVerifier` and both yield an `AccessToken` carrying `claims["org_id"]`, so the tool layer is unchanged.

- **Connect**: Claude Desktop → Settings → Connectors → Add custom connector → URL `https://pulse.axiolo.com/api/mcp`. Leave OAuth Client ID/Secret blank — Claude self-registers via Dynamic Client Registration (RFC 7591).
- **Flow**: unauthenticated `/api/mcp/` → `401` + `WWW-Authenticate: … resource_metadata=…` → Claude fetches the PRM + AS metadata, registers (DCR), opens `/authorize` in the browser → Pulse **consent page** (reuses the `/admin` session — sign in if needed; multi-org operators pick the org) → Approve → auth code → `/token` (PKCE S256) → audience-bound access + refresh tokens → connected.
- **Endpoints live at the domain ROOT** (issuer = `frontend_base_url`, i.e. `https://pulse.axiolo.com`), NOT under `/api/`: `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/api/mcp`, `/authorize`, `/authorize/consent`, `/token`, `/register`, `/revoke`. nginx proxies these to the backend via `^~`/`=` `location` blocks in Pulse's own site file (they outrank the `~ /\.` dotfile-deny so `.well-known` isn't blocked).
- **Token model**: opaque, SHA-256-hashed + 8-char prefix like API keys (instant revocation via `revoked_at`), bound to `(user, org, client)` and audience-bound to `mcp_resource_url`. Access 1h, refresh 30d (both rotated on refresh). Org membership is re-checked on every call — a removed member loses access immediately, not at token expiry.
- **Scope**: a single `mcp` scope grants the same admin tool surface an API key does, scoped to the chosen org. Tables `oauth_clients` / `oauth_authorization_codes` / `oauth_grants` (migration 0008, app-layer gated like `api_keys`, no RLS).
- Code: `api/pulse_api/mcp/oauth/` (`provider.py`, `verifier.py`, `tokens.py`, `routes.py`) + the RS wiring in `mcp/server.py`. Source-of-truth plan: `~/.claude/plans/bright-purring-nygaard.md`.

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