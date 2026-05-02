# Renee Mueller · GLC Engagement v1

**Status:** Active
**URL:** `https://tomdigati.github.io/pulse/?t=7e3937c0be912149`
**Sent:** *(pending Tom's confirmation)*
**Cards:** 19

---

## 1. Client profile

**Name:** Renee Mueller
**Role and org:** President, Good Life Capital · CEO, Vrly Media
**How we met:** Existing IGTMS client, GLC ecosystem alignment work

### Behavioral profile

- **Mobile-first.** She lives on her phone between events, flights, and speaking engagements.
- **Time-starved.** Regularly misses meetings due to travel and double-booking. Apologizes often for delayed responses.
- **Has dyscalculia and flips numbers.** Numbers must be plain and large. Never bury figures in dense paragraphs.
- **Strong communicator.** Comfortable saying she doesn't know something. Prefers fast-moving sessions over polished documents she can't influence.
- **Exhausted but engaged.** Doing homework on her phone when she has 5 minutes.
- **Communicates by layering toward her point.** Mirror-and-confirm approach works best.

### Representative quote

> "I still have plenty to get you, working on it, but yes just trying to cover all the other bases as well. I apologize I didn't respond yesterday, it's been a little bit of a whirlwind with traveling, and we had the opening for an event last night, and speaking today, then done!"

### What this means for the deck

- Tappable, not type-heavy. Heavy on confirm-edit (8 cards) and file-upload (4) so she can move quickly.
- Sessions must be resumable. She'll start, get interrupted, pick up later from a different device. (Pulse supports this universally — flagging because it matters more for her than most.)
- Cognitive load per card minimal. One question per card.
- Auto-save on every action. She closes the browser without thinking about it.
- Tone warm and forgiving. No progress bars that scold. No reminders that nag.

---

## 2. Engagement context

GLC ecosystem alignment work. Validating service delivery, vendor relationships, ICP, and a handful of pending decisions before the next phase of work with IGTMS + Axiolo.

**Source material:**
- ClickUp export `Vrly_IGTMS_Data_Capture.txt`
- GLC Master Context document
- Prior team's project sheet (Doug, Jenn, Enrique, Megan)
- Recent business plan for GLC structure

**Open items prompting the deck:**
- Operator hire below Renee — critical path for everything else
- Logan Boyce intro to Gabriel (Axiolo) — gate for active campaign coordination
- Axiolo Part 1 itemized approval (multiple landing pages, deliverability fix, Astro rebuild)
- Vendasta migration audit before GHL build
- ICP confirmation — has shifted since the prior team

---

## 3. The card deck

Authored as the v1 reference deck and committed in `supabase/seed.sql`. 19 cards.

### Category: Client Review (8)

| # | Title | Type | Skip |
|---|---|---|---|
| 1 | Service Delivery Matrix | confirm-edit | optional |
| 2 | Sale to Fulfillment Process | long-text | optional |
| 3 | Ideal Client Profile (ICP) Confirmation | confirm-edit | optional |
| 4 | Current Services and Packages | confirm-edit | optional |
| 5 | Active Vendor List | confirm-edit | optional |
| 6 | CMO Responsibilities | confirm-edit | optional |
| 7 | Ownership of Delivery by Stages | long-text | optional |
| 8 | SLA or Service Guarantees | confirm-edit | optional |

### Category: Document and Access Requests (6)

| # | Title | Type | Skip |
|---|---|---|---|
| 9 | Vendasta Access | file-upload | optional |
| 10 | Website Admin Access | file-upload | optional |
| 11 | Pitch Decks and Brand Materials | file-upload | optional |
| 12 | Case Studies and Testimonials | long-text | optional |
| 13 | GLC Org Chart | file-upload | optional |
| 14 | Tools List Confirmation | multi-select | optional |

### Category: Decisions (5)

| # | Title | Type | Skip |
|---|---|---|---|
| 15 | Operator Hire Timeline | single-select | **required** |
| 16 | Doug Documents Validation | multi-select | optional |
| 17 | Logan Introduction Status | single-select | **required** |
| 18 | Axiolo Part 1 Approval | multi-select | **required** |
| 19 | Anything Else We Should Know | long-text | optional |

Full card content (contexts, questions, options) is in `supabase/seed.sql`. Edit via `/admin/` per card; re-run the seed via `scripts/apply-sql.mjs` if you want to reset to the file's text.

---

## 4. Active References

| File | Wired to |
|---|---|
| `deliverables/glc-org-chart.html` | Card 13 (GLC Org Chart) |
| `deliverables/vrly-icp-v1.html` | Card 3 (Ideal Client Profile Confirmation) |

Both are IGTMS-styled HTML files matching Poppins / green palette.

---

## 5. Operations log

- **2026-04-29** — First production deploy. Card 13 wording revised to remove all references to Jeff Cohn (retained business material kept, departure framing dropped).
- **2026-04-30** — Token rotated to a 16-hex-char form for cleaner SMS. Test client created at the same time so Tom could rehearse the flow without polluting Renee's data.
- **2026-04-30** — Vrly ICP HTML wired to Card 3.
- **2026-04-30** — Tom confirmed link works on his phone. Responses cleared to a fresh state in preparation for sending.

---

## 6. Handoff

- [ ] All non-skippable cards answered (Cards 15, 17, 18)
- [ ] Responses exported to ClickUp
- [ ] HTML deliverables archived if no longer relevant
- [ ] Token rotated or revoked if access should end
