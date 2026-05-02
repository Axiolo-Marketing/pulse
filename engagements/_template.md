# <Client name> · <Engagement name>

**Status:** Drafting / Active / Complete / Paused
**URL:** *(filled in after admin → New engagement, or rotated)*
**Sent:** *(date)*
**Cards:** *(N)*

---

## 1. Client profile

**Name:** <full name>
**Role and org:** <e.g. President, Good Life Capital | CEO, Vrly Media>
**How we met:** <one or two sentences>

### Behavioral profile

How does this client move? What drives the deck design?

- Mobile-first or desktop-first?
- Tappable or willing to type?
- Voice (iOS keyboard mic) likely?
- Time-starved? Specific time windows when they're reachable?
- Numbers-comfortable, or does dyscalculia / number anxiety apply?
- Comfortable saying "I don't know" or do they default to non-answers?
- Communication style: direct? layered? deferential?
- Anything else relevant — language, time zone, vision/hearing, attention rhythms

### Representative quote

> *(a real message or transcript snippet that lets a reader hear them)*

### What this means for the deck

- Card order (highest leverage first? warm-up cards?)
- Tone (which words to use, which to avoid)
- Response types to favor (lots of confirm-edit? heavier on file-upload?)
- Skip policy (most skippable, or several required?)
- Active references? Anything we should pre-build as HTML?

---

## 2. Engagement context

What is this engagement trying to validate, unblock, or align? What documents / transcripts informed the deck?

- Source material (transcripts, business plans, prior work)
- Open items as of <date>
- Decisions we're trying to surface
- Anything we deliberately did not include and why

---

## 3. The card deck

| # | Title | Type | Skip | Notes |
|---|---|---|---|---|
| 1 | … | confirm-edit | required | … |
| 2 | … | … | … | … |

Full card content (titles, contexts, questions, options, defaults) lives in the database. Author cards via the admin "+ Add card" flow, or via SQL if you prefer batch authoring. See `supabase/seed.sql` for the Renee pattern.

---

## 4. Active References

Any HTML deliverables for this engagement, dropped into `pulse/public/deliverables/`:

- `<slug>.html` — wired to Card N (e.g. "GLC Org Chart" → Card 13)

---

## 5. Operations log

Running notes during the engagement.

- **YYYY-MM-DD** — Sent the URL via SMS. Renee replied 12 minutes later with an ICP correction.
- **YYYY-MM-DD** — Edited Card 4 wording per her feedback.
- **YYYY-MM-DD** — Exported all to ClickUp under task `<id>`.

---

## 6. Handoff

When the engagement is complete:

- [ ] All non-skippable cards answered
- [ ] Responses exported to ClickUp
- [ ] HTML deliverables archived (or moved out of `public/deliverables/` if no longer needed)
- [ ] Token rotated or revoked if access should end

What landed (a sentence or two on outcomes — what changed because of this engagement).
