-- Pulse v1 seed: Renee Mueller + 19 cards
-- Run after schema.sql. Idempotent: re-running updates card content in place.

-- ──────────────────────────────────────────────────────────────────────────
-- Renee's client record
--
-- Fixed UUID lets us reference it from the card inserts below without
-- needing a CTE or extra round-trip. The token is generated once on first
-- run via encode(gen_random_bytes(8), 'hex') — 16 hex chars (64 bits of
-- entropy), short enough to fit cleanly in an SMS. On re-run, the
-- on-conflict-do-nothing clause leaves the original token intact so
-- Renee's URL stays stable.
-- ──────────────────────────────────────────────────────────────────────────

insert into public.clients (id, name, org_name, engagement_name, token)
values (
  '00000000-0000-0000-0000-000000000001',
  'Renee Mueller',
  'Vrly Media / Good Life Capital',
  'GLC Engagement v1',
  encode(gen_random_bytes(8), 'hex')
)
on conflict (id) do nothing;

-- ──────────────────────────────────────────────────────────────────────────
-- 19 cards. on conflict (client_id, order_index) do update lets us iterate
-- on card copy by re-running this file.
-- ──────────────────────────────────────────────────────────────────────────

-- Helper: client_id for Renee
-- (inlined as the literal UUID in each insert below for clarity)

-- ─── Category: Client Review ──────────────────────────────────────────────

-- Card 1
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  1,
  'Client Review',
  'Service Delivery Matrix',
  $$We have reconstructed your current service delivery model from the prior team's project sheet. Content Engine is owned by Enrique on production with Doug on strategy and is currently stalled. Ads Management is active under Logan Boyce. The website is in Phase 5 with Jenn on development and Enrique on graphics. Something Good Magazine had Megan on content, Enrique on design, and Jenn on layout, with March and April issues in progress when the team departed. CRM and automation work is incomplete and returning to GHL. Vrly Foundation has Jenn on web and CRM and Logan on ads, with the landing page and donor capture incomplete.$$,
  $$Is this picture accurate? If anything has changed or was never quite right, please tell us where to update.$$,
  'confirm-edit',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 2
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  2,
  'Client Review',
  'Sale to Fulfillment Process',
  $$We understand that leads come in primarily through your personal network, the ERS sphere, and Logan's coaching network. Once a prospect is ready, you sell them on a package, MRR billing begins, and a 90-day onboarding model takes over. The prior team built an ads onboarding form in Typeform routed through Vendasta, and a 6-email onboarding series was planned but never completed. Today, website changes and landing page requests route through Vendasta to your inbox, which makes you the bottleneck at every stage.$$,
  $$What moves a client from "sold" to "in onboarding" today, now that the prior team is gone? If anything currently lives in your head or in undocumented habits, that is exactly what we want to capture here.$$,
  'long-text',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 3
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  3,
  'Client Review',
  'Ideal Client Profile (ICP) Confirmation',
  $$We have you serving the busy entrepreneur, SMB owner, $2M to $8M revenue, who knows they need marketing but cannot staff it full-time. They are also building toward an exit, succession, or scaling event in the next 5 to 10 years. Strong industry fits include Home Services, Legal, Medical, Professional Services, and Large Nonprofits eligible for Google Ad Grants. Approximately 75% of your current clients came through your personal network or the ERS sphere.$$,
  $$Does this match how you think about your ideal client today? If you would tighten or widen this anywhere, tell us.$$,
  'confirm-edit',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 4
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  4,
  'Client Review',
  'Current Services and Packages',
  $$We have six core packages on file: Vrly Content Engine (anchor product, fulfilled by Axiolo), Ads Management (Logan Boyce), AI Audit (entry point, IGTMS-led), AI Assistant (vendor TBD), Social Media Management (fulfilled by Axiolo, phasing toward Content Engine), and Crowdfunding Platform (fulfillment vendor TBD).$$,
  $$Are these still the active packages? If you have added, paused, or retired anything, tell us.$$,
  'confirm-edit',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 5
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  5,
  'Client Review',
  'Active Vendor List',
  $$Active vendors we have on record: Logan Boyce / Digital Tabby (ads), Axiolo (incoming white-label fulfillment), Vendasta (current platform, being replaced), Magcloud (Something Good Magazine distribution). Prior team vendors with unconfirmed status: Jenn, Enrique, Megan Cimple, Mark Fisher, Aadeck, Magnfi Team.$$,
  $$Is the active vendor list correct, and which of the prior team vendors still have any ongoing relationship with you?$$,
  'confirm-edit',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 6
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  6,
  'Client Review',
  'CMO Responsibilities',
  $$We have the CMO role at Vrly currently performed by you, with IGTMS providing fractional strategy. The role covers vendor and fulfillment oversight, content manager coordination with Logan, client communication and retention, package strategy and margin management, marketing for Vrly itself, and GLC ecosystem alignment. Rebecca's Operations Team handles back of house only.$$,
  $$Does this reflect the CMO function as you would describe it, or have we missed anything you consider part of this role?$$,
  'confirm-edit',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 7
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  7,
  'Client Review',
  'Ownership of Delivery by Stages',
  $$We have a six-stage model: Sale (you), Handoff to fulfillment (currently informal and you-dependent), Onboarding Days 1-30 (foundation and activation, owner unclear), Execution Days 31-60 (Axiolo and Logan), Optimization Days 61-90 (owner unclear), and Ongoing Post-90 (owner unclear).$$,
  $$For the stages where ownership is unclear, who do you currently expect to own each one? If the answer is "I do for now," that is fine to say.$$,
  'long-text',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 8
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  8,
  'Client Review',
  'SLA or Service Guarantees',
  $$The only SLA-adjacent language we have found is in your client onboarding document: billing continues even when client delays cause timeline slippage. We have not located any formal performance guarantee, turnaround commitment, or service-level agreement.$$,
  $$Are there any verbal or written commitments you have made to current clients that we should know about? Even informal ones count.$$,
  'confirm-edit',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- ─── Category: Document and Access Requests ───────────────────────────────

-- Card 9
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  9,
  'Document and Access Requests',
  'Vendasta Access',
  $$We need access to Vendasta to complete the migration audit before the GHL build begins. The Vendasta contract runs through August 2026, and we need to validate what the prior team built before anything is rebuilt. We also need to resolve the Sammy's Superheroes email deliverability issue.$$,
  $$Can you share Vendasta admin credentials or send a workspace invitation to Tom? You can upload a screenshot of the credentials, paste them in a follow-up text, or attach an invite confirmation here.$$,
  'file-upload',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 10
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  10,
  'Document and Access Requests',
  'Website Admin Access',
  $$The Vrly website is at vrlymultimedia.com on WordPress (Cloudways), with a target launch of May 20, 2026. We need admin access to assess the staging site before any rebuild work happens.$$,
  $$Please add the Axiolo team as admins or upload the WordPress credentials here. If easier, paste the WP login URL into a short text reply on the next card.$$,
  'file-upload',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 11
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  11,
  'Document and Access Requests',
  'Pitch Decks and Brand Materials',
  $$We have not received pitch decks, brand guidelines, or service one-pagers. We know website wireframes (by Jenn) exist and a brand guidelines consistency checklist was in progress.$$,
  $$Can you upload any pitch decks, brand assets, or service one-pagers you have? Or paste a Google Drive link below if it is easier.$$,
  'file-upload',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 12
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  12,
  'Document and Access Requests',
  'Case Studies and Testimonials',
  $$We do not have any client case studies or testimonials on file. We know the AI Assistant has documented client savings of $40K to $50K per year, but no formal case study has been written.$$,
  $$Are there any client testimonials, results data, or before-and-after examples you can share, even informally? Even an email from a happy client would help.$$,
  'long-text',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 13
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  13,
  'Document and Access Requests',
  'GLC Org Chart',
  $$We have rebuilt the GLC org structure from your business plan but have not seen a visual org chart. The structure has shifted recently, so we want to validate the current shape before anything depends on it.$$,
  $$Does the org chart we have on file match how you would describe the structure today? Notes, edits, or an updated chart upload all work.$$,
  'file-upload',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 14
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  14,
  'Document and Access Requests',
  'Tools List Confirmation',
  $$Tools we have on file across the GLC ecosystem: GHL, Vendasta, AppFolio, InvestNext, Stripe, NMI, QuickBooks, Zoom Enterprise, Google Workspace, Slack, ClickUp, Claude, Zapier, WordPress on Cloudways, Magcloud, Kisi.$$,
  $$Which of these are you keeping, and which should we mark as deprecated? Tap any that should NOT be in your stack going forward.$$,
  'multi-select',
  '[
    "Vendasta (deprecating)",
    "Zapier (deprecating, moving to N8N)",
    "Wix (if applicable)",
    "WordPress on Cloudways (if migrating to Astro)",
    "Magcloud (if Something Good Magazine is being paused)",
    "Kisi (if shared spaces are being deprioritized)"
  ]'::jsonb,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- ─── Category: Decisions ──────────────────────────────────────────────────

-- Card 15
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  15,
  'Decisions',
  'Operator Hire Timeline',
  $$The Operations Team has confirmed that an operator hire below you is the critical path for everything else. Without that role filled, you remain the bottleneck for marketing, vendor coordination, and client delivery. Mark has indicated a preference for a US-based hire, and Rebecca's team will write the role criteria.$$,
  $$What is your honest current expectation on when you can move on this hire?$$,
  'single-select',
  '[
    "Within 30 days",
    "Within 60 days",
    "Within 90 days",
    "Longer than 90 days",
    "I need help structuring this before I can commit"
  ]'::jsonb,
  false
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 16
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  16,
  'Decisions',
  'Doug Documents Validation',
  $$The prior team's project sheet lists these items as Complete under Doug. We need to know which ones are actually current and which should be retired or replaced.$$,
  $$Which of these still reflect how you operate today? Tap any that are still in use.$$,
  'multi-select',
  '[
    "Initial funnel concept mapping",
    "Funnel architecture mapping",
    "Messaging framework drafts",
    "Unified Vrly business agreement",
    "Formal sales handoff SOP",
    "Client onboarding document",
    "Client offboarding document",
    "First 90 days at Vrly document"
  ]'::jsonb,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 17
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  17,
  'Decisions',
  'Logan Introduction Status',
  $$You agreed on the Axiolo call to introduce Logan Boyce to Gabriel directly. This is the gate before Axiolo can coordinate creative and landing pages with Logan on active campaigns.$$,
  $$Where are you with the Logan introduction?$$,
  'single-select',
  '[
    "Done, intro made",
    "Will do this week",
    "Will do next week",
    "Need Tom to draft the intro for me"
  ]'::jsonb,
  false
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 18
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  18,
  'Decisions',
  'Axiolo Part 1 Approval',
  $$Gabriel has sent the proposal with Part 1 (Unblocking Vrly) and Part 2 (Ongoing Services). Part 1 is itemized so you can pick what to move on now.$$,
  $$Which Part 1 items would you like to move forward on this week? Tap any that you are ready to approve.$$,
  'multi-select',
  '[
    "TopForm onboarding coordination (Included)",
    "Aksarben Mortgage 2 funnel landing pages ($2,000)",
    "Vrly Content Engine landing page ($750)",
    "Vrly Media second landing page ($750)",
    "Vrly Foundation landing page ($750)",
    "Sammy''s Superheroes email deliverability diagnostic ($500)",
    "Vrly Media website rebuild Astro ($3,500)",
    "Zapier to N8N migration ($1,500)",
    "Vrly-branded email setup (Included)"
  ]'::jsonb,
  false
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- Card 19
insert into public.cards (client_id, order_index, category, title, context, question, response_type, options, skip_allowed)
values (
  '00000000-0000-0000-0000-000000000001',
  19,
  'Decisions',
  'Anything Else We Should Know',
  $$We have covered the items currently on our radar. If something is on your mind that did not show up in these cards, this is where to put it.$$,
  $$Is there anything else we should know, decide on, or unblock for you?$$,
  'long-text',
  null,
  true
)
on conflict (client_id, order_index) do update set
  category      = excluded.category,
  title         = excluded.title,
  context       = excluded.context,
  question      = excluded.question,
  response_type = excluded.response_type,
  options       = excluded.options,
  skip_allowed  = excluded.skip_allowed;

-- ──────────────────────────────────────────────────────────────────────────
-- Attachments
--
-- Reset all attachment_path values for Renee, then set explicit ones below.
-- This block is idempotent and self-contained: to add a new deliverable,
-- drop the file in pulse/public/deliverables/ and append a row here.
-- ──────────────────────────────────────────────────────────────────────────

update public.cards
set attachment_path = null
where client_id = '00000000-0000-0000-0000-000000000001';

update public.cards
set attachment_path = 'deliverables/glc-org-chart.html'
where client_id = '00000000-0000-0000-0000-000000000001'
  and order_index = 13;
