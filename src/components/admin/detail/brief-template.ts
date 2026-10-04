/** Starter outline pre-filled into an empty brief editor so operators begin
 * from a structure instead of a blank box. Plain Markdown. */
export const BRIEF_TEMPLATE = `# <Client name> · <Engagement name>

**Status:** Drafting / Active / Complete / Paused
**URL:** *(pulled from Copy link above)*
**Sent:** *(date)*
**Cards:** *(N)*

---

## 1. Client profile

**Name:** <full name>
**Role and org:**
**How we met:**

### Behavioral profile
- Mobile-first or desktop-first?
- Tappable, willing to type, voice-friendly?
- Time-starved? Specific time windows when they're reachable?
- Numbers-comfortable, or does dyscalculia / number anxiety apply?
- Communication style: direct? layered?
- Any other quirks: language, time zone, vision, attention rhythms.

### Representative quote
> *(a real message or transcript snippet so anyone reading can hear them)*

### What this means for the deck
- Card order
- Tone (which words to use, which to avoid)
- Response types to favor
- Skip policy

---

## 2. Engagement context

What this engagement is trying to validate, unblock, or align.

- Source material (transcripts, business plans, prior work)
- Open items
- Decisions we're trying to surface

---

## 3. The card deck

| # | Title | Type | Skip |
|---|---|---|---|
| 1 | … | confirm-edit | required |

---

## 4. Active References

Any HTML deliverables for this engagement. Drop files in \`public/deliverables/\` and wire them via the Edit form on each card.

---

## 5. Operations log

- **YYYY-MM-DD** — sent the link, …

---

## 6. Handoff

- [ ] All required cards answered
- [ ] Responses exported to ClickUp
- [ ] Engagement deleted if access should end
`;
