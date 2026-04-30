# Pulse by IGTMS Project Specification

**Repository:** tomdigati/pulse
**Product owner:** Tom DiGati, Client Transformation Lead, IGTMS
**Specification version:** 1.1 (post-shipping update)
**Last updated:** April 2026

---

## How to Use This Document

This is the v1-as-built source of truth for Pulse. The original v1.0 brief lives in git history (`b5fc78f`). Every section below has been updated to match what actually shipped, including optimizations and design changes made during the build. Where the implementation diverges from the original brief, the rationale is in a "Changed from v1.0" callout.

Read this whole document before changing anything. The constraints reflect real engagement context with a real first client (Renee Mueller).

---

## 1. Product Overview

### What Pulse Is

Pulse is a mobile-first decision and validation tool. A consultant (the operator) sends a single secure link to a client (the user). The user opens the link on any device, taps through a sequence of pre-populated decision cards, confirms or corrects what we already know, and uploads documents where needed. Progress saves automatically. The user can stop and resume at any time from any device with the same link.

The operator sees responses in real time as they come in, edits card text directly when wording needs to change, and exports responses to whatever project management or operations system the engagement uses (in v1, that is ClickUp).

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
- No em-dashes anywhere (use commas, parentheses, or sentence breaks instead)
- Use "you" and "your" in user-facing copy
- Avoid jargon, acronyms, and consultant-speak
- Avoid words like "audit," "accountability," "compliance"

---

## 2. The User: Renee Mueller

The first deployment of Pulse is for Renee Mueller, President of Good Life Capital and CEO of Vrly Media. Every design decision is evaluated against whether it serves her specifically. If it does not work for Renee, it does not ship.

### What We Know About Her

**Behavioral profile:**
- Mobile-first. She lives on her phone between events, flights, and speaking engagements.
- Time-starved. Regularly misses meetings due to travel and double-booking.
- Has dyscalculia and flips numbers. Numbers must be plain and large. Never bury figures in dense paragraphs.
- Strong communicator. Comfortable saying she does not know something. Prefers fast-moving sessions over polished documents she cannot influence.
- Exhausted but engaged. Doing homework on her phone when she has 5 minutes.
- Communicates by layering toward her point. Mirror-and-confirm approach works best.

**What this means for Pulse design:**
- Tappable, not type-heavy.
- Resumable across sessions and devices.
- One question, one decision, one tap per card.
- Auto-save on every action.
- Warm, forgiving tone. No scolding progress bars.

### Authentication Approach for Renee

Renee gets a single unique URL. Tom sends it via text or email. She opens it on any device and is automatically logged in. **No password. No login screen. No account creation. The URL itself is her identity.** This is intentional friction reduction.

The URL contains a 16-hex-character random token (64 bits of entropy) that maps to her record in Supabase. The token is permanent for v1 and rotates only when Tom rotates it from the admin view.

> **Changed from v1.0**: The original brief called for a 32+ character token. We use 16 hex chars (64 bits of entropy) so the URL fits cleanly in an SMS preview. 64 bits is unguessable for a private invite link; the brute-force surface is theoretical at this scale.

---

## 3. The Operator: Tom DiGati

Tom is the consultant sending the pulse. He uses Pulse from a desktop browser (admin is not mobile-optimized; the user-facing app is). He needs:

- A simple admin view to see Renee's responses as they arrive
- A way to view uploaded files (signed download URLs)
- A way to export responses for pasting into ClickUp
- A way to **edit card text** directly from the admin (title, category, context, question, options, attachment, skip-allowed) so wording fixes don't require a deploy
- A way to generate a new client engagement and rotate the unique URL

For v1, the admin view is a single password-protected page that lists all engagements and lets Tom drill into per-engagement responses with editing and export per card.

---

## 4. The Card Model

Every interaction in Pulse is a card. A card is a single unit of decision or input. The user sees one card at a time. Each card has:

- **Title**, short, plain language
- **Context**, what we already know, 2 to 4 sentences. The "what we know" content from the source material. Should read in 15 seconds.
- **Question**, one specific decision or input we need from the user. Never compound.
- **Response type**, one of: `confirm-edit`, `single-select`, `multi-select`, `short-text`, `long-text`, `file-upload`, `document-link`, `contact-share`
- **Options** (for select types), the answer choices (jsonb array)
- **Default value**, the existing text shown for confirm-edit cards (nullable)
- **Skip allowed**, boolean. Some cards must be answered. Most can be skipped.
- **Category**, for grouping in the admin view (Client Review, Document and Access Requests, Decisions)
- **Attachment path** *(new in shipped v1)*, optional path to an HTML reference file (relative to site base, e.g. `deliverables/glc-org-chart.html`). If set, the card renders a "View Active Reference" button that opens the file in a sandboxed iframe modal.

### Card Response Types

**confirm-edit**
The most common type. We show what we believe to be true. User taps "Yes, correct" or "Needs edit" or "Skip for now." If they tap edit, a textarea opens for a free-form correction.

**single-select**
2 to 5 options. User taps one. **Auto-saves on tap** and advances. Options are mutually exclusive.

**multi-select**
2 to 9 options. User toggles any number, then taps Continue.

**short-text**
Single line input. Email addresses, names, URLs.

**long-text**
Multi-line textarea. Used sparingly. Placeholder reads *"Add a note. Tap the keyboard mic to talk."* — the iOS keyboard mic dictates straight into the textarea, no extra wiring needed.

**file-upload**
User taps to upload one or more files. Supports PDF, DOCX, PNG, JPG, CSV, XLSX. Max 25MB per file. Up to 5 files per card. Streams to Supabase Storage at `{client_id}/{card_id}/{uuid}-{filename}`.

**document-link**
User pastes a URL to a Google Doc, Drive folder, or other shared resource. Must start with `http://` or `https://`.

**contact-share**
Three short text fields: name, email, role. Email is required.

### Optional Notes Field *(new in shipped v1)*

On every card type **except** `confirm-edit`, `short-text`, and `long-text`, an optional "Notes (optional)" textarea renders below the primary input. Placeholder: *"Add a note. Tap the keyboard mic to talk."* The note is folded into `response_value.note` on save, surfaces in the admin response detail, and shows up in the ClickUp markdown export under a `**Note:**` suffix.

`confirm-edit` cards already have a free-form correction path via "Needs edit" so they don't get a duplicate field. `short-text` and `long-text` cards already have an open text input that uses the same voice-friendly placeholder, so a separate notes field would be redundant.

### Card State

Each card has one of these states for each user (per-user, not per-card globally):
- `not_started`, no row exists in `responses`
- `viewed`, user landed on the card but didn't submit. Inserted automatically the first time the card renders.
- `answered`, user submitted a response
- `skipped`, user explicitly skipped
- `needs_edit`, reserved (not currently emitted by the v1 client; the edit-then-submit flow goes straight to `answered`)

The `viewed` row gives the operator a signal that the user opened a card but didn't act. Inserted via `INSERT ... ON CONFLICT DO NOTHING` so re-renders are idempotent.

---

## 5. Renee's v1 Card Set

These 19 cards are the v1 deployment for Renee. Source content is from the ClickUp export `Vrly_IGTMS_Data_Capture.txt` plus additional items from the GLC Master Context.

Each card below specifies title, context, question, response type, and category. The seed file uses these verbatim. Tom can edit any of this from the admin without a redeploy.

### Category: Client Review (8 cards)

1. **Service Delivery Matrix** — `confirm-edit`
2. **Sale to Fulfillment Process** — `long-text`
3. **Ideal Client Profile (ICP) Confirmation** — `confirm-edit`, with `attachment_path = deliverables/vrly-icp-v1.html`
4. **Current Services and Packages** — `confirm-edit`
5. **Active Vendor List** — `confirm-edit`
6. **CMO Responsibilities** — `confirm-edit`
7. **Ownership of Delivery by Stages** — `long-text`
8. **SLA or Service Guarantees** — `confirm-edit`

### Category: Document and Access Requests (6 cards)

9. **Vendasta Access** — `file-upload`
10. **Website Admin Access** — `file-upload`
11. **Pitch Decks and Brand Materials** — `file-upload`
12. **Case Studies and Testimonials** — `long-text`
13. **GLC Org Chart** — `file-upload`, with `attachment_path = deliverables/glc-org-chart.html`
14. **Tools List Confirmation** — `multi-select` (6 deprecation candidates)

### Category: Decisions (5 cards)

15. **Operator Hire Timeline** — `single-select` (5 options, **required**)
16. **Doug Documents Validation** — `multi-select` (8 options)
17. **Logan Introduction Status** — `single-select` (4 options, **required**)
18. **Axiolo Part 1 Approval** — `multi-select` (9 itemized options, **required**)
19. **Anything Else We Should Know** — `long-text`

> **Changed from v1.0**: Card 13's wording was updated to remove all references to Jeff Cohn (per Tom's request after seeing the deployed copy). Original context and question are preserved in git history.

The full body text for each card is in `supabase/seed.sql`. Re-running the seed updates card content idempotently via `ON CONFLICT (client_id, order_index) DO UPDATE`.

---

## 6. Database Schema (Supabase)

### Tables

**clients**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | `gen_random_uuid()` |
| name | text not null | "Renee Mueller" |
| org_name | text | "Vrly Media / Good Life Capital" |
| engagement_name | text | "GLC Engagement v1" |
| token | text not null unique | 16-hex-char random, generated via `encode(gen_random_bytes(8), 'hex')` |
| created_at | timestamptz | default now() |
| last_active_at | timestamptz | nullable, touched on every save |

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
| response_type | text not null | check constraint enforces enum |
| options | jsonb | nullable, array of strings for select types |
| default_value | text | nullable |
| skip_allowed | boolean | default true |
| **attachment_path** | text | nullable, relative path to HTML reference |
| created_at | timestamptz | |

**responses**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| card_id | uuid FK → cards(id) | on delete cascade |
| client_id | uuid FK → clients(id) | on delete cascade |
| state | text not null | enum: not_started, viewed, answered, skipped, needs_edit |
| response_value | jsonb | shape depends on response_type, see §6.3 |
| viewed_at | timestamptz | set on first render |
| answered_at | timestamptz | set on submit |
| created_at | timestamptz | |
| updated_at | timestamptz | trigger auto-updates |
| **unique** | (card_id, client_id) | upsert key |

**uploads**
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| card_id | uuid FK | on delete cascade |
| client_id | uuid FK | on delete cascade |
| file_name | text not null | |
| file_size_bytes | integer not null | |
| storage_path | text not null | `{client_id}/{card_id}/{uuid}-{filename}` |
| mime_type | text | |
| uploaded_at | timestamptz | default now() |

### 6.1 Helper Functions

```sql
-- Read the x-pulse-token request header (NULL if absent).
create or replace function public.pulse_request_token()
returns text language sql stable as $$
  select nullif(coalesce(
    current_setting('request.headers', true)::jsonb->>'x-pulse-token',
    ''
  ), '');
$$;

-- Resolve the client_id for the presented token.
create or replace function public.pulse_request_client_id()
returns uuid language sql stable as $$
  select id from public.clients
  where token = public.pulse_request_token() limit 1;
$$;
```

### 6.2 Row Level Security

All four tables have RLS enabled. The user-facing app uses the **anon key** plus an `x-pulse-token` request header. RLS policies read the header via `pulse_request_token()` and only return rows whose `client_id` matches.

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
- **clients**: `clients_self_read` (select where token matches), `clients_self_touch` (update where token matches)
- **cards**: `cards_self_read` (select where `client_id = pulse_request_client_id()`)
- **responses**: `responses_self_read`, `responses_self_insert`, `responses_self_update`
- **uploads**: `uploads_self_read`, `uploads_self_insert`

### 6.3 Storage

Bucket `pulse-uploads`, private. File path convention:
```
{client_id}/{card_id}/{uuid}-{sanitized-filename}
```

Storage policies on `storage.objects`:
- `pulse_uploads_self_read`: select where `(storage.foldername(name))[1] = pulse_request_client_id()::text`
- `pulse_uploads_self_insert`: same with check

The `x-pulse-token` header propagates from supabase-js's `global.headers` into the storage service's RLS context. Verified during M4: a real upload from the browser landed in storage and inserted the matching `uploads` row, all gated by token.

### 6.4 response_value shapes

| Response type | Shape (when state = 'answered') |
|---|---|
| confirm-edit | `{ confirmed: true }` or `{ confirmed: false, correction: string }` |
| single-select | `{ selected: string, note?: string }` |
| multi-select | `{ selected: string[], note?: string }` |
| short-text | `{ text: string }` *(no separate note — the input is the note)* |
| long-text | `{ text: string }` *(same)* |
| document-link | `{ url: string, note?: string }` |
| contact-share | `{ name, email, role, note?: string }` |
| file-upload | `{ file_ids: uuid[], note?: string }` |

When state = `skipped` and a note was typed, value is `{ note: string }`. Otherwise null.

### 6.5 Seed

`supabase/seed.sql` is idempotent:
- Renee's client row uses fixed UUID `00000000-0000-0000-0000-000000000001`. `ON CONFLICT (id) DO NOTHING` so the token is generated only on first run and stays stable on re-runs.
- All 19 cards use `ON CONFLICT (client_id, order_index) DO UPDATE` so re-running picks up wording changes without recreating rows.
- Attachment paths set in a separate, idempotent block at the end of the seed.

---

## 7. Frontend Specification

### Architecture

- Astro 5 static site, code-split per page.
- Two pages:
  - `src/pages/index.astro` — user-facing app (Renee).
  - `src/pages/admin.astro` — operator console (Tom).
- Each page bundles its own JS chunk. The user-facing chunk contains the **anon key only**. The admin chunk contains the **service role key**. Verified against the production build (the JWT signature for the service role key appears in the admin chunk and not the user chunk).
- Vanilla TypeScript + DOM-string templates. No framework runtime.
- All state lives in Supabase. No localStorage for app state. SessionStorage is used only for the admin-login flag and the picker's transient unlock state.

### URL Pattern

```
https://tomdigati.github.io/pulse/?t={16-hex-char-token}
```

Custom domain (`pulse.igtms.com`) is deferred per §14.1. The `base: "/pulse"` Astro config makes the site work under the GitHub Pages subpath.

### Bootstrap Flow

1. Page loads, runs `src/scripts/app.ts`.
2. Script reads `?t=` from the URL.
3. Builds a Supabase client with the anon key plus a `x-pulse-token` header.
4. Fetches the `clients` row (RLS gates it to the matching token), then in parallel:
   - All 19 cards for that client
   - All existing responses
   - All existing uploads
5. Computes `bootIndex = firstUnansweredIndex(cards, responses)` — the first card where state is not `answered` or `skipped`. (`viewed` counts as not-answered, so reopening the link returns the user to the card they left.)
6. Renders that card. If `bootIndex > 0`, also renders the resume banner.

### Card UI Pattern

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
|  Notes (optional)                        |
|  [textarea]                              |
|                                          |
|  [   Continue / Confirm   ]              |
|  [   Skip for now         ]              |
+------------------------------------------+
```

### 7.1 Navigation Controls *(new in shipped v1)*

The topbar shows three controls grouped on the right:

- **Back arrow** (`‹`) — goes to the previous card. Disabled at index 0.
- **Progress button** (`4 of 19 ▾`) — clickable to open the slide picker.
- **Forward arrow** (`›`) — goes to the next card. Disabled at index 19.

The user can also progress through the deck via Confirm / Continue / Skip buttons (which save their response, then advance).

**Slide picker** is a modal overlay listing all 19 cards with their state (Answered, Skipped, Viewed, Not viewed). Tap a row to jump directly. Esc and backdrop-tap dismiss. The current card is highlighted in green.

### 7.2 Pre-fill on Revisit *(new in shipped v1)*

Navigating back to an already-answered card pre-loads the form so the user can refine instead of restart:
- multi-select / single-select chips: prior selections highlighted via `seedDraftFromResponse` on each navigation
- short-text / long-text / document-link: input value pre-filled from `response_value`
- contact-share: all three fields pre-filled
- confirm-edit's edit textarea: pre-filled with the prior `correction` if edits were sent before

Above the input, a small green strip ("prior-hint") explains the loaded state:
- "You confirmed this earlier."
- "You sent edits earlier."
- "You skipped this earlier. Answer if you want to revisit."
- "Your previous answer is loaded. Edit and resubmit to update it."

Re-submission upserts the same `(card_id, client_id)` row.

### 7.3 Active Reference Modal *(new in shipped v1)*

If a card has `attachment_path` set, the card renders a "View Active Reference" button between context and question. Tapping it opens a modal with a sandboxed iframe loading `{BASE_URL}{attachment_path}`. Sandbox is `allow-scripts` so interactive deliverables work; same-origin is denied, so iframe content can't reach the parent.

Files live in `pulse/public/deliverables/`. They deploy via the standard GitHub Actions build — drop a new file in the directory, commit, push, and reference it via the admin's Edit form.

Modal closes on:
- The X in the panel header
- Tap on the dim backdrop
- Esc key

### 7.4 Save and Resume

- Every response is saved on submit.
- Re-saves upsert on `(card_id, client_id)`. `viewed_at` is preserved through the upsert so the admin can see when the card was first opened separately from when it was answered.
- After save, `last_active_at` is updated on the client row (best-effort, fire-and-forget).
- On boot, `firstUnansweredIndex` returns the user to where they left.
- Resume banner ("Welcome back. Picking up where you left off.") shows whenever `bootIndex > 0`. It dismisses on the first save or advance.

> **Changed from v1.0**: The brief called for a time-based banner fade. We dismiss on first interaction instead — cleaner UX and more reliable than a setTimeout (which had quirky behavior in dev mode).

### 7.5 File Upload UX

- Tap the green dropzone → native iOS picker (Camera, Photos, Files).
- File streams to Supabase Storage with a pending chip showing percent / error state.
- After successful upload, an `uploads` row is inserted and the file appears as a chip with name, size, and an X to remove.
- Up to 5 files per card. Max 25MB per file. Both limits surface in the dropzone label.
- Continue is enabled when there's at least one file OR a non-empty note.

### 7.6 Empty States and Errors

- Missing token: "This link is missing a code. Please check the link your consultant sent you."
- Invalid token: "We could not find your engagement. Please check the link or contact Tom."
- Network failure on save: amber banner reading "Could not save just now. We will retry automatically." Plus a manual Retry button. The app retries the same action every 10 seconds until success (per spec §7); manual Retry cancels the timer and retries immediately.

### 7.7 Polish Notes

- Tap targets ≥44px throughout (per Apple HIG)
- Safe-area insets on `.page` padding so iPhone notch / home indicator don't crowd content
- `min-height: 100dvh` for iOS Safari's dynamic viewport
- Card / error / banner enter with a 0.3s fade + 6px translateY; loading state pulses subtly. All respect `prefers-reduced-motion`
- Focus rings via `:focus-visible` on every interactive element (visible on keyboard, not on mouse/touch)

---

## 8. Tom's Admin View

Lives at `/admin/` (e.g. `https://tomdigati.github.io/pulse/admin/`). Desktop only (per Tom's preference; not mobile-optimized). Password-gated. After login, two views:

### 8.1 Login

Single password field. Input → SHA-256 → compare to `PUBLIC_ADMIN_PASSWORD_HASH` (inlined into the admin chunk at build time). On match, `sessionStorage` flag set; tab close ends the session.

### 8.2 Engagement List

Table with one row per client:

| Client | Engagement | Progress | Last active | Actions |
|---|---|---|---|---|
| Renee Mueller<br>Vrly Media / GLC | GLC Engagement v1 | 4 / 19 | 2 hours ago | View · Copy link · Rotate token |

- **Progress** = `count(answered) + count(skipped) / total`, rendered as a green pill.
- **Last active** uses the operator's local timezone via `Intl.DateTimeFormat`. Relative format under 24h ("2 hours ago"); absolute with timezone for older.
- **Copy link** writes the production URL with the current token to the clipboard.
- **Rotate token** generates a fresh 16-hex-char token, PATCHes the row, copies the new URL. The old URL stops working immediately.

### 8.3 Response Detail

For a selected client, all 19 cards in order. Each card row shows:
- Card number and category
- Title
- State badge (Answered green, Skipped amber, Viewed gray, Not viewed gray)
- Formatted response body
- Suggested ClickUp status dropdown (per §14.3 mapping, override-able)
- Timestamp ("Answered 5 minutes ago" or "Viewed 2 hours ago")
- **Edit button** *(new in shipped v1)* and **Copy** button

### 8.4 Card Editing *(new in shipped v1)*

Tapping Edit on any row swaps the read-only header for an inline form:

- Title
- Category
- Context (textarea)
- Question (textarea)
- Options (one per line, only for select types)
- Active reference path (e.g. `deliverables/example.html`, optional)
- Skip allowed toggle

Save PATCHes the `cards` row (service role bypasses RLS), updates the local cards array, and re-renders just that article. Cancel reverts. A toast confirms each save.

Existing responses are tied to `card_id`, not card text, so prior answers remain valid after edits.

### 8.5 ClickUp Markdown Export

Per spec §14.3 format. Two paths:
- **Per-card Copy** button on each row — copies a single card's block.
- **Copy all as Markdown** at the top — concatenates all 19 blocks separated by `---`.

File-upload responses include 7-day signed URLs in the markdown (long enough for the link to stay good in ClickUp).

---

## 9. Tech Stack

| | |
|---|---|
| Frontend | Astro 5, vanilla TypeScript, no framework runtime |
| Database | Supabase Postgres |
| Storage | Supabase Storage (private bucket `pulse-uploads`) |
| Hosting | GitHub Pages |
| CI/CD | GitHub Actions (`actions/deploy-pages@v4`) |
| Build | `npm run build` (Vite under Astro) |
| Package manager | npm |
| Domain | `tomdigati.github.io/pulse/` (custom domain deferred) |

### Dependencies

- `@supabase/supabase-js` (browser)
- `pg` (devDep, used by `scripts/apply-sql.mjs` for one-off SQL applies)

### Environment Variables

Build-time:
- `PUBLIC_SUPABASE_URL`
- `PUBLIC_SUPABASE_ANON_KEY` — inlined into both pages
- `PUBLIC_SUPABASE_SERVICE_ROLE_KEY` — inlined into the **admin chunk only**
- `PUBLIC_ADMIN_PASSWORD_HASH` — SHA-256 hex of the admin password

Local-only (never inlined):
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` — used by `scripts/verify.mjs` and `scripts/apply-sql.mjs`

### What NOT to Use

- No backend server. Everything is client-side or Supabase edge functions (none defined yet).
- No third-party auth library. Token-in-URL is the auth model.
- No analytics SDKs.
- No third-party UI component libraries. Vanilla CSS.

---

## 10. Deployment Plan

### Initial Setup (one-time, completed)

1. Astro scaffold under `pulse/` with `base: "/pulse"`.
2. Supabase project `yhphmutbquhgikqjypch` created.
3. Schema applied via `scripts/apply-sql.mjs` (uses a direct Postgres connection with `pg`).
4. Seed applied (creates Renee + 19 cards + a test client).
5. Repo secrets set via `gh secret set`:
   - `PUBLIC_SUPABASE_URL`
   - `PUBLIC_SUPABASE_ANON_KEY`
   - `PUBLIC_SUPABASE_SERVICE_ROLE_KEY`
   - `PUBLIC_ADMIN_PASSWORD_HASH`
6. GitHub Pages enabled with `build_type: workflow` via `gh api repos/.../pages -X POST`.
7. `.github/workflows/deploy.yml` builds on push to `main` and on `workflow_dispatch`.

### Iteration Loop

1. Edit code locally.
2. Branch + commit + `git push`.
3. Open PR via `gh pr create`.
4. Merge → workflow runs → production updates in ~30 seconds.

### Adding a New HTML Deliverable

1. Drop the file in `pulse/public/deliverables/<slug>.html`.
2. `git add public/deliverables/<slug>.html && git commit -m "Add <slug> deliverable" && git push`.
3. Wire it to a card via the admin Edit feature (Active reference path = `deliverables/<slug>.html`).

### Renee's Onboarding

1. Tom resets responses if needed (via `scripts/apply-sql.mjs` or admin).
2. Tom copies Renee's URL from the admin (Copy link button).
3. Tom sends via SMS with a short framing note.
4. Renee opens, taps through cards over multiple sessions.
5. Tom monitors the admin as responses come in.
6. Tom exports responses to ClickUp using the markdown buttons.

---

## 11. Implementation Order (as shipped)

The original v1.0 brief defined Milestones 1–9. Everything 1–8 shipped. Milestone 9 ("send to Renee") is the operational handoff and not a code change. Several post-M8 polish PRs followed once Tom started testing.

### Shipped Milestones

| # | What | PR |
|---|---|---|
| M1 | Astro scaffold + Supabase schema/seed/verify | #1 |
| M2 | Token-header RLS + URL auth + render Card 1 | #1 |
| M3 | Confirm/Edit/Skip + response upserts + retry banner | #1 |
| M4 | All response types + Active Reference modal + file upload | #1 |
| M5 | Save and resume + viewed-state + resume banner | #1 |
| M6 | Brand polish, tap targets, transitions, focus rings, auto-retry | #1 |
| M7 | `/admin` with password gate + ClickUp markdown export | #1 |
| M8 | GitHub Actions deploy + first production deploy | #1 |
| Post-M8 | Back/forward navigation + slide picker | #2 |
| Post-M8 | Notes textarea on every card + Card 13 Jeff removal | #3 |
| Post-M8 | Drop redundant notes on open-text cards | #4 |
| Post-M8 | Inline card editing in admin | #5 |
| Post-M8 | Shorter 16-char tokens | #6 |
| Post-M8 | ICP deliverable wired to Card 3 | #7 |

---

## 12. v1 Success Criteria

Pulse v1 is successful if all of the following are true:

1. Renee opens her link on her phone in under 5 seconds. ✓
2. She taps through at least 10 cards in a single session of under 5 minutes.
3. She returns to the app at least once and resumes where she left off. ✓ (resume flow shipped)
4. She uploads at least one document successfully from her phone. ✓ (file-upload verified)
5. Her responses are visible to Tom in the admin view in real time. ✓
6. Tom exports her responses to ClickUp using the markdown export. ✓
7. Renee gives positive feedback on the experience.
8. The product feels like an IGTMS product, not a generic survey tool. ✓

Items 2, 3, 7 require Renee actually using the app and are validated post-send.

---

## 13. Future Enhancements (Out of Scope for v1)

Not in v1, noted for context:

- Move admin queries behind a Supabase edge function so the service role key isn't in the bundle (current security caveat for admin URL discovery)
- Custom domain `pulse.igtms.com` (DNS not configured)
- Card creation through admin UI (cards are seeded; admin can edit but not create)
- Per-client password gate (current model: URL token alone is the credential)
- Multi-engagement support per client
- Email or SMS notifications on submit
- Collaborative responses (multiple stakeholders per client)
- Conditional card logic
- Analytics
- White-labeling for other consultants

---

## 14. Configuration Decisions (Locked)

### 14.1 Production URL

```
https://tomdigati.github.io/pulse/
```

Custom domain deferred. App's Astro config has `base: "/pulse"` and `site: "https://tomdigati.github.io"` to match.

### 14.2 Admin Authentication

Single shared password. Hash stored as `PUBLIC_ADMIN_PASSWORD_HASH` (SHA-256 hex). Inlined at build into the admin chunk only. Login flag in `sessionStorage`; tab close ends the session.

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

One block per card. The "Copy all" button concatenates with `---` rules between.

**Recommended status** is auto-suggested per the table below. Tom can override per-card via a dropdown before copying.

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

### 14.4 Timestamps

Display in operator's local timezone via `Intl.DateTimeFormat`. Relative format under 24h ("2 hours ago"); absolute with timezone for older ("April 23, 2026 at 4:15 PM CDT"). Stored in UTC; conversion at display time.

### 14.5 Production Deployment SLA

No formal SLA. Tom owns the entire stack. Deploys when Tom is satisfied. No customer commitments, no uptime guarantees.

### 14.6 Hosting and Infrastructure Ownership

- GitHub repository: `tomdigati/pulse`
- Supabase project: `yhphmutbquhgikqjypch`
- GitHub Pages: enabled with Actions source
- Domain: GitHub Pages default

No third-party dependencies on Axiolo infrastructure for hosting. Axiolo's role is engineering capability where needed, not ownership of the deployed product.

### 14.7 Token Format *(new in shipped v1)*

16 hex chars, generated via `encode(gen_random_bytes(8), 'hex')` in SQL or `crypto.getRandomValues(new Uint8Array(8))` in the admin's rotate flow. 64 bits of entropy.

### 14.8 Bundle Isolation *(new in shipped v1)*

Astro splits per page. Verified against the production build:

| Bundle | Anon key | Service role key | Admin password hash |
|---|---|---|---|
| User-facing (`/`) | yes | **no** | **no** |
| Admin (`/admin/`) | yes | yes | yes |

The user-facing chunk cannot be used to read other clients' data; the anon key + RLS gate it to the matching token. Anyone who fetches the admin chunk can extract the service role key and password hash — that's an accepted v1 caveat (single operator, internal use). Pre-public hardening is to move admin queries behind an edge function (see §13).

### 14.9 HMR in Dev *(new in shipped v1)*

Both `app.ts` and `admin.ts` call `import.meta.hot.decline()` so any code change forces a full page reload in dev. This prevents stale module instances from accumulating button listeners on the same DOM during iteration. Production builds don't include the HMR client.

---

## End of Specification

This document is the source of truth for Pulse v1 as deployed. Update it as decisions evolve.

*Pulse by IGTMS. Decisions, not paperwork.*
