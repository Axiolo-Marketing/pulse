# Pulse by IGTMS Project Specification

**Repository:** tomdigati/pulse
**Product owner:** Tom DiGati, Client Transformation Lead, IGTMS
**Specification version:** 1.0
**Last updated:** April 2026

---

## How to Use This Document

This is the project brief for Claude Code. Read this entire document before writing any code. Every section below is intentional. The constraints, design choices, and user details are based on real engagement context with a real client. Do not generalize or simplify what is here.

When you are ready to build, follow the implementation order in Section 11. Confirm each milestone before moving to the next.

---

## 1. Product Overview

### What Pulse Is

Pulse is a mobile-first decision and validation tool. A consultant (the operator) sends a single secure link to a client (the user). The user opens the link on any device, taps through a sequence of pre-populated decision cards, confirms or corrects what we already know, and uploads documents where needed. Progress saves automatically. The user can stop and resume at any time from any device with the same link.

The operator sees responses in real time as they come in, then exports those responses to whatever project management or operations system the engagement uses (in v1, that is ClickUp).

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
- Avoid words like "audit," "accountability," "compliance", these feel transactional and pressuring

---

## 2. The User: Renee Mueller

The first deployment of Pulse is for Renee Mueller, President of Good Life Capital and CEO of Vrly Media. Every design decision should be evaluated against whether it serves her specifically. If it does not work for Renee, it does not ship.

### What We Know About Her

**Behavioral profile:**
- Mobile-first. She lives on her phone between events, flights, and speaking engagements.
- Time-starved. She regularly misses meetings due to travel and double-booking. She apologizes often for delayed responses.
- Has dyscalculia and flips numbers. Numbers must be plain and large. Never bury figures in dense paragraphs.
- Strong communicator. Comfortable saying she does not know something. Prefers fast-moving sessions over polished documents she cannot influence.
- Exhausted but engaged. Doing homework on her phone when she has 5 minutes.
- Communicates by layering toward her point. Mirror-and-confirm approach works best.

**Quote from a typical Renee message:**
> "I still have plenty to get you, working on it, but yes just trying to cover all the other bases as well. I apologize I didn't respond yesterday, it's been a little bit of a whirlwind with traveling, and we had the opening for an event last night, and speaking today, then done!"

**What this means for Pulse design:**
- The interface must be tappable, not type-heavy.
- Sessions must be resumable. She will start, get interrupted, and pick up later from a different device.
- Cognitive load per card must be minimal. One question, one decision, one tap.
- The system must save automatically. She will close the browser without thinking about it.
- The tone must be warm and forgiving. No progress bars that scold. No reminders that nag.

### Authentication Approach for Renee

Renee gets a single unique URL. Tom sends it via text or email. She opens it on any device and is automatically logged in. No password. No login screen. No account creation. The URL itself is her identity.

The URL contains a long random token (32+ characters) that maps to her record in Supabase. The token is permanent for v1 (no expiration). If the token leaks, Tom can rotate it from the admin view.

---

## 3. The Operator: Tom DiGati

Tom is the consultant sending the pulse. He needs:
- A simple admin view to see Renee's responses as they arrive
- A way to view uploaded files
- A way to export responses for pasting into ClickUp
- A way to add or edit cards (in a future version, but v1 can have cards defined in code)
- A way to generate a new client engagement and create the unique URL

For v1, the admin view can be simple, a single password-protected page that lists all responses for the active engagement.

---

## 4. The Card Model

Every interaction in Pulse is a card. A card is a single unit of decision or input. The user sees one card at a time. Each card has:

- **Title**, short, plain language, describes what we are asking about
- **Context**, what we already know, written in 2 to 4 sentences. This is the "what we know" content from the source material. Never longer than the user can read in 15 seconds.
- **Question**, the specific decision or input we need from the user. One question per card. Never compound.
- **Response type**, one of: confirm-edit, single-select, multi-select, short-text, long-text, file-upload, document-link, contact-share
- **Options** (for select types), the answer choices
- **Skip allowed**, boolean. Some cards must be answered. Most can be skipped.
- **Category**, for grouping in the admin view (Client Review, Vendor Access, Decisions, Document Requests)

### Card Response Types

**confirm-edit**
The most common type. We show what we believe to be true. User taps "Yes, correct" or "Needs edit." If they tap edit, a single short text field appears with the existing text pre-filled. They edit and save. Three buttons total: Confirm, Edit, Skip.

**single-select**
2 to 4 options. User taps one. Option list is short and mutually exclusive.

**multi-select**
2 to 8 options. User taps any number. Useful for "which of these apply" questions.

**short-text**
Single line input. Email addresses, names, URLs.

**long-text**
Multi-line textarea. Used sparingly. Only when no other format fits.

**file-upload**
User taps to upload one or more files. Supports PDF, DOCX, PNG, JPG, CSV, XLSX. Max 25MB per file. Up to 5 files per card.

**document-link**
User pastes a URL to a Google Doc, Drive folder, or other shared resource.

**contact-share**
Specialized type for asking Renee to share a contact (name, email, role) with us. Three short text fields.

### Card State

Each card has one of these states for each user:
- `not_started`, user has not seen this card
- `viewed`, user opened the card but did not respond
- `answered`, user submitted a response
- `skipped`, user explicitly skipped
- `needs_edit`, user said edit but did not finish typing the correction

The system tracks state per user, not per card globally.

---

## 5. Renee's v1 Card Set

These 19 cards are the v1 deployment for Renee. Source content is from the ClickUp export `Vrly_IGTMS_Data_Capture.txt` plus additional items from the GLC Master Context that did not make it into ClickUp.

Each card below specifies title, context, question, response type, and category. Use these verbatim in the seed data.

### Category: Client Review (8 cards)

---

**Card 1: Service Delivery Matrix**

*Category:* Client Review
*Question type:* confirm-edit

*Context shown to Renee:*
We have reconstructed your current service delivery model from the prior team's project sheet. Content Engine is owned by Enrique on production with Doug on strategy and is currently stalled. Ads Management is active under Logan Boyce. The website is in Phase 5 with Jenn on development and Enrique on graphics. Something Good Magazine had Megan on content, Enrique on design, and Jenn on layout, with March and April issues in progress when the team departed. CRM and automation work is incomplete and returning to GHL. Vrly Foundation has Jenn on web and CRM and Logan on ads, with the landing page and donor capture incomplete.

*Question:*
Is this picture accurate? If anything has changed or was never quite right, please tell us where to update.

*Skip allowed:* yes

---

**Card 2: Sale to Fulfillment Process**

*Category:* Client Review
*Question type:* long-text

*Context shown to Renee:*
We understand that leads come in primarily through your personal network, the ERS sphere, and Logan's coaching network. Once a prospect is ready, you sell them on a package, MRR billing begins, and a 90-day onboarding model takes over. The prior team built an ads onboarding form in Typeform routed through Vendasta, and a 6-email onboarding series was planned but never completed. Today, website changes and landing page requests route through Vendasta to your inbox, which makes you the bottleneck at every stage.

*Question:*
What moves a client from "sold" to "in onboarding" today, now that the prior team is gone? If anything currently lives in your head or in undocumented habits, that is exactly what we want to capture here.

*Skip allowed:* yes

---

**Card 3: Ideal Client Profile (ICP) Confirmation**

*Category:* Client Review
*Question type:* confirm-edit

*Context shown to Renee:*
We have you serving the busy entrepreneur, SMB owner, $2M to $8M revenue, who knows they need marketing but cannot staff it full-time. They are also building toward an exit, succession, or scaling event in the next 5 to 10 years. Strong industry fits include Home Services, Legal, Medical, Professional Services, and Large Nonprofits eligible for Google Ad Grants. Approximately 75% of your current clients came through your personal network or the ERS sphere.

*Question:*
Does this match how you think about your ideal client today? If you would tighten or widen this anywhere, tell us.

*Skip allowed:* yes

---

**Card 4: Current Services and Packages**

*Category:* Client Review
*Question type:* confirm-edit

*Context shown to Renee:*
We have six core packages on file: Vrly Content Engine (anchor product, fulfilled by Axiolo), Ads Management (Logan Boyce), AI Audit (entry point, IGTMS-led), AI Assistant (vendor TBD), Social Media Management (fulfilled by Axiolo, phasing toward Content Engine), and Crowdfunding Platform (fulfillment vendor TBD).

*Question:*
Are these still the active packages? If you have added, paused, or retired anything, tell us.

*Skip allowed:* yes

---

**Card 5: Active Vendor List**

*Category:* Client Review
*Question type:* confirm-edit

*Context shown to Renee:*
Active vendors we have on record: Logan Boyce / Digital Tabby (ads), Axiolo (incoming white-label fulfillment), Vendasta (current platform, being replaced), Magcloud (Something Good Magazine distribution). Prior team vendors with unconfirmed status: Jenn, Enrique, Megan Cimple, Mark Fisher, Aadeck, Magnfi Team.

*Question:*
Is the active vendor list correct, and which of the prior team vendors still have any ongoing relationship with you?

*Skip allowed:* yes

---

**Card 6: CMO Responsibilities**

*Category:* Client Review
*Question type:* confirm-edit

*Context shown to Renee:*
We have the CMO role at Vrly currently performed by you, with IGTMS providing fractional strategy. The role covers vendor and fulfillment oversight, content manager coordination with Logan, client communication and retention, package strategy and margin management, marketing for Vrly itself, and GLC ecosystem alignment. Rebecca's Operations Team handles back of house only.

*Question:*
Does this reflect the CMO function as you would describe it, or have we missed anything you consider part of this role?

*Skip allowed:* yes

---

**Card 7: Ownership of Delivery by Stages**

*Category:* Client Review
*Question type:* long-text

*Context shown to Renee:*
We have a six-stage model: Sale (you), Handoff to fulfillment (currently informal and you-dependent), Onboarding Days 1-30 (foundation and activation, owner unclear), Execution Days 31-60 (Axiolo and Logan), Optimization Days 61-90 (owner unclear), and Ongoing Post-90 (owner unclear).

*Question:*
For the stages where ownership is unclear, who do you currently expect to own each one? If the answer is "I do for now," that is fine to say.

*Skip allowed:* yes

---

**Card 8: SLA or Service Guarantees**

*Category:* Client Review
*Question type:* confirm-edit

*Context shown to Renee:*
The only SLA-adjacent language we have found is in your client onboarding document: billing continues even when client delays cause timeline slippage. We have not located any formal performance guarantee, turnaround commitment, or service-level agreement.

*Question:*
Are there any verbal or written commitments you have made to current clients that we should know about? Even informal ones count.

*Skip allowed:* yes

---

### Category: Document and Access Requests (6 cards)

---

**Card 9: Vendasta Access**

*Category:* Document and Access Requests
*Question type:* file-upload

*Context shown to Renee:*
We need access to Vendasta to complete the migration audit before the GHL build begins. The Vendasta contract runs through August 2026, and we need to validate what the prior team built before anything is rebuilt. We also need to resolve the Sammy's Superheroes email deliverability issue.

*Question:*
Can you share Vendasta admin credentials or send a workspace invitation to Tom? You can upload a screenshot of the credentials, paste them in a follow-up text, or attach an invite confirmation here.

*Skip allowed:* yes

---

**Card 10: Website Admin Access**

*Category:* Document and Access Requests
*Question type:* file-upload

*Context shown to Renee:*
The Vrly website is at vrlymultimedia.com on WordPress (Cloudways), with a target launch of May 20, 2026. We need admin access to assess the staging site before any rebuild work happens.

*Question:*
Please add the Axiolo team as admins or upload the WordPress credentials here. If easier, paste the WP login URL into a short text reply on the next card.

*Skip allowed:* yes

---

**Card 11: Pitch Decks and Brand Materials**

*Category:* Document and Access Requests
*Question type:* file-upload

*Context shown to Renee:*
We have not received pitch decks, brand guidelines, or service one-pagers. We know website wireframes (by Jenn) exist and a brand guidelines consistency checklist was in progress.

*Question:*
Can you upload any pitch decks, brand assets, or service one-pagers you have? Or paste a Google Drive link below if it is easier.

*Skip allowed:* yes

---

**Card 12: Case Studies and Testimonials**

*Category:* Document and Access Requests
*Question type:* long-text

*Context shown to Renee:*
We do not have any client case studies or testimonials on file. We know the AI Assistant has documented client savings of $40K to $50K per year, but no formal case study has been written.

*Question:*
Are there any client testimonials, results data, or before-and-after examples you can share, even informally? Even an email from a happy client would help.

*Skip allowed:* yes

---

**Card 13: GLC Org Chart**

*Category:* Document and Access Requests
*Question type:* file-upload

*Context shown to Renee:*
We have rebuilt the GLC org structure from your business plan but have not seen a visual org chart. Jeff Cohn referenced a ChatGPT-generated org chart on March 12 that was never shared. With Jeff's departure, the structure has shifted.

*Question:*
Do you have an updated org chart you can upload, or paste a Google Drive link to the one Jeff originally created?

*Skip allowed:* yes

---

**Card 14: Tools List Confirmation**

*Category:* Document and Access Requests
*Question type:* multi-select

*Context shown to Renee:*
Tools we have on file across the GLC ecosystem: GHL, Vendasta, AppFolio, InvestNext, Stripe, NMI, QuickBooks, Zoom Enterprise, Google Workspace, Slack, ClickUp, Claude, Zapier, WordPress on Cloudways, Magcloud, Kisi.

*Question:*
Which of these are you keeping, and which should we mark as deprecated? Tap any that should NOT be in your stack going forward.

*Options:*
- Vendasta (deprecating)
- Zapier (deprecating, moving to N8N)
- Wix (if applicable)
- WordPress on Cloudways (if migrating to Astro)
- Magcloud (if Something Good Magazine is being paused)
- Kisi (if shared spaces are being deprioritized)

*Skip allowed:* yes

---

### Category: Decisions (5 cards)

---

**Card 15: Operator Hire Timeline**

*Category:* Decisions
*Question type:* single-select

*Context shown to Renee:*
The Operations Team has confirmed that an operator hire below you is the critical path for everything else. Without that role filled, you remain the bottleneck for marketing, vendor coordination, and client delivery. Mark has indicated a preference for a US-based hire, and Rebecca's team will write the role criteria.

*Question:*
What is your honest current expectation on when you can move on this hire?

*Options:*
- Within 30 days
- Within 60 days
- Within 90 days
- Longer than 90 days
- I need help structuring this before I can commit

*Skip allowed:* no

---

**Card 16: Doug Documents Validation**

*Category:* Decisions
*Question type:* multi-select

*Context shown to Renee:*
The prior team's project sheet lists these items as Complete under Doug. We need to know which ones are actually current and which should be retired or replaced.

*Question:*
Which of these still reflect how you operate today? Tap any that are still in use.

*Options:*
- Initial funnel concept mapping
- Funnel architecture mapping
- Messaging framework drafts
- Unified Vrly business agreement
- Formal sales handoff SOP
- Client onboarding document
- Client offboarding document
- First 90 days at Vrly document

*Skip allowed:* yes

---

**Card 17: Logan Introduction Status**

*Category:* Decisions
*Question type:* single-select

*Context shown to Renee:*
You agreed on the Axiolo call to introduce Logan Boyce to Gabriel directly. This is the gate before Axiolo can coordinate creative and landing pages with Logan on active campaigns.

*Question:*
Where are you with the Logan introduction?

*Options:*
- Done, intro made
- Will do this week
- Will do next week
- Need Tom to draft the intro for me

*Skip allowed:* no

---

**Card 18: Axiolo Part 1 Approval**

*Category:* Decisions
*Question type:* multi-select

*Context shown to Renee:*
Gabriel has sent the proposal with Part 1 (Unblocking Vrly) and Part 2 (Ongoing Services). Part 1 is itemized so you can pick what to move on now.

*Question:*
Which Part 1 items would you like to move forward on this week? Tap any that you are ready to approve.

*Options:*
- TopForm onboarding coordination (Included)
- Aksarben Mortgage 2 funnel landing pages ($2,000)
- Vrly Content Engine landing page ($750)
- Vrly Media second landing page ($750)
- Vrly Foundation landing page ($750)
- Sammy's Superheroes email deliverability diagnostic ($500)
- Vrly Media website rebuild Astro ($3,500)
- Zapier to N8N migration ($1,500)
- Vrly-branded email setup (Included)

*Skip allowed:* no

---

**Card 19: Anything Else We Should Know**

*Category:* Decisions
*Question type:* long-text

*Context shown to Renee:*
We have covered the items currently on our radar. If something is on your mind that did not show up in these cards, this is where to put it.

*Question:*
Is there anything else we should know, decide on, or unblock for you?

*Skip allowed:* yes

---

## 6. Database Schema (Supabase)

### Tables

**clients**
- `id` uuid primary key, default `gen_random_uuid()`
- `name` text not null (e.g. "Renee Mueller")
- `org_name` text (e.g. "Vrly Media / Good Life Capital")
- `engagement_name` text (e.g. "GLC Engagement v1")
- `token` text not null unique (32+ char random string used in URL)
- `created_at` timestamptz default now()
- `last_active_at` timestamptz nullable

**cards**
- `id` uuid primary key, default `gen_random_uuid()`
- `client_id` uuid not null, references `clients(id)` on delete cascade
- `order_index` integer not null (display order within the client's deck)
- `category` text not null (e.g. "Client Review", "Document and Access Requests", "Decisions")
- `title` text not null
- `context` text not null
- `question` text not null
- `response_type` text not null (one of: confirm-edit, single-select, multi-select, short-text, long-text, file-upload, document-link, contact-share)
- `options` jsonb (for select types)
- `default_value` text (the existing text shown for confirm-edit cards)
- `skip_allowed` boolean default true
- `created_at` timestamptz default now()

**responses**
- `id` uuid primary key, default `gen_random_uuid()`
- `card_id` uuid not null, references `cards(id)` on delete cascade
- `client_id` uuid not null, references `clients(id)` on delete cascade
- `state` text not null (one of: not_started, viewed, answered, skipped, needs_edit)
- `response_value` jsonb (the actual answer, structure depends on response_type)
- `viewed_at` timestamptz nullable
- `answered_at` timestamptz nullable
- `created_at` timestamptz default now()
- `updated_at` timestamptz default now()
- unique constraint on (`card_id`, `client_id`)

**uploads**
- `id` uuid primary key, default `gen_random_uuid()`
- `card_id` uuid not null, references `cards(id)` on delete cascade
- `client_id` uuid not null, references `clients(id)` on delete cascade
- `file_name` text not null
- `file_size_bytes` integer not null
- `storage_path` text not null (path in Supabase storage bucket)
- `mime_type` text
- `uploaded_at` timestamptz default now()

### Storage Buckets

- `pulse-uploads`, private bucket for all client file uploads
- File path convention: `{client_id}/{card_id}/{uuid}-{filename}`

### Row Level Security

For v1, the application uses the Supabase service role key from a serverless function or build-time environment. RLS is enabled on all tables but bypassed via service role for authenticated operations. Public access to the database is blocked.

A simpler v1 approach: use Supabase's anon key with RLS policies that allow read and write only when a valid token is in the request. For v1, the token-in-URL flow can be implemented as a custom JWT or a simpler client-side header that the API validates.

Recommended for v1: client-side token in URL → exchange for a short-lived Supabase session via a serverless edge function → use that session for all queries. This avoids exposing service role keys in the browser.

### Seed Data

The 19 cards in Section 5 are seed data. Build a `seed.sql` or `seed.ts` file that inserts Renee's client record and all 19 cards on initial deployment.

---

## 7. Frontend Specification

### Architecture

- Single-page application (SPA) using vanilla JavaScript, or a lightweight framework (Svelte or Astro recommended for performance and simplicity)
- Static build deployed to GitHub Pages
- All Supabase calls happen client-side via the JavaScript client
- Token captured from URL on load and stored in `sessionStorage` for the session
- Mobile-first responsive design, looks great on a phone first, desktop second

### URL Pattern

`https://pulse.igtms.com/?t={token}`

If `pulse.igtms.com` is not yet pointed at GitHub Pages, fallback URL:
`https://tomdigati.github.io/pulse/?t={token}`

### App Flow

1. User opens link
2. App reads `t` parameter from URL
3. App queries Supabase for the client record matching the token
4. If found, app loads all cards for that client and any existing responses
5. App displays the first unanswered card
6. User taps through cards
7. Each response auto-saves to Supabase as it is submitted
8. Progress indicator shows X of Y completed
9. On the final card, user sees a "Thank you, Tom will follow up" screen

### Card UI Pattern

Each card is full-screen on mobile. The structure is:

```
+--------------------------------+
|  [Pulse logo]      [3 of 19]   |
|                                |
|  Category: Client Review       |
|                                |
|  Card Title                    |
|  ----------------------------  |
|  Context paragraph here.       |
|  Two to four sentences.        |
|  Maximum 60 to 80 words.       |
|                                |
|  Question text here?           |
|                                |
|  [Response interface]          |
|                                |
|  [   Confirm   ]               |
|  [   Edit      ]               |
|  [   Skip      ]               |
+--------------------------------+
```

For confirm-edit cards, the three buttons stack vertically on mobile. Confirm is the primary action (filled green button). Edit is secondary (outlined). Skip is tertiary (text link).

### Visual Design

- Background: `#F7F7F7` (light gray) for the page
- Card surface: `#FFFFFF` with a subtle shadow
- Card border-radius: 12px
- Padding inside card: 24px on mobile, 32px on desktop
- Typography:
  - Card title: Poppins SemiBold 600, 22px on mobile, 28px on desktop
  - Category label: Poppins Medium 500, 11px, uppercase, letter-spacing 1px, color `#07926B`
  - Context: Poppins Regular 400, 15px, line-height 1.6, color `#3B373B`
  - Question: Poppins SemiBold 600, 17px, color `#3B373B`
  - Buttons: Poppins SemiBold 600, 16px
- Primary button: background `#07926B`, white text, border-radius 8px, padding 14px 24px, full width on mobile
- Secondary button: white background, `#07926B` border 1.5px, `#07926B` text
- Tertiary link: no background, `#777777` text, underline on hover

### Save and Resume

- Every response is saved to Supabase the moment the user submits it
- On page load, the app fetches the user's existing responses and skips ahead to the first unanswered card
- A "Resume where you left off" message shows briefly on return visits
- A small progress indicator (e.g. "3 of 19" or "16% complete") is visible at the top

### File Upload UX

- User taps the upload area (large dashed border, 120px tall, says "Tap to upload or paste link")
- Native file picker opens on mobile (iOS will show camera, photos, files options)
- File uploads stream to Supabase storage with a progress bar
- After upload, file appears as a chip with name and size, with an X to remove
- Up to 5 files per card
- Max 25MB per file

### Empty States and Errors

- If the URL has no token: show "This link is missing a code. Please check the link your consultant sent you."
- If the token is invalid: show "We could not find your engagement. Please check the link or contact Tom."
- If the network fails on save: show a banner "Could not save just now. We will retry automatically." Retry every 10 seconds until success.
- If the user closes the app mid-card without submitting: state is `viewed` and they can pick up there next time.

---

## 8. Tom's Admin View

A separate page at `/admin` that requires a password (set via environment variable). It shows:

### Engagement List
A simple table:
- Client name
- Org name
- Engagement name
- Cards completed / total
- Last active timestamp
- Action buttons: View Responses, Copy Link, Rotate Token

### Response Detail View
For a selected client, show all cards in order with:
- Card title and category
- The user's response (formatted appropriately for the response type)
- Timestamp of response
- Any uploaded files (with download links)
- Skipped cards clearly marked

### Export Format
A "Copy as Markdown" button that produces a clipboard-ready markdown summary of all responses, formatted to paste directly into a ClickUp comment or doc.

### Admin Authentication
For v1, a single shared password for Tom is sufficient. Stored as `ADMIN_PASSWORD` in environment variables. Validated client-side against a hash stored in the build, OR validated via a Supabase edge function. The simpler v1 approach is fine.

---

## 9. Tech Stack and Tools

### Required
- **Frontend framework:** Vanilla JavaScript or Astro (recommended for static-first builds with islands of interactivity). Svelte or React are also acceptable if the engineer prefers.
- **Database and storage:** Supabase (free tier is sufficient for v1)
- **Hosting:** GitHub Pages
- **Domain:** `pulse.igtms.com` (DNS to be configured separately, fallback to `tomdigati.github.io/pulse/` for v1)
- **Build tooling:** Whatever the framework requires, Vite for Astro/Svelte, etc.
- **Package manager:** npm or pnpm

### Environment Variables
- `SUPABASE_URL`, Supabase project URL
- `SUPABASE_ANON_KEY`, Supabase anon public key
- `ADMIN_PASSWORD`, password for Tom's admin view (or hash thereof)

### Dependencies (suggested)
- `@supabase/supabase-js`, Supabase client
- A markdown export library if needed (or write a small one inline)

### What NOT to Use
- No backend server, all logic is client-side or Supabase edge functions
- No authentication library (Auth0, Firebase Auth, etc.), token-in-URL is the auth model
- No analytics SDKs in v1, privacy first
- No third-party UI component libraries, use vanilla CSS or Tailwind if preferred

---

## 10. Deployment Plan

### Initial Setup

1. Clone `tomdigati/pulse` locally
2. Initialize the project structure (frontend, package.json, README)
3. Create a Supabase project named "pulse-prod" (or "pulse-dev" first if preferred)
4. Run the schema SQL to create tables
5. Run the seed SQL to populate Renee's cards
6. Add Supabase URL and anon key to `.env.local` and to GitHub repo secrets
7. Build the frontend to a `dist/` folder
8. Configure GitHub Pages to deploy from a `gh-pages` branch or from `/dist`
9. Set up GitHub Actions to build and deploy on push to `main`

### Custom Domain (Optional, Post-v1)

1. In GitHub Pages settings, add `pulse.igtms.com` as the custom domain
2. In the IGTMS DNS provider, add a CNAME record pointing `pulse.igtms.com` to `tomdigati.github.io`
3. Wait for DNS propagation
4. Confirm HTTPS is enabled

### Renee's Onboarding

1. Generate Renee's record in the database with a unique token
2. Construct her URL: `https://pulse.igtms.com/?t={token}`
3. Tom sends the URL via text message with a short framing note
4. Renee opens the link, taps through cards over multiple sessions
5. Tom monitors the admin view as responses come in
6. Tom exports responses to ClickUp using the "Copy as Markdown" button

---

## 11. Implementation Order

Follow this sequence. Do not skip ahead. Confirm each milestone before moving to the next.

### Milestone 1: Project Initialization
- Initialize the framework (Astro recommended)
- Set up the Supabase project
- Run the database schema
- Insert seed data for Renee's client record and all 19 cards
- Confirm: I can query Supabase and see the cards.

### Milestone 2: Token Authentication and Card Loading
- Build the token capture from URL
- Build the Supabase client and fetch the client record by token
- Fetch all cards for that client and any existing responses
- Display card 1 as a static page (no interactivity yet)
- Confirm: opening the URL with a valid token shows card 1 with the correct content.

### Milestone 3: Card Interaction and Response Saving
- Implement the confirm-edit response type fully
- Save responses to Supabase on submit
- Move to the next card on submit
- Show progress indicator
- Confirm: I can tap through 5 cards and see responses in the Supabase responses table.

### Milestone 4: All Response Types
- Implement single-select, multi-select, short-text, long-text, document-link, contact-share
- Implement file-upload with Supabase storage integration
- Confirm: every response type works on mobile and desktop, and uploads land in Supabase storage.

### Milestone 5: Save and Resume Flow
- On page load, skip ahead to the first unanswered card
- Show resume message
- Persist progress across sessions and devices
- Confirm: I can close the browser at card 5 and return on a different device to card 5.

### Milestone 6: IGTMS Brand Polish
- Apply full IGTMS brand system (Poppins, color palette, spacing, button styles)
- Mobile-first layout polish
- Tap-target sizing (minimum 44px)
- Smooth transitions between cards
- Empty states and error states
- Confirm: the app feels like a finished IGTMS product on a phone.

### Milestone 7: Admin View
- Build `/admin` route with password gate
- Engagement list table
- Response detail view per client
- "Copy as Markdown" export button
- Confirm: I can log into admin, see Renee's responses, and copy them to ClickUp format.

### Milestone 8: Production Deployment
- Configure GitHub Actions for auto-build and deploy
- Generate Renee's production token
- Construct her URL
- Test the full flow end-to-end on a real phone with the production link
- Confirm: Renee's URL works and saves responses to production Supabase.

### Milestone 9: Send to Renee
- Tom sends the URL with a short text
- Monitor responses as they arrive
- Iterate on cards based on Renee's actual usage

---

## 12. v1 Success Criteria

Pulse v1 is successful if all of the following are true:

1. Renee opens her link on her phone in under 5 seconds
2. She taps through at least 10 cards in a single session of under 5 minutes
3. She returns to the app at least once and resumes where she left off
4. She uploads at least one document successfully from her phone
5. Her responses are visible to Tom in the admin view in real time
6. Tom exports her responses to ClickUp using the markdown export
7. Renee gives positive feedback on the experience (verbatim or implicit)
8. The product feels like an IGTMS product, not a generic survey tool

If all eight criteria are met, Pulse v1 ships and the platform is validated. From there, additional clients can be onboarded by creating new client records and unique tokens.

---

## 13. Future Enhancements (Out of Scope for v1)

These are noted for context but are explicitly not part of the v1 build:

- Card creation and editing through the admin UI (v1 cards are seeded in code)
- Multi-engagement support per client (v1 is one engagement per token)
- Email or SMS notifications when responses are submitted
- Collaborative responses (multiple stakeholders per client)
- Conditional card logic (if X then Y)
- Analytics and engagement metrics
- White-labeling for other consultants beyond IGTMS
- Custom branding per engagement

These will be considered after v1 ships and Pulse is validated with Renee.

---

## 14. Configuration Decisions (Locked)

These decisions are confirmed by Tom and locked for v1. Implement against these specifications.

### 14.1 Production URL

For v1, the production URL is the GitHub Pages default:

```
https://tomdigati.github.io/pulse/
```

Custom domain (`pulse.igtms.com`) is deferred to a future phase. No domain purchase or DNS configuration is needed for v1.

The application must work correctly under the GitHub Pages URL pattern, including any subpath routing required by Pages defaults.

### 14.2 Admin Authentication

The admin view uses a single shared password stored as the `ADMIN_PASSWORD` environment variable. No multi-user auth, no Supabase auth users, no password reset flow. Tom is the only operator in v1.

Implementation pattern:

- Password (or hash thereof) committed in build-time environment config
- Single `/admin` login screen with password field only
- On successful login, set a session flag in `sessionStorage`
- Session expires when the browser tab closes
- No "remember me" or persistent sessions in v1

### 14.3 ClickUp Export Format

The "Copy as Markdown" button on a client's response detail view should produce a clipboard-ready markdown block formatted to update an existing ClickUp task description. The export format is:

```
# {Card Title}

**Status:** {recommended_status}

## Response from {Client Name}
{Renee's response in plain markdown body text}

## Original Context
{The card's original context paragraph}

## Original Question
{The card's original question}

---
```

One block per card, separated by `---` rules. Each block uses H1 for the card title, H2 for the response and reference sections, and plain body for the response content.

The recommended status is selected automatically based on response state and content:

| Response Pattern | Recommended Status |
|---|---|
| User confirmed without edit | `IGTMS Review` |
| User edited the existing content | `IGTMS Review` |
| User uploaded a file | `IGTMS Review` |
| User skipped a card | `Waiting on Good Life` |
| User said they need help (single-select option) | `Needs Attention` |
| User indicated a blocker | `Blocked` |
| Card has not been viewed yet | `Waiting on Good Life` |
| User selected "Done" or "Approved" option | `Approved` |
| User indicated all items in scope are complete | `Complete` |

The full set of valid status values for the export:

- `Waiting on IGTMS`
- `Waiting on Good Life`
- `Needs Attention`
- `IGTMS Review`
- `Client Review`
- `Blocked`
- `Approved`
- `Complete`

The admin view should also allow Tom to override the recommended status from a dropdown before copying, in case the automatic suggestion is wrong for a specific response.

### 14.4 Timestamps

All timestamps shown in the admin view are displayed in Tom's local timezone. The application detects the operator's timezone from the browser using `Intl.DateTimeFormat().resolvedOptions().timeZone` and formats accordingly.

Stored timestamps in Supabase remain in UTC. Conversion happens at display time, not at storage time.

Timestamp format in the admin view:

```
April 23, 2026 at 4:15 PM CDT
```

Or relative format for recent activity:

```
2 hours ago
```

Use the relative format for anything within the last 24 hours, and the absolute format with timezone for anything older.

### 14.5 Production Deployment SLA

There is no formal SLA. Tom owns the entire product end to end. Deployment happens when Tom is satisfied with the build. No external timeline pressure exists.

This is an in-house tool for IGTMS use. No customer commitments, no uptime guarantees, no support agreements. If the app breaks, Tom fixes it on his own schedule.

### 14.6 Hosting and Infrastructure Ownership

Tom owns the entire stack:

- GitHub repository: `tomdigati/pulse` (personal)
- Supabase project: created and owned by Tom
- GitHub Pages: deployed from Tom's repository
- Domain: not configured for v1, will use GitHub Pages default URL

No third-party dependencies on Axiolo infrastructure for hosting. Axiolo's role is engineering capability where needed, not ownership of the deployed product.

---

## End of Specification

This document is the source of truth for Pulse v1. Update this file as decisions evolve. All implementation should reference this specification.

For questions, contact Tom DiGati directly.

*Pulse by IGTMS. Decisions, not paperwork.*
