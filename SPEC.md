# Pulse Project Specification

**Repository:** Axiolo-Marketing/pulse (private)
**Product owner:** Tom DiGati, Client Transformation Lead, Axiolo
**Specification version:** 3.0 (multi-tenant SaaS)
**Last updated:** June 2026

> **v3.0 changes.** Pulse used to be a single-tenant tool for Axiolo. As of the
> multi-tenant migration (PRs #3–#8), it runs as an invite-only SaaS: any number
> of consulting orgs can sign in, each with their own clients, cards, uploads,
> API keys, members, invites, and audit log. The role-flip / RLS pattern remains
> the multi-tenant backstop (see §6 and `CLAUDE.md`). Axiolo is now just the
> first org. See `~/.claude/plans/let-s-plan-on-makding-mossy-orbit.md` for the
> migration's design notes.

---

## How to Use This Document

This is the product specification for Pulse. It describes how Pulse works, what it offers, and how Tom uses it to onboard a client. It is **not** about any specific engagement — it should apply equally to engagement #1 and engagement #50.

Each individual engagement (Renee Mueller, Josh Rosen, future clients) has its own brief stored in the database, edited from `/admin/`. That's where client-specific behavioral notes, the deck sketch, the live URL, the operations log, and the handoff checklist live. The admin pre-fills new briefs with a structured template so the shape is right from the first keystroke.

For the operational runbook — "how to onboard a new client end-to-end" — see §11.5.

---

## 1. Product Overview

### What Pulse Is

Pulse is a mobile-first decision and validation tool. A consultant (the operator) sends a single secure link to a client. The client opens the link on any device, taps through a sequence of pre-populated decision cards, confirms or corrects what we already know, and uploads documents where needed. Progress saves automatically. The client can stop and resume at any time from any device with the same link.

The operator sees responses in real time as they arrive, edits card text directly when wording needs to change, and exports responses to whatever project management or operations system the engagement uses (in v1, that is ClickUp).

### Why Pulse Exists

Traditional client onboarding asks the client to produce documents, fill out forms, and attend meetings. That model fails for time-starved founders and operators who cannot dedicate focused blocks of time to anything beyond their core work. Pulse inverts the model. The consultant produces the document, the client validates it. The cognitive load shifts from production to confirmation. A 90-minute meeting becomes a 5-minute mobile review.

### Brand Identity

Pulse is an Axiolo product. Tom DiGati owns the codebase. The default chrome and
client-facing deck are Axiolo-branded — Axiolo is the first tenant on the SaaS
and the canonical reference for "what Pulse looks like."

Per-tenant branding is partial in v3.0. Each org uploads its own **logo** which
appears in the admin chrome and on the client-facing deck header next to the
Axiolo wordmark. Display name is editable; slug is immutable. No per-tenant
color overrides, no custom domains, no rebrand — those are deliberate
out-of-scope items for this iteration.

**Visual identity:**
- Font: Poppins (Google Fonts), max weight SemiBold (600). Never heavier.
- Primary green: `#07926B`
- Green dark: `#065A47`
- Green light: `#ECF6F3`
- Charcoal: `#3B373B`
- Light gray: `#F7F7F7`
- Border: `#C8CDCC`
- White: `#FFFFFF`
- Muted text: `#777777`
- Amber for warnings: `#E07B00`

**Voice and tone:**
- Direct but warm
- No em-dashes anywhere (use commas, parentheses, or sentence breaks)
- "you" and "your" in user-facing copy
- Avoid jargon, acronyms, and consultant-speak
- Avoid "audit," "accountability," "compliance" — these feel transactional and pressuring

---

## 2. The Client (any engagement)

Pulse is built for time-starved decision-makers — founders, principals, executives — who cannot give a long synchronous session but can validate a small focused chunk on their phone between other commitments.

### What every client gets

- One private URL (e.g. `https://<pulse-domain>/?t=<16-hex-token>`)
- No password, no account, no login screen. The URL itself is identity.
- A deck of cards that's been pre-built from existing context (transcripts, prior work, business plan, the operator's notes)
- Auto-save on every action so they can step away and come back
- Cross-device resume — same URL on phone, laptop, or tablet picks up where they left off
- A "Welcome back. Picking up where you left off." banner on return visits

### Profile per engagement

Every engagement starts with a one-page profile capturing how *this* client moves so the deck can be tuned to them. Fields include:
- Role and org context
- Behavioral profile (mobile-first? voice-first? prefers numbers or prose? quirks like dyscalculia, ESL, dyslexia, time zones, etc.)
- A representative quote so anyone reading can hear their voice
- What this means for their deck (card order, tone, response types to favor)

The brief lives in `clients.brief` and is edited from `/admin/`. New briefs open with a structured template pre-filled so the shape is right from the first keystroke. Click **Copy as Markdown** to share the brief with the client, the team, or anywhere markdown pastes (email, Slack, ClickUp).

The first deployment is **Renee Mueller** (GLC). The second is **Josh Rosen** (HotSpex). Both briefs are accessible inside the admin at their respective engagement detail pages.

---

## 3. The Operator: Tom DiGati

Tom is the consultant running every engagement. He uses Pulse from a desktop browser (admin is desktop-only by design; the user-facing app is mobile-first). For each engagement he:

- Creates the engagement (admin → "+ New engagement")
- Writes the engagement brief — profile, deck sketch, ops log, handoff (admin → "+ Write brief" or Edit)
- Authors the card deck (admin → per-card "+ Add card", or via direct SQL for bulk)
- Sends the URL to the client via SMS or email
- Watches responses arrive in real time (admin engagement detail)
- Edits card wording when needed (admin → Edit on each card)
- Exports responses to ClickUp (admin → Copy as Markdown, per-card or whole engagement)
- Shares the brief with the client or team (admin → Copy brief as Markdown)

Tom is the only operator. There is no multi-user admin in v1. The admin is gated by a single shared password.

---

## 4. The Card Model

Every interaction in Pulse is a card. A card is a single unit of decision or input. The client sees one card at a time. Each card has:

- **Title**, short, plain language
- **Context**, what we already know (2 to 4 sentences). Should read in 15 seconds.
- **Question**, the specific decision or input we need. One per card. Never compound.
- **Response type**, one of: `confirm-edit`, `single-select`, `multi-select`, `short-text`, `long-text`, `file-upload`, `document-link`, `contact-share`
- **Options** (for select types), jsonb array of strings
- **Default value**, the existing text shown for confirm-edit cards (nullable)
- **Skip allowed**, boolean
- **Category**, for grouping in admin (e.g. Client Review, Decisions, Document Requests — pick whatever taxonomy fits the engagement)
- **Attachment path** (optional), relative path to an HTML reference file. If set, the card renders a "View Active Reference" button that opens the file in a sandboxed iframe modal.

### Response types

| Type | Behavior | response_value shape |
|---|---|---|
| `confirm-edit` | Yes / Needs edit / Skip. Edit opens a textarea pre-filled with the prior correction or `default_value`. | `{confirmed: true}` or `{confirmed: false, correction}` |
| `single-select` | 2–5 mutually exclusive options. Auto-saves on tap and advances. | `{selected, note?}` |
| `multi-select` | 2–9 options. Toggle any number, then Continue. | `{selected: [...], note?}` |
| `short-text` | Single-line input. | `{text}` |
| `long-text` | Multi-line textarea. Voice-friendly placeholder. | `{text}` |
| `document-link` | URL input (validated http/https). | `{url, note?}` |
| `contact-share` | Three fields: name, email, role. Email required. | `{name, email, role, note?}` |
| `file-upload` | Drop or pick up to 5 files, max 25MB each. | `{file_ids: [...], note?}` |

### Notes on every card

Every card type **except** `confirm-edit`, `short-text`, and `long-text` shows an optional "Notes (optional)" textarea below the primary input. Voice-friendly placeholder ("Add a note. Tap the keyboard mic to talk."). Note is folded into `response_value.note` on save. confirm-edit already has the Needs edit textarea; short-text and long-text already are open-text inputs that use the same placeholder.

### Card state

Per-client, per-card:
- `not_started` — no row exists in `responses`
- `viewed` — client opened the card but didn't submit. Inserted automatically on first render.
- `answered` — client submitted a response
- `skipped` — client explicitly skipped
- `needs_edit` — reserved (not currently emitted; the edit-then-submit flow goes straight to `answered`)

---

## 5. Authoring Cards for an Engagement

Cards live in the database (`public.cards`), one row per card per client. Two ways to author:

### Via admin (recommended for new engagements)

1. Sign in to `/admin/`
2. Click into the engagement (or create a new one with "+ New engagement")
3. Click "+ Add card" at the bottom of the cards list
4. Fill title, category, response type (dropdown), context, question, options (if select), default value (if confirm-edit), attachment path (optional), skip allowed
5. Save → appended with `order_index = max + 1`
6. Repeat per card

The admin handles all the schema details. `response_type` is fixed at creation (cannot be changed via Edit) so existing responses don't end up with the wrong shape.

### Via direct SQL (for bulk authoring)

For a long deck, hand-writing the inserts is faster than clicking through the admin. Pattern:

```sql
insert into public.cards (
  client_id, order_index, category, title, context, question,
  response_type, options, default_value, skip_allowed, attachment_path
) values (...)
on conflict (client_id, order_index) do update set
  category = excluded.category,
  title = excluded.title,
  context = excluded.context,
  question = excluded.question,
  response_type = excluded.response_type,
  options = excluded.options,
  default_value = excluded.default_value,
  skip_allowed = excluded.skip_allowed,
  attachment_path = excluded.attachment_path;
```

The `on conflict` clause means re-running the same SQL updates the cards in place. Useful for iterating on wording.

### Active References (HTML deliverables)

If a card benefits from a visual reference (org chart, ICP one-pager, sales playbook excerpt), drop the HTML file at `pulse/public/deliverables/<slug>.html`, commit it, push it, then wire it onto the card via the admin Edit form (Active reference path = `deliverables/<slug>.html`).

The file deploys with the next push to `main`. The card's "View Active Reference" button opens it in a sandboxed iframe modal (`sandbox="allow-scripts"`).

Match the Axiolo brand if you can — Plus Jakarta Sans, the blue palette (`#2960F6` primary on `#0A0F2E` navy), the radius/shadow tokens from `src/styles/pulse.css` — so the modal feels continuous with the card. See `public/deliverables/glc-org-chart.html` for a reference implementation.

---

## 6. Database Schema (self-hosted Postgres)

Pulse moved off Supabase to a self-hosted Postgres + FastAPI stack during the
v2→v3 migration. The schema is owned by Alembic migrations in
`api/migrations/versions/` (0001 — initial port; 0003 — API keys; 0004 —
multi-tenant tables, RLS, `pulse_member` role; 0005 — drops `users.is_admin`,
NOT NULLs the org_id columns; 0006 — `organization_invites.revoked_at`).

### Tables — tenant data

Every tenant-owned table carries an `org_id uuid not null references organizations(id)`
foreign key. RLS scopes reads/writes by `pulse.org_id` GUC set per request.

**organizations** — the tenant
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| name | text not null | Display name, owner-editable |
| slug | text not null unique | URL-safe, immutable |
| logo_path | text | Relative to `upload_dir`, set via logo upload endpoint |
| created_at, updated_at | timestamptz | |

**organization_memberships** — who can act in which org, at what role
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK → organizations | on delete cascade |
| user_id | uuid FK → users | on delete cascade |
| role | text not null check | `'owner'` or `'member'` |
| created_at | timestamptz | |
| **unique** | (org_id, user_id) | |

**organization_invites** — pending invitations
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK → organizations | |
| email | text not null | lowercased on insert |
| role | text not null check | `'owner'` or `'member'` |
| token_hash | text not null unique | SHA-256 of the raw signed token (raw never persisted) |
| expires_at | timestamptz not null | default `now() + interval '7 days'` |
| accepted_at | timestamptz | set on successful redemption |
| revoked_at | timestamptz | set on explicit revoke; distinguishes revoked from accepted |
| created_by | uuid FK → users | nullable; set null on user delete |
| created_at | timestamptz | |

**audit_logs** — every mutation by every operator
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK → organizations | on delete cascade |
| user_id | uuid FK → users | nullable; set null on user delete |
| action | text not null | stable enum (see §12.3) |
| target_type | text | `'client'`, `'card'`, `'member'`, `'invite'`, `'org'`, `'api_key'`, `'attachment'` |
| target_id | uuid | |
| metadata | jsonb | small payload describing the change (≤ 2 KB) |
| created_at | timestamptz | |

**clients** — engagements (now tenant-scoped)
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| org_id | uuid FK → organizations | **NOT NULL** — every client belongs to one org |
| name, engagement_name | text | |
| token | text not null unique | 16-hex-char magic-link credential |
| brief | text | markdown |
| created_at, last_active_at | timestamptz | |

**cards / responses / uploads** — unchanged shape from v2 except each now
carries `org_id uuid` (nullable as of PR 1, NOT NULL after PR 2's migration 0005).
`responses.org_id` and `uploads.org_id` have a `default` that reads from the
`pulse.org_id` GUC, so client-facing INSERTs auto-populate from the request
middleware without needing the route handler to pass it.

### Tables — operator identity (global, not tenant-scoped)

**users**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| email | text not null unique | |
| password_hash | text | argon2id; nullable for OAuth-only users |
| name | text | |
| is_superadmin | bool not null default false | cross-tenant tier — see §12.4 |
| email_verified_at | timestamptz | |
| last_active_org_id | uuid FK → organizations | last context the user was in; default for new sessions |
| created_at, last_login_at | timestamptz | |

`users.is_admin` from v2 is **gone**. Admin powers come from
`organization_memberships.role = 'owner'`. Superadmin (cross-tenant) comes from
`users.is_superadmin`.

**oauth_identities** — unchanged from v2. Google and Microsoft.

**api_keys**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK → users | |
| org_id | uuid FK → organizations | **per-(user, org)** — a multi-org owner mints separate keys per org |
| label, prefix, key_hash | text | `pulse_<32-hex>` format; SHA-256 hash + 8-char prefix for lookup |
| last_used_at, created_at | timestamptz | |
| revoked_at | timestamptz | |

### Roles + RLS — the multi-tenant backstop

Four Postgres roles defined in `db-init/01-pulse-roles.sql` + migration 0004:

| Role | BYPASSRLS | Used by |
|---|---|---|
| `pulse_owner` | yes (by ownership) | Alembic migrations |
| `pulse_anon` | no | client-facing endpoints (`get_anon_session`); RLS scopes by `pulse.token` and `pulse.org_id` GUCs |
| `pulse_member` | no | operator endpoints (`get_org_scoped_session`); RLS scopes by `pulse.org_id` GUC |
| `pulse_admin` | yes | `/api/superadmin/*`, OAuth/invite resolution that must cross orgs, migrations |

Two GUCs drive RLS:
- `pulse.token` — the 16-hex client token; set on every `pulse_anon` request
- `pulse.org_id` — the active org's UUID; set on every `pulse_anon` AND `pulse_member` request

Helper functions:

```sql
public.pulse_request_token()      -- nullif(current_setting('pulse.token',  true), '')
public.pulse_request_client_id()  -- look up client.id from the token
public.pulse_request_org_id()     -- nullif(current_setting('pulse.org_id', true), '')::uuid
```

Note: `SET LOCAL pulse.org_id = $1` does NOT accept bound params (Postgres `SET`
limitation). Use `select set_config('pulse.org_id', :id, true)` instead. Same
bug nearly shipped to v2 for `pulse.token` — call sites enforce the pattern.

Policies are role-keyed (one set for `pulse_anon`, one for `pulse_member`):

- `pulse_anon`: client-facing — `clients_self_read`, `clients_self_touch`,
  `cards_self_read`, `responses_self_*`, `uploads_self_*` (unchanged from v2,
  scoped by `pulse_request_client_id()`).
- `pulse_member`: operator — `*_org_scope` policies on every tenant table,
  scoped by `pulse_request_org_id()`. Tables covered: `clients`, `cards`,
  `responses`, `uploads`, `api_keys`, `audit_logs`, `organization_memberships`,
  `organization_invites`, `organizations`.

### Storage

File uploads land on local disk under `settings.upload_dir`
(`/var/lib/pulse/uploads/` in prod) — Supabase Storage is gone. Path convention
`{client_id}/{card_id}/{uuid}-{filename}` for engagement uploads, and
`org-logos/{org_id}/{uuid}.{ext}` for org logos. `resolve_within_upload_dir()`
is the traversal defense — every disk read/write goes through it.

---

## 7. Frontend Specification

### Architecture

- Astro 5 static site, code-split per page.
- Two pages: `src/pages/index.astro` (user-facing) and `src/pages/admin.astro` (operator).
- Each page bundles its own JS chunk. The user-facing chunk contains the **anon key only**. The admin chunk contains the **service role key**. Verified against the production build.
- Vanilla TypeScript + DOM-string templates. No framework runtime.
- All state lives in Supabase. No localStorage for app state. SessionStorage is used only for the admin login flag.

### URL Pattern

```
https://<pulse-domain>/?t=<16-hex-char-token>
```

Pulse now serves from the domain root behind nginx (see `deploy/roles/nginx-site/`). `astro.config.mjs` no longer sets `site` or `base` — set `site` once the production domain is final so Astro can build absolute URLs into sitemap.xml / Open Graph tags.

### Bootstrap Flow

1. Page loads, runs `src/scripts/app.ts`.
2. Script reads `?t=` from URL.
3. Builds a Supabase client with anon key + `x-pulse-token` header.
4. Fetches client row (RLS gates it), then in parallel: cards, responses, uploads.
5. Computes `bootIndex = firstUnansweredIndex(cards, responses)` — first card where state is not `answered` or `skipped`.
6. Renders that card. If `bootIndex > 0`, renders the resume banner.

### Card UI

```
+------------------------------------------+
|  Axiolo · Pulse   [‹]  4 of 19 ▾  [›]    |
|------------------------------------------|
|  [optional resume banner]                |
|                                          |
|  CATEGORY                                |
|  Card Title                              |
|  ─────────────────────────────────────── |
|  Context paragraph.                      |
|                                          |
|  [↗ View Active Reference] (optional)    |
|                                          |
|  Question text?                          |
|                                          |
|  [Response interface]                    |
|                                          |
|  Notes (optional, for some types)        |
|  [textarea]                              |
|                                          |
|  [   Continue / Confirm   ]              |
|  [   Skip for now         ]              |
+------------------------------------------+
```

### Navigation Controls

The topbar shows three controls grouped on the right:
- **Back arrow** (`‹`) — previous card. Disabled at index 0.
- **Progress button** (`4 of 19 ▾`) — opens the slide picker.
- **Forward arrow** (`›`) — next card. Disabled at the last card.

**Slide picker** is a modal listing all cards with their state (Answered, Skipped, Viewed, Not viewed). Tap a row to jump.

### Pre-fill on Revisit

Navigating back to an already-answered card pre-loads the form so the client can refine instead of restart:
- Multi-select / single-select chips: prior selections highlighted
- Text / link / contact inputs: pre-filled from `response_value`
- confirm-edit's edit textarea: pre-filled with the prior correction

A small green "prior-hint" strip explains the loaded state. Re-submission upserts the same row.

### Active Reference Modal

If a card has `attachment_path`, the card renders a "View Active Reference" button. Tap → modal opens with a sandboxed iframe. Sandbox is `allow-scripts` (no same-origin) so deliverables can be interactive without escaping. Modal closes on X, backdrop click, or Esc.

### Save and Resume

- Every response saves on submit. Upsert on `(card_id, client_id)`. `viewed_at` survives the upsert.
- After save, `last_active_at` is touched on the client row (best-effort).
- On boot, `firstUnansweredIndex` returns the client to where they left.
- Resume banner ("Welcome back. Picking up where you left off.") shows whenever `bootIndex > 0`. Dismisses on first save or advance.

### File Upload UX

- Tap the dropzone → native iOS picker
- File streams to Supabase Storage with a pending chip
- After success, an `uploads` row inserts and the file appears as a chip with name, size, X to remove
- Up to 5 files per card; 25MB per file; both limits surface in the dropzone label
- Continue enables when there's at least one file OR a non-empty note

### Empty States and Errors

- Missing token: "This link is missing a code. Please check the link your consultant sent you."
- Invalid token: "We could not find your engagement. Please check the link or contact Tom."
- Network failure on save: amber banner "Could not save just now. We will retry automatically." Retries every 10 seconds; manual Retry triggers immediately.

### Polish

- Tap targets ≥44px (Apple HIG)
- Safe-area insets so iPhone notch / home indicator don't crowd content
- `min-height: 100dvh` for iOS Safari's dynamic viewport
- Card / error / banner enter with a 0.3s fade + 6px translateY; honors `prefers-reduced-motion`
- `:focus-visible` outlines on every interactive element

---

## 8. Tom's Admin View

Lives at `/admin/`. Desktop only. Password-gated. After login:

### Engagement List

Header with "+ New engagement" button. Table with one row per client:

| Client | Engagement | Progress | Last active | Actions |
|---|---|---|---|---|
| Client name<br>Org | Engagement name | answered+skipped / total | Local timezone, relative under 24h | View · Copy link · Delete |

**+ New engagement** opens a modal: client name (required), org (optional), engagement name (optional). Submit inserts the row, generates a fresh 16-hex-char token, navigates to the new client's detail view.

**Copy link** writes the production URL with the current token.

### Engagement Detail

- Back link to engagement list
- Client name + org/engagement context
- "Copy all as Markdown" + "Copy link" buttons
- All cards in `order_index` order; per-card:
  - Card number and category
  - Title
  - State badge (Answered green / Skipped amber / Viewed gray / Not viewed gray)
  - Formatted response body
  - Suggested ClickUp status dropdown (override-able)
  - Timestamp ("Answered 5 minutes ago" or "Viewed 2 hours ago")
  - **Edit** button + **Delete** button + **Copy** button
- "+ Add card" trigger at the bottom

### Card Editing

Tap **Edit** on any row → swaps the read-only header for an inline form:
- Title
- Category
- Context (textarea)
- Question (textarea)
- Options (one per line, only for select types)
- Active reference path (optional)
- Skip allowed toggle

Save PATCHes the `cards` row, updates local state, re-renders the article. Cancel reverts.

`response_type` is intentionally **not** in the Edit form — it's set on Add only. Changing type after responses exist would invalidate them.

### Card Add / Delete

**+ Add card** opens an inline form with the same fields as Edit, plus a `response_type` dropdown. Saves with `order_index = max + 1`.

**Delete** prompts for confirmation listing what cascades (responses, uploads, storage objects). FK cascade handles responses/uploads; storage objects are best-effort removed.

### ClickUp Markdown Export

Format per §14.3. Two paths:
- **Per-card Copy** button — single card's block.
- **Copy all as Markdown** — concatenates all blocks separated by `---`.

File-upload responses include 7-day signed URLs.

---

## 9. Tech Stack

| | |
|---|---|
| Frontend | Astro 5 + vanilla TypeScript (no React, no Tailwind) |
| Backend | FastAPI (Python 3.13), SQLModel ORM, asyncpg, Alembic |
| Database | Self-hosted Postgres 16 |
| Storage | Local disk under `settings.upload_dir`; per-tenant subdirs |
| MCP | FastMCP, mounted at `/api/mcp/`, Bearer-key authed |
| Dev orchestration | Docker Compose; `make dev` / `make test` / `make migrate` / `make seed-dev` |
| Production hosting | Shared Debian VPS; nginx in front of `pulse-api.service` |
| Production builds | `git pull` + `uv sync` + `npm ci && npm run build` on the VPS via Ansible |
| Domain | `pulse.axiolo.com` (Axiolo tenant); single-domain w/ in-app org switcher for v3 (no per-tenant subdomains yet) |

### Dependencies (backend, see `api/pyproject.toml`)

- `fastapi`, `uvicorn`, `sqlmodel`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`
- `pydantic-settings`, `python-multipart`, `itsdangerous`, `argon2-cffi`
- `httpx` (OAuth + tests), `slowapi` (rate limiting), `cryptography` (Fernet)
- `fastmcp` (MCP server)
- Test: `pytest`, `pytest-asyncio`, `respx`, `freezegun`

### Dependencies (frontend, see `package.json`)

- `astro` — only build-time + dev server
- No runtime UI framework; no UI component library; vanilla CSS with brand tokens in `src/styles/pulse.css`

### Environment Variables (top-level — see `.env.example` for the full list)

Database URLs (one per role; all default to `database_url` if unset):
- `DATABASE_URL` (`pulse_owner` — migrations)
- `ANON_DATABASE_URL` (`pulse_anon`)
- `MEMBER_DATABASE_URL` (`pulse_member`)
- `ADMIN_DATABASE_URL` (`pulse_admin`)
- `TEST_DATABASE_URL`, `TEST_ANON_DATABASE_URL`, `TEST_MEMBER_DATABASE_URL`, `TEST_ADMIN_DATABASE_URL`

Auth + crypto:
- `SESSION_SECRET` — signs `itsdangerous` session + token cookies
- `SUPERADMIN_EMAILS` — whitespace/comma-separated emails granted `is_superadmin = true` at migrate-time and on `make seed-dev`
- `SIGNUP_ENABLED` — defaults `false`; production is invite-only

OAuth:
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`
- `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID`, `MICROSOFT_REDIRECT_URI`

Email (Resend):
- `RESEND_API_KEY` — Resend API key; empty keeps `send_email` in log-only mode (no mail sent)
- `EMAIL_FROM` — from-address on a Resend-verified domain, e.g. `Pulse <pulse@notifications.axiolo.com>`

Misc:
- `UPLOAD_DIR` (`/var/lib/pulse/uploads/` in prod)
- `FRONTEND_BASE_URL` (used in outbound email links)
- `CORS_ALLOWED_ORIGIN`
- `ENVIRONMENT` (`development` / `production`; gates `Secure` cookie flag)

### What NOT to Use

- No Supabase. No GitHub Pages. No client-side service-role keys.
- No third-party UI framework / component library.
- No `window.alert/confirm/prompt` — inline confirms + `toast()` per the frontend conventions.
- No third-party auth provider for the operator surface (own session + invite system).

---

## 10. Deployment

### Production runbook

See `deploy/README.md` for the full Ansible playbook walkthrough. The headline:

- Production runs on a **shared Debian VPS** that hosts other Axiolo apps. The
  Ansible roles are scoped to Pulse's own paths and never modify shared system
  state. Pre-flight always runs `--check --diff` before applying.
- The VPS clones the repo at `/opt/pulse/source` via the operator's
  `~/.ssh/github_deploy_key` and builds locally (`uv sync` + `npm ci && npm run build`).
- Migration order on a fresh deploy: ensure `pulse_owner`, `pulse_anon`,
  `pulse_member`, `pulse_admin` roles exist (from `db-init/01-pulse-roles.sql`),
  set `SUPERADMIN_EMAILS` BEFORE running `alembic upgrade head` (the 0004
  migration reads it at execution time to promote the named users), then
  `alembic upgrade head`.

### First superadmin

`make seed-dev` is for the dev DB. In prod, a user with an email in
`SUPERADMIN_EMAILS` gets `is_superadmin = true` set by migration 0004's data
migration. If you forgot to set the env var before migrating, a one-shot
`UPDATE users SET is_superadmin = true WHERE lower(email) = ...` fixes it.

### Iteration loop

1. Edit code locally
2. Branch → commit → `git push`
3. Open PR via `gh pr create`
4. Merge → operator runs `ansible-playbook deploy.yml` on the VPS

### Adding a New HTML Deliverable

Unchanged from v2 — drop the file at `public/deliverables/<slug>.html`, commit,
wire it via the admin Edit form (Active reference path = `deliverables/<slug>.html`).

---

## 11. Implementation History (shipped)

| # | What | PR |
|---|---|---|
| M1–M4 | Project scaffold + RLS + all response types + attachments | #1 |
| M5–M6 | Save/resume + brand polish + auto-retry | #1 |
| M7 | `/admin` with password gate + ClickUp export | #1 |
| M8 | GitHub Actions deploy + first production deploy | #1 |
| Post-M8 | Back/forward navigation + slide picker | #2 |
| Post-M8 | Notes textarea on every card; Card 13 wording | #3 |
| Post-M8 | Drop redundant notes on open-text cards | #4 |
| Post-M8 | Inline card editing in admin | #5 |
| Post-M8 | 16-char tokens | #6 |
| Post-M8 | New engagement modal + add/delete cards in admin | #9 |

---

## 11.5 Onboarding a New Client (runbook)

The whole flow happens in `/admin/` — no terminal, no git, no files.

**Create the engagement:**

1. Open `/admin/`, sign in, click **+ New engagement**.
2. Fill name, org, engagement name. Submit. The admin lands you on the empty detail view with a fresh token.

**Write the brief:**

3. Click **+ Write brief** at the top. A markdown editor opens, pre-filled with a structured template (profile, deck sketch, source material, ops log, handoff checklist).
4. Fill in what you know about the client and what this engagement is trying to validate. Save.
5. The brief is now editable inline at any time. Use **Copy as Markdown** to paste it into an email, Slack, ClickUp, or anywhere you want to share context with the client or the team.

**Author the cards:**

6. Scroll to **+ Add card** at the bottom and add each card you want. Title, category, response type (dropdown), context, question, options (if select), default value (if confirm-edit), attachment path (if you have an HTML reference), skip allowed.
7. Save. The card appends to the deck.
8. Edit and Delete are available on every row.

**(Optional) Wire an active reference:**

9. If a card benefits from a visual reference (org chart, ICP one-pager, sales playbook excerpt), drop the HTML file at `pulse/public/deliverables/<slug>.html` (or have a dev do it for you) and reference it via the Edit form's "Active reference path" field, e.g. `deliverables/glc-org-chart.html`.

**Send to the client:**

10. Click **Copy link** on the engagement detail or in the engagement list.
11. SMS or email the URL. The link works on any device. No password, no account.

**Operate the engagement:**

12. Watch responses arrive in the admin (refresh to see new state).
13. Edit any card wording inline if you want to refine. Existing responses stay valid because they're tied to `card_id`, not the wording.
14. Update the brief's operations log as the engagement progresses.
15. When the client is done, **Copy all as Markdown** and paste the responses into ClickUp.

**Hygiene:**

- If the link leaks, delete the engagement to invalidate it (the token can no longer be rotated in place).
- Don't share the admin URL or password with the client.
- The brief is the single source of truth for engagement narrative — you don't need to keep notes anywhere else.

---

## 12. Success Criteria for Any Engagement

An engagement is successful if:

1. The client opens the link on their phone in under 5 seconds.
2. They tap through at least half the deck in their first session.
3. They return at least once and resume cleanly.
4. They submit at least one substantive response (not just skips or confirms — an edit, a note, a file).
5. Tom can see responses in the admin in real time and export them to ClickUp.
6. The client gives positive or neutral feedback. (Negative is informative.)
7. The product feels like an Axiolo product, not a generic survey tool.

Each engagement file should track which of these were true at handoff.

---

## 13. Future Enhancements (Out of Scope for v1)

Not in v1, noted for context:
- Move admin queries behind a Supabase edge function so the service role key isn't in the bundle (current security caveat — single operator, accept for now)
- Custom domain `pulse.igtms.com` (DNS not configured)
- Per-client password gate (currently URL token is the credential)
- Multi-stakeholder per engagement (one URL, multiple respondents merged)
- Conditional card logic (if X then Y)
- Drag-to-reorder cards in admin
- Analytics on engagement completion
- White-labeling for other consultants beyond Axiolo

---

## 14. Configuration Decisions (Locked)

### 14.1 Production URL

Not yet finalized. `pulse.axiolo.com` is under consideration. The Ansible
deploy reads `pulse_domain` from `deploy/group_vars/all.yml` — change there
once decided, then re-run `make deploy-apply`.

### 14.2 Admin Authentication

> **STALE — see CLAUDE.md "Auth subsystems" for the current model.**
> The old SHA-256-password-hash-in-bundle is gone; the admin now uses
> email + password (argon2) or Google / Microsoft 365 OAuth, with signed
> session cookies. The text below is preserved as historical reference
> for the v1 architecture.

Single shared password. Hash stored as `PUBLIC_ADMIN_PASSWORD_HASH` (SHA-256 hex), inlined into the admin chunk at build. Login flag in `sessionStorage`; tab close ends the session.

### 14.3 ClickUp Export Format

```
# {Card Title}

**Status:** {recommended_status}

## Response from {Client Name}
{response body, with Note: suffix if a note was provided}

## Original Context
{the card's context}

## Original Question
{the card's question}

---
```

One block per card, separated by `---` rules.

**Recommended status** auto-suggested per the table below. Override per-card via dropdown before copying.

| Response pattern | Status |
|---|---|
| Confirmed without edit | Axiolo Review |
| Edited the existing content | Axiolo Review |
| Uploaded a file | Axiolo Review |
| Skipped a card | Waiting on Good Life |
| Said they need help (regex on "need help") | Needs Attention |
| Indicated a blocker (regex on "blocked", "stuck", "waiting on") | Blocked |
| Card not yet viewed | Waiting on Good Life |
| Selected "Done" or "Approved" or "Complete" | Approved |
| Empty file-upload, empty multi-select | Waiting on Good Life |

Valid status values:
`Waiting on Axiolo`, `Waiting on Good Life`, `Needs Attention`, `Axiolo Review`, `Client Review`, `Blocked`, `Approved`, `Complete`.

> The "Waiting on Good Life" label is a holdover from the first engagement. For non-GLC engagements, the operator can override per-card. Future enhancement: per-engagement default-status labels.

### 14.4 Timestamps

Display in operator's local timezone via `Intl.DateTimeFormat`. Relative under 24h ("2 hours ago"); absolute with timezone for older ("April 23, 2026 at 4:15 PM CDT"). Stored UTC; conversion at display.

### 14.5 Production Deployment SLA

No formal SLA. Tom owns the entire stack. Deploys when satisfied.

### 14.6 Hosting and Infrastructure

> **STALE.** Pulse has migrated off Supabase + GitHub Pages. The current
> stack is FastAPI + self-hosted Postgres + RLS, deployed via Ansible to a
> shared Debian VPS. See `deploy/README.md` for the runbook and CLAUDE.md
> "Architecture" for the design.

- Repo: Axiolo internal (final hosting location pending)
- Domain: pending final selection (pulse.axiolo.com under consideration)

### 14.7 Token Format

16 hex chars, generated server-side at engagement creation via `secrets.token_hex(8)` (Python). 64 bits of entropy.

### 14.8 Bundle Isolation

Astro splits per page, but **no service role key is shipped to the browser
anymore** — that was a v2 Supabase caveat. The FastAPI backend authenticates
every admin call via a signed-cookie session and runs queries on the
`pulse_member` Postgres role; the browser bundle holds no credentials beyond
the operator's session cookie and the engagement token from the URL.

### 14.9 HMR in Dev

Both `app.ts` and `admin.ts` call `import.meta.hot.decline()` so any code change forces a full page reload in dev. Prevents stale module instances accumulating button listeners.

---

## 12. Multi-tenant model (v3)

Added in the v2→v3 migration. Each consulting org runs Pulse as if it were
single-tenant — they only see their own clients, cards, uploads, members,
invites, API keys, and audit log. The database enforces this via RLS on the
`pulse_member` role; the application layer reinforces it via the `org_id`
filter passed implicitly through the `pulse.org_id` GUC.

### 12.1 Roles within an org

| Role | Powers |
|---|---|
| `owner` | Everything operational, plus: edit org name, upload/delete logo, invite + remove members, change member roles, revoke API keys. Cannot demote/remove the last owner (UI hides controls; backend enforces via `FOR UPDATE` lock + count check). |
| `member` | All operational endpoints (engagements, cards, responses, uploads, API keys, activity feed read). Read-only on org settings + members list. |

Multi-org membership is allowed for owners only (a consultant can serve
multiple client-orgs); a member belongs to exactly one org. Enforced at the
invite + signup paths, not by a hard schema constraint, so an existing
single-org member could be invited into a second org as owner.

### 12.2 Invite flow (signed-link, 7-day expiry)

1. Owner submits `{email, role}` to `POST /api/orgs/me/invites`. A new
   `organization_invites` row is inserted with `token_hash` (SHA-256 of the
   signed token; raw token never persisted).
2. Backend signs a token via `itsdangerous.URLSafeTimedSerializer` with salt
   `pulse-org-invite` and emails a link `{FRONTEND_BASE_URL}/invite?token=…`.
3. Recipient clicks → public `GET /api/invites/{token}` returns the org name,
   role, and lifecycle status (`pending` | `expired` | `accepted` | `revoked`).
4. Acceptance: `POST /api/invites/{token}/accept` with either
   `{auth: "password", password, name?}` or `{auth: "google"|"microsoft"}`.
   The password path creates/links the user inside one transaction and
   atomically claims the invite (`accept_atomically` returns false if another
   tab already won). The OAuth path returns a `redirect_url` that re-enters
   the OAuth flow with the invite token stashed in the state cookie; the
   callback resolves and accepts.
5. Revocation: `DELETE /api/orgs/me/invites/{id}` stamps `revoked_at` (separate
   from `accepted_at`, so revoked invites surface as `status: "revoked"` and
   not the misleading `"accepted"`).

Existing-user OAuth sign-ins also accept any pending invite for their email —
without this, an invitee with a prior Pulse account would land with no
membership and 403 on every admin call.

### 12.3 Audit log

Every mutating operator route writes an audit row in the same transaction as
the user action (atomic — a failed action rolls back the audit too). The
21-action enum is defined in `api/pulse_api/audit.py`:

| Domain | Actions |
|---|---|
| Engagement | `engagement.create`, `engagement.update`, `engagement.delete`, `engagement.reset` |
| Card | `card.create`, `card.update`, `card.delete`, `card.import` |
| Attachment | `attachment.upload` |
| Org | `org.create`, `org.update`, `org.delete`, `org.logo_set`, `org.logo_remove` |
| Member | `member.invite`, `member.invite_revoke`, `member.role_change`, `member.remove`, `member.join` |
| API key | `api_key.create`, `api_key.revoke` |

`metadata` is a small JSON payload tailored per action (e.g.,
`{from: "member", to: "owner"}` for role changes; `{prefix: "pulse_abc1…"}` for
API keys — never the raw secret).

Read surface: `GET /api/orgs/me/activity` (member-readable, no owner gate) with
composite `(created_at, id)` cursor pagination + actor/action filters. The
Activity tab on `/admin/#settings/activity` renders this in reverse-chronological
order with human-readable verbs.

### 12.4 Superadmin tier

`users.is_superadmin = true` unlocks `/api/superadmin/*` (4 routes today) for
cross-tenant operations: list every org, create an org + invite an owner,
delete an empty org, view members of any org. Gated by `get_current_superadmin`,
runs on `pulse_admin` (BYPASSRLS). Frontend mirror at `/admin/#superadmin`,
nav link only renders when `me.is_superadmin`.

Set superadmins via the `SUPERADMIN_EMAILS` env var, consumed by migration 0004
at execution time and by `make seed-dev` for the dev container. Empty in dev =
no superadmins, which is acceptable.

### 12.5 API surface (operator)

All routes JSON in/out. Auth is cookie or `Authorization: Bearer pulse_<key>`
(cookie wins for user identity; Bearer wins for org attribution).

**Personal / multi-org switching** (`get_current_user`):
- `GET /api/me/orgs` — list memberships
- `POST /api/me/switch-org` — set active org

**Org details + branding** (`get_current_org_member`; owner-only writes):
- `GET /api/orgs/me`
- `PATCH /api/orgs/me` (name only; slug immutable)
- `POST /api/orgs/me/logo` — multipart, ≤ 500 KB, png/jpeg/svg/webp
- `DELETE /api/orgs/me/logo`
- `GET /api/orgs/me/logo/{filename}` — served from disk

**Members** (`get_current_org_member`; mutations owner-only):
- `GET /api/orgs/me/members`
- `PATCH /api/orgs/me/members/{user_id}` — `{role}`
- `DELETE /api/orgs/me/members/{user_id}`

**Invites** (owner-only writes):
- `GET /api/orgs/me/invites`
- `POST /api/orgs/me/invites` — `{email, role}`
- `DELETE /api/orgs/me/invites/{id}` — revoke

**Activity** (`get_current_org_member`):
- `GET /api/orgs/me/activity?limit=&cursor=&actor_user_id=&action=`

**Invite acceptance** (public):
- `GET /api/invites/{token}` — resolve
- `POST /api/invites/{token}/accept` — `{auth, ...}`

**Superadmin** (`get_current_superadmin`):
- `GET /api/superadmin/orgs`
- `POST /api/superadmin/orgs` — `{name, slug, owner_email}`
- `DELETE /api/superadmin/orgs/{org_id}` — refuses 409 if org has clients
- `GET /api/superadmin/orgs/{org_id}/members`

Legacy admin surface (`/api/admin/*`) is unchanged in URL but now runs on
`pulse_member` instead of `pulse_admin`, scoped by the active org.

---

## End of Specification

Source of truth for the Pulse product as deployed. Each engagement has its own
brief in the database, edited from `/admin/`. Each tenant org has its own slice
of every table, enforced by RLS.

*Pulse — decisions, not paperwork.*
