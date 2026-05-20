---
name: pulse-qa
description: |
  Use this skill when the user is drafting a Pulse card deck for a client engagement,
  converting an existing artifact (ICP draft, sales playbook, engagement plan, brand
  brief, org chart, etc.) into a deck of confirm-or-correct cards, or extracting
  pending items / [TBD] / open questions / unconfirmed assumptions from a document so
  they can be sent to a client for validation via Pulse.

  Triggers include phrases like: "draft a Pulse deck for...", "build cards for the
  <client> engagement", "convert these pending items into cards", "turn this ICP into
  a Pulse review", "what should the deck look like for...", "I have a draft with
  open items, make it into cards", "Pulse Q&A for <client>".

  The skill loads the canonical authoring format (markdown shape Pulse expects),
  the response-type decision tree, the Axiolo voice rules, the sequencing pattern,
  and a worked example. The output should be a single markdown document ready to
  hand to the operator (Tom) for direct apply to the database.
---

# Pulse Q&A Authoring Skill

## What Pulse is (one paragraph)

Pulse is a mobile-first review tool for time-starved decision-makers. The operator
(Tom DiGati at Axiolo) sends a single private URL to a client. The client taps
through a deck of pre-populated cards confirming, correcting, or skipping each
one. The deck shifts the cognitive load from production to confirmation: Tom
has already done the work; the client just validates it. Cards cover what we
already believe, what documents we need, and what decisions only the client
can make.

## The card model

Each card has these fields:

| Field | Required | Notes |
|---|---|---|
| **title** | yes | Short, plain language. The card heading. |
| **category** | yes | A label for grouping (e.g. "Confirm What We Know", "Documents and Access", "Decisions for <Client>"). Pick whatever taxonomy fits the engagement. |
| **type** | yes | One of: `confirm-edit`, `single-select`, `multi-select`, `short-text`, `long-text`, `file-upload`, `document-link`, `contact-share`. Cannot be changed after a card is created — choose carefully. |
| **context** | yes | 2-4 sentences (max 60-80 words). What we already believe to be true. The client should be able to read it in 15 seconds. |
| **question** | yes | One question. Never compound. The specific decision or input. |
| **options** | for select types only | A list of 2-9 options for single/multi-select. Mutually exclusive for single, "any that apply" for multi. |
| **skip allowed** | yes | `required` (must be answered before the deck is complete) or `optional` (client can skip). Most cards are optional. Make a card required only if Month 1 work blocks without it. |
| **attachment** | optional | Path to an HTML reference file under `public/deliverables/<slug>.html`. Renders a "View Active Reference" button on the card. Use when a visual artifact (org chart, ICP one-pager, MRI report) helps the client confirm. |

`default_value` exists in the schema but is intentionally **not** surfaced to the client. The Needs edit textarea opens blank. Don't include it in the markdown.

## Response type decision tree

When you have a pending item to turn into a card, walk this tree top to bottom:

1. **Is the client supplying a file?** → `file-upload`. Use for documents, exports, screenshots, decks, recordings. Up to 5 files, 25MB each.
2. **Is the client supplying a URL to a shared resource?** → `document-link`. Use for Drive folders, Google Docs, Notion pages.
3. **Is the client supplying contact info for a person?** → `contact-share`. Three fields (name, email, role/context). Use for "share the credentials owner" or "introduce me to your CFO" patterns. **Never** ask for passwords inline.
4. **Is the answer one short structured value (a name, title, ID, single number)?** → `short-text`.
5. **Is the answer free-form prose (process explanation, narrative, "anything else we should know")?** → `long-text`.
6. **Is the answer one of a discrete set of choices?**
   - **Mutually exclusive (pick one)** → `single-select` with 2-5 options.
   - **Several can apply (pick any)** → `multi-select` with 2-9 options.
7. **Otherwise: we already believe something specific is true and want the client to confirm or push back** → `confirm-edit`. This is the most common type. The client sees the context, taps "Yes, correct" / "Needs edit" / "Skip for now". Tapping Edit opens a blank textarea for free-form correction.

If you find yourself drafting a `confirm-edit` card with a paragraph in the context that ends with "right?" — you've got it. That's the shape.

## Sequencing pattern

The deck order matters. Pulse clients are time-starved and engaged in bursts. Sequence so:

1. **Warm-up confirms first.** Cards that take 5 seconds to tap "Yes, correct" build momentum. Engagement frame, primary goal, anything they already saw in the contract.
2. **Document and access requests in the middle.** By now they've answered a few cards and feel the rhythm. File uploads, credentials handoffs, and database exports go here.
3. **Hard decisions at the end.** By card 15 they're in flow. Save the ones requiring real thought (timing commitments, vendor approvals, named introductions) for last.

Required cards (`skip: required`) are the ones that block downstream work. Use sparingly: ideally 30-50% of the deck. The rest can be skipped and circled back in the first weekly session. A deck where every card is required reads as homework, not a review.

## Voice and tone (Axiolo brand)

- **Direct but warm.** "We have rebuilt..." > "Please verify that we have rebuilt..."
- **Use "you" and "your"**, not "the client" or the recipient's name in the question.
- **No em-dashes anywhere.** Use commas, parentheses, periods, or pipe characters. This is a hard rule across Axiolo deliverables.
- **Avoid "audit", "accountability", "compliance"** — these read transactional and pressuring. Use "review", "calibration", "validation" instead.
- **Mirror the client's language** when known. If they said "lane 2" on a transcript, use "lane 2" in the card.
- **Numbers plain and large.** Don't bury figures in dense paragraphs. "$600K" not "approximately six hundred thousand dollars". (Some clients have dyscalculia; even those who don't appreciate clarity.)
- **One question per card.** If you wrote "and" in the question, it's two cards.

## Extracting cards from existing artifacts

Most decks are built from artifacts that already exist (ICP draft, sales playbook, engagement plan, transcript notes). Walk the artifact and look for these patterns:

| Pattern in the artifact | Card type | Example |
|---|---|---|
| `[TBD]`, `[INSERT X]`, `<placeholder>`, "TODO" markers | `short-text` (if structured), `confirm-edit` (if needs validation) | `[INSERT CIVIL MESH LEGAL NAME]` → short-text "What is Civil Mesh's full legal entity name and state of incorporation?" |
| Hedged statements: "we believe", "we think", "our assumption is", "X is currently..." | `confirm-edit` | "We believe Renee is the primary seller" → confirm-edit "Is Renee as primary seller still the plan?" |
| Open questions in the doc | `single-select` if there are listed alternatives, `short-text` if one-word answer, `long-text` if open-ended | "When can we hire?" with options 30/60/90 days → single-select |
| References to documents we don't have | `file-upload` | "Attach the proposal templates" → file-upload "Upload the proposal documents you currently use" |
| References to URLs we don't have | `document-link` | "Link to the Drive folder" → document-link |
| Names of people we need to engage | `contact-share` | "Introduce us to your CFO" → contact-share |
| Lists of options ("A or B or C") | `single-select` (pick one) or `multi-select` (pick any) | "Texas, Florida, or Northeast first?" → single-select |
| Meta question: "Is there anything else?" | `long-text` | Always end the deck with one of these, optional skip |

A useful exercise: open the artifact, mark every place you'd write "[confirm with client]" if you were leaving notes for yourself. Each mark is a card.

## The markdown format Pulse expects

Output your deck as a single markdown document with this shape. The operator (Tom) hands it directly to me (Claude in his other session) for SQL apply, or to a future loader script.

```markdown
# <Engagement title>

**Engagement:** <exact engagement_name as it appears in the admin>
**Client:** <client full name>
**Recipient:** <who taps the link, e.g. CEO's name. Same as Client most of the time.>
**Operator:** Tom DiGati, Axiolo Client Transformation Lead
**Card count:** <N>
**Categories:** <Category 1> (<n>), <Category 2> (<n>), <Category 3> (<n>)
**Source material:** <transcript names, dates, prior artifacts>

---

## Card 1: <Title>

**Category:** <Category 1>
**Type:** confirm-edit
**Skip:** required

**Context:**
<2-4 sentences. What we already believe.>

**Question:**
<One question.>

---

## Card 2: <Title>

**Category:** <Category 1>
**Type:** single-select
**Skip:** optional

**Context:**
<...>

**Question:**
<...>

**Options:**
- option one
- option two
- option three

---

## Card 3: <Title>

**Category:** <Category 2>
**Type:** file-upload
**Skip:** optional
**Attachment:** deliverables/<slug>.html

**Context:**
<...>

**Question:**
<...>

---

(continue for each card)

---

## Sequencing summary

| Card | Category | Type | Required |
|---|---|---|---|
| 1. <Title> | <Cat> | confirm-edit | yes |
| ... |

**Required: <X> of <N>.**

---

## Notes for Tom

1. Anything the operator should know before send. Sequencing rationale, attachments to wire up later, status mappings to override, time estimate for the client.
```

The header block is required. The categories field can be informal (e.g., "Confirm What We Know (12), Documents and Access (7), Decisions for Luke (3)").

Cards are separated by `---` rules and start with `## Card N: <Title>` exactly. The loader (or Claude) splits on this pattern.

Field labels are in bold (`**Category:**`, `**Type:**`, etc.). Text after the label until the next blank line or labeled field is the value.

For `confirm-edit` cards, do **not** include a `**Default:**` field — Pulse no longer pre-fills the edit textarea, so any default text is just dead weight in the output.

## Worked example: extracting cards from an ICP draft

Suppose the operator has a draft ICP for a new client "Acme Co" that reads:

> The Acme Ideal Client Profile targets [SEGMENT TBD] companies with $5-20M revenue,
> located primarily in [GEOGRAPHIES TBD], and decision-maker is typically the [ROLE TBD].
> We believe the buying trigger is regulatory pressure, but Acme has flagged that
> tech debt may be a stronger signal, needs validation. Pricing tier candidates:
> Lite ($2K/mo), Standard ($5K/mo), Enterprise (custom). [Acme to pick which to lead with.]
> Anchor case study: TBD, Acme to provide the strongest reference customer with
> permission to be named. We need the SOW template and 12-month account list.

Pending items extracted:

1. **Segment** — `[TBD]` → confirm-edit, but needs the client to write it. Better as `short-text` if a single segment name, or `long-text` if a short profile. Use **short-text**.
2. **Geographies** — `[TBD]` → typically more than one. **multi-select** with state/region options if we have a candidate list, or **short-text** if open-ended.
3. **Decision-maker role** — `[TBD]` → **short-text**.
4. **Buying trigger (regulatory vs tech debt)** — hedged + listed alternatives. **single-select** with: regulatory pressure / tech debt / both equally / something else (add a note).
5. **Pricing tier to lead with** — listed options + decision. **single-select** with three named tiers + a "hold both, lead with a different account first" escape hatch.
6. **Anchor case study customer** — needs a contact and permission. **contact-share** (name, email, role/context).
7. **SOW template** — file we don't have. **file-upload**.
8. **12-month account list** — file we don't have. **file-upload**.

Resulting deck (abbreviated):

```markdown
# Acme Co · Axiolo x Acme, GTM Calibration

**Engagement:** Axiolo x Acme, GTM Calibration
**Client:** <Acme contact name>
**Card count:** 8
**Categories:** Confirm What We Know (4), Documents and Access (2), Decisions for Acme (2)

---

## Card 1: Buying Trigger

**Category:** Confirm What We Know
**Type:** single-select
**Skip:** required

**Context:**
We have you positioned around regulatory pressure as the primary buying trigger,
but you flagged on the second call that tech debt may be the stronger signal.
We want to lock this in before the messaging work in Month 2.

**Question:**
Which trigger should we lead with?

**Options:**
- Regulatory pressure
- Tech debt
- Both equally, audience-dependent
- Something else, add a note below

---

## Card 2: Target Segment

**Category:** Confirm What We Know
**Type:** short-text
**Skip:** required

**Context:**
The ICP draft has the segment as TBD. We need a one-line industry or vertical
to anchor the messaging and the prospect list.

**Question:**
What is the primary segment we should lead with?

---

(continue for cards 3-8: geographies, role, pricing tier, case study contact,
SOW upload, account list upload)

---

## Notes for Tom

1. The pricing tier card (5) is the highest-leverage decision. If Acme picks Enterprise,
   the case study card (6) becomes blocking for Month 2.
2. Wire up the ICP HTML once published. Card 2 (Target Segment) and Card 3 (Geographies)
   would both benefit from an attachment.
```

That's the shape. Concise, sequenced, voice-correct, ready to hand off.

## Common mistakes to avoid

- **Em-dashes.** They sneak in. Replace with commas, periods, parentheses, or pipes.
- **Compound questions.** "Are these the right segments and is the pricing accurate?" is two cards.
- **Long context paragraphs.** 4 sentences max. If you have more, the artifact is the place; the card is the gate.
- **Required-everything decks.** Most cards should be `optional`. Required is for true blockers only.
- **Asking for passwords or other secrets.** Use `contact-share` to get who owns the credentials, then handle access exchange securely off-channel.
- **Putting card text in `default_value`.** That field is no longer surfaced to clients. The "what we believe" goes in `context`.
- **Confusing the operator with the recipient.** The operator is always Tom. The recipient is the named client. The card voice addresses the recipient ("you").

## Output checklist

Before handing the deck to the operator, verify:

- [ ] Header block has Engagement (matches admin exactly), Client, Card count, Categories, Source material
- [ ] Every card has Category, Type, Skip, Context, Question
- [ ] Select-type cards have Options
- [ ] No em-dashes anywhere
- [ ] No compound questions
- [ ] Required cards are deliberate, not default
- [ ] Sequencing follows easy-confirms → access → hard-decisions
- [ ] One final `long-text` "Anything else we should know?" optional card at the end
- [ ] Sequencing summary table at the bottom
- [ ] Notes for Tom calling out: highest-leverage cards, attachments to wire up, status overrides, estimated completion time
