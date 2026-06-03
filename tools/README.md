# tools/

Operator tooling that talks to a running Pulse instance over the admin API.
Unlike the obsolete top-level `scripts/` (pre-Supabase-migration, safe to delete),
nothing here touches the database directly — it all goes through `/api/admin/*`,
so RLS and request validation stay intact.

## apply-deck.py

Push a [pulse-qa](../.claude/skills/pulse-qa.md) deck markdown straight to the
live portal. It logs in as the operator, creates the engagement, and adds every
card. When it finishes, the deck is live and it prints the client's magic URL.

Zero dependencies — stdlib Python 3 only. No Docker, no `pip install`.

### Usage

```bash
export PULSE_ADMIN_EMAIL='tom@axiolo.com'
export PULSE_ADMIN_PASSWORD='…'        # your operator login (must be email-verified)

# 1. Always dry-run first: parses + validates, makes zero network calls.
python3 tools/apply-deck.py deck.md --dry-run

# 2. Apply for real against production (https://pulse.axiolo.com by default).
python3 tools/apply-deck.py deck.md

# Options
python3 tools/apply-deck.py deck.md --org 'Acme Co'        # set org_name
python3 tools/apply-deck.py deck.md --base-url http://localhost:14321  # local dev
python3 tools/apply-deck.py deck.md --force                # allow a same-named 2nd engagement
```

### What it expects

The deck must be in the canonical authoring format the pulse-qa skill emits:
a header block (`**Engagement:**`, `**Client:**`, …) followed by `## Card N:`
sections with `**Category:** / **Type:** / **Skip:** / **Context:** / **Question:**`
(and `**Options:**` for select types, `**Attachment:**` when wiring a deliverable).
Trailing `## Sequencing summary` / `## Notes for Tom` sections are ignored.

### Safety

- **Dry-run by default in your head:** run `--dry-run` first. It catches placeholder
  client names, select cards missing options, bad response types, and empty fields
  before anything hits the portal.
- **Duplicate guard:** refuses to create a second engagement with an
  `engagement_name` that already exists, unless you pass `--force`.
- **Auth:** uses the same email+password session as `/admin/`. The account must be
  `is_admin = true` and email-verified. OAuth-only accounts can't be used by the
  script (no password) — set a password via the portal's forgot-password flow if needed.
- Credentials are read from env (`PULSE_ADMIN_EMAIL` / `PULSE_ADMIN_PASSWORD`); the
  script never stores them.
