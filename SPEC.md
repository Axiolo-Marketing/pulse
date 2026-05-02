# Pulse by IGTMS Project Specification

**Repository:** tomdigati/pulse
**Product owner:** Tom DiGati, Client Transformation Lead, IGTMS
**Specification version:** 2.0 (engagement-agnostic)
**Last updated:** April 2026

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

Pulse is an IGTMS product. Tom DiGati owns the codebase. End users see only IGTMS branding. The product follows the IGTMS brand system throughout.

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

- One private URL (e.g. `https://tomdigati.github.io/pulse/?t=<16-hex-token>`)
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
- Rotates the token if the link leaks (admin → Rotate token)

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

For an example, see how Renee's 19 cards are inserted in `supabase/seed.sql`.

### Active References (HTML deliverables)

If a card benefits from a visual reference (org chart, ICP one-pager, sales playbook excerpt), drop the HTML file at `pulse/public/deliverables/<slug>.html`, commit it, push it, then wire it onto the card via the admin Edit form (Active reference path = `deliverables/<slug>.html`).

The file deploys with the next push to `main`. The card's "View Active Reference" button opens it in a sandboxed iframe modal (`sandbox="allow-scripts"`).

Match the IGTMS brand if you can — Poppins, the green palette, the radius/shadow tokens — so the modal feels continuous with the card. See `public/deliverables/glc-org-chart.html` for a reference implementation.

---

## 6. Database Schema (Supabase)

### Tables

**clients**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | `gen_random_uuid()` |
| name | text not null | Client's full name |
| org_name | text | Organization, optional |
| engagement_name | text | Engagement label, optional |
| token | text not null unique | 16-hex-char random, generated via `encode(gen_random_bytes(8), 'hex')` |
| created_at | timestamptz | default now() |
| last_active_at | timestamptz | nullable, touched on every save |
| brief | text | nullable, markdown engagement narrative edited from /admin/ |

**cards**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| client_id | uuid FK → clients(id) | on delete cascade |
| order_index | integer | unique with client_id |
| category | text not null | |
| title | text not null | |
| context | text not null | |
| question | text not null | |
| response_type | text not null | enum check constraint |
| options | jsonb | nullable |
| default_value | text | nullable |
| skip_allowed | boolean | default true |
| attachment_path | text | nullable, relative path to HTML reference |
| created_at | timestamptz | |

**responses**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| card_id | uuid FK | on delete cascade |
| client_id | uuid FK | on delete cascade |
| state | text not null | enum: not_started, viewed, answered, skipped, needs_edit |
| response_value | jsonb | shape depends on response_type |
| viewed_at | timestamptz | set on first render |
| answered_at | timestamptz | set on submit |
| created_at, updated_at | timestamptz | trigger auto-updates updated_at |
| **unique** | (card_id, client_id) | upsert key |

**uploads**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| card_id, client_id | uuid FK | on delete cascade |
| file_name | text not null | |
| file_size_bytes | integer not null | |
| storage_path | text not null | `{client_id}/{card_id}/{uuid}-{filename}` |
| mime_type | text | |
| uploaded_at | timestamptz | default now() |

### Helper functions

```sql
create or replace function public.pulse_request_token()
returns text language sql stable as $$
  select nullif(coalesce(
    current_setting('request.headers', true)::jsonb->>'x-pulse-token',
    ''
  ), '');
$$;

create or replace function public.pulse_request_client_id()
returns uuid language sql stable as $$
  select id from public.clients
  where token = public.pulse_request_token() limit 1;
$$;
```

### Row Level Security

All four tables have RLS enabled. The user-facing app uses the **anon key** plus an `x-pulse-token` request header. Policies read the header via `pulse_request_token()` and only return rows whose `client_id` matches.

Grants are explicit and column-scoped:

```sql
grant select on public.clients to anon, authenticated;
-- last_active_at is the only column anon can update; the token can never
-- be overwritten via PostgREST even with a valid policy match.
grant update (last_active_at) on public.clients to anon, authenticated;
grant select on public.cards to anon, authenticated;
grant select, insert, update on public.responses to anon, authenticated;
grant select, insert on public.uploads to anon, authenticated;
```

Policies (one per table):
- **clients**: `clients_self_read`, `clients_self_touch`
- **cards**: `cards_self_read`
- **responses**: `responses_self_read`, `responses_self_insert`, `responses_self_update`
- **uploads**: `uploads_self_read`, `uploads_self_insert`

### Storage

Bucket `pulse-uploads`, private. Path convention: `{client_id}/{card_id}/{uuid}-{sanitized-filename}`.

Storage policies on `storage.objects`:
- `pulse_uploads_self_read`: select where `(storage.foldername(name))[1] = pulse_request_client_id()::text`
- `pulse_uploads_self_insert`: same with check

The `x-pulse-token` header propagates from supabase-js into storage's RLS context.

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
https://tomdigati.github.io/pulse/?t=<16-hex-char-token>
```

The Astro config has `base: "/pulse"` and `site: "https://tomdigati.github.io"` to match GitHub Pages.

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
|  IGTMS · Pulse    [‹]  4 of 19 ▾  [›]    |
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
| Client name<br>Org | Engagement name | answered+skipped / total | Local timezone, relative under 24h | View · Copy link · Rotate token |

**+ New engagement** opens a modal: client name (required), org (optional), engagement name (optional). Submit inserts the row, generates a fresh 16-hex-char token, navigates to the new client's detail view.

**Copy link** writes the production URL with the current token.

**Rotate token** generates a fresh 16-hex-char token, PATCHes the row, copies the new URL. The old URL stops working immediately.

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
| Frontend | Astro 5, vanilla TypeScript |
| Database | Supabase Postgres |
| Storage | Supabase Storage (private bucket `pulse-uploads`) |
| Hosting | GitHub Pages |
| CI/CD | GitHub Actions (`actions/deploy-pages@v4`) |
| Build | `npm run build` (Vite under Astro) |
| Domain | `tomdigati.github.io/pulse/` (custom domain deferred) |

### Dependencies

- `@supabase/supabase-js` (browser)
- `pg` (devDep, for `scripts/apply-sql.mjs`)

### Environment Variables

Build-time (inlined into bundles):
- `PUBLIC_SUPABASE_URL`
- `PUBLIC_SUPABASE_ANON_KEY` — both pages
- `PUBLIC_SUPABASE_SERVICE_ROLE_KEY` — admin chunk only
- `PUBLIC_ADMIN_PASSWORD_HASH` — SHA-256 of admin password

Local-only (never inlined):
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` — for `scripts/`

### What NOT to Use

- No backend server. Everything is client-side or Supabase edge functions (none defined yet).
- No third-party auth library. Token-in-URL is the auth model.
- No analytics SDKs.
- No third-party UI component libraries. Vanilla CSS.

---

## 10. Deployment

### Initial Setup (one-time, completed)

Schema applied via `scripts/apply-sql.mjs`. Repo secrets set via `gh secret set`. GitHub Pages enabled with Actions source. Workflow at `.github/workflows/deploy.yml`.

### Iteration Loop

1. Edit code locally
2. Branch → commit → `git push`
3. Open PR via `gh pr create`
4. Merge → workflow runs → production updates in ~30 seconds

### Adding a New HTML Deliverable

1. Drop the file at `pulse/public/deliverables/<slug>.html`
2. `git add public/deliverables/<slug>.html && git commit -m "Add <slug> deliverable" && git push`
3. Wire it to a card via admin Edit (Active reference path = `deliverables/<slug>.html`)

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

- If the link leaks or you want to start over, click **Rotate token**. Old URL stops working immediately.
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
7. The product feels like an IGTMS product, not a generic survey tool.

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
- White-labeling for other consultants beyond IGTMS

---

## 14. Configuration Decisions (Locked)

### 14.1 Production URL

```
https://tomdigati.github.io/pulse/
```

Custom domain deferred. `base: "/pulse"` Astro config for GitHub Pages.

### 14.2 Admin Authentication

Single shared password. Hash stored as `PUBLIC_ADMIN_PASSWORD_HASH` (SHA-256 hex), inlined into the admin chunk at build. Login flag in `sessionStorage`; tab close ends the session.

To rotate:
```bash
echo -n "<new-password>" | shasum -a 256
gh secret set PUBLIC_ADMIN_PASSWORD_HASH --repo tomdigati/pulse --body "<new-hash>"
gh workflow run deploy.yml --repo tomdigati/pulse --ref main
```

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
| Confirmed without edit | IGTMS Review |
| Edited the existing content | IGTMS Review |
| Uploaded a file | IGTMS Review |
| Skipped a card | Waiting on Good Life |
| Said they need help (regex on "need help") | Needs Attention |
| Indicated a blocker (regex on "blocked", "stuck", "waiting on") | Blocked |
| Card not yet viewed | Waiting on Good Life |
| Selected "Done" or "Approved" or "Complete" | Approved |
| Empty file-upload, empty multi-select | Waiting on Good Life |

Valid status values:
`Waiting on IGTMS`, `Waiting on Good Life`, `Needs Attention`, `IGTMS Review`, `Client Review`, `Blocked`, `Approved`, `Complete`.

> The "Waiting on Good Life" label is a holdover from the first engagement. For non-GLC engagements, the operator can override per-card. Future enhancement: per-engagement default-status labels.

### 14.4 Timestamps

Display in operator's local timezone via `Intl.DateTimeFormat`. Relative under 24h ("2 hours ago"); absolute with timezone for older ("April 23, 2026 at 4:15 PM CDT"). Stored UTC; conversion at display.

### 14.5 Production Deployment SLA

No formal SLA. Tom owns the entire stack. Deploys when satisfied.

### 14.6 Hosting and Infrastructure

- Repo: `tomdigati/pulse`
- Supabase project: `yhphmutbquhgikqjypch`
- GitHub Pages with Actions source
- Domain: GitHub Pages default

### 14.7 Token Format

16 hex chars, generated via `encode(gen_random_bytes(8), 'hex')` (SQL) or `crypto.getRandomValues(new Uint8Array(8))` (admin rotate). 64 bits of entropy.

### 14.8 Bundle Isolation

Astro splits per page. Verified:

| Bundle | Anon key | Service role key | Admin password hash |
|---|---|---|---|
| User-facing (`/`) | yes | **no** | **no** |
| Admin (`/admin/`) | yes | yes | yes |

Anyone who fetches the admin chunk can extract the service role key — accepted v1 caveat (single operator, internal). Pre-public hardening: move admin queries behind an edge function (§13).

### 14.9 HMR in Dev

Both `app.ts` and `admin.ts` call `import.meta.hot.decline()` so any code change forces a full page reload in dev. Prevents stale module instances accumulating button listeners.

---

## End of Specification

Source of truth for the Pulse product as deployed. Each engagement has its own brief in the database, edited from `/admin/`.

*Pulse by IGTMS. Decisions, not paperwork.*
