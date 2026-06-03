#!/usr/bin/env python3
"""apply-deck.py — push a pulse-card-creator deck straight to the live portal.

Takes a deck markdown file in the canonical pulse-qa authoring format
(.claude/skills/pulse-qa.md) and applies it to a running Pulse instance via
the admin API: it logs in as the operator, creates the engagement, and adds
every card. The moment it finishes, the deck is live at the printed URL.

No direct database access required — everything goes through the same
admin API the operator console uses, so RLS and validation stay intact.

Usage:
    export PULSE_ADMIN_EMAIL='tom@axiolo.com'
    export PULSE_ADMIN_PASSWORD='...'
    python3 tools/apply-deck.py path/to/deck.md
    python3 tools/apply-deck.py deck.md --dry-run        # parse + validate only
    python3 tools/apply-deck.py deck.md --org 'Acme Co'  # set org_name
    python3 tools/apply-deck.py deck.md --base-url http://localhost:14321

Defaults to the production portal (https://pulse.axiolo.com). Zero third-party
dependencies — stdlib only.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://pulse.axiolo.com"

# Must match CreateCardRequest.response_type in api/pulse_api/routes/admin_api.py
RESPONSE_TYPES = (
    "confirm-edit", "single-select", "multi-select", "short-text",
    "long-text", "file-upload", "document-link", "contact-share",
)
SELECT_TYPES = ("single-select", "multi-select")

# Field labels that can appear inside a card block, used to slice values.
CARD_LABELS = ("Category", "Type", "Skip", "Attachment", "Context",
               "Question", "Options", "Default")

PLACEHOLDER_RE = re.compile(r"<[^>]+>")


class DeckError(Exception):
    """Parse/validation failure — message is operator-facing."""


# ── Parsing ─────────────────────────────────────────────────────────────────

def _header_field(text: str, label: str) -> str | None:
    """Pull a single-line `**Label:** value` field from the header block."""
    m = re.search(rf"^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _split_value_by_labels(block: str) -> dict[str, str]:
    """Slice a card block into {label: raw_value} by locating each `**Label:**`
    marker and taking the text up to the next marker. Robust to fields being
    inline (`**Type:** confirm-edit`) or on the following line(s)."""
    # Find every known label marker and its position.
    marks: list[tuple[int, int, str]] = []  # (start, end, label)
    for label in CARD_LABELS:
        for m in re.finditer(rf"\*\*{re.escape(label)}:\*\*", block):
            marks.append((m.start(), m.end(), label))
    marks.sort()
    values: dict[str, str] = {}
    for i, (_start, end, label) in enumerate(marks):
        next_start = marks[i + 1][0] if i + 1 < len(marks) else len(block)
        raw = block[end:next_start].strip()
        # First occurrence of a label wins; ignore stray repeats.
        values.setdefault(label, raw)
    return values


def _parse_options(raw: str) -> list[str]:
    opts = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith(("-", "*")):
            opts.append(line[1:].strip())
    return opts


def parse_deck(text: str) -> tuple[dict, list[dict]]:
    """Return (engagement, cards). Raises DeckError on a malformed deck."""
    card_heading = re.compile(r"^##\s+Card\s+\d+\s*:\s*(.+?)\s*$", re.MULTILINE)
    headings = list(card_heading.finditer(text))
    if not headings:
        raise DeckError("No cards found. Expected '## Card N: <Title>' headings.")

    header = text[: headings[0].start()]
    engagement_name = _header_field(header, "Engagement")
    client_name = _header_field(header, "Client")
    org_name = _header_field(header, "Org")  # rarely present; --org overrides
    if not engagement_name:
        raise DeckError("Header is missing '**Engagement:**'.")
    if not client_name:
        raise DeckError("Header is missing '**Client:**'.")

    engagement = {
        "engagement_name": engagement_name,
        "name": client_name,
        "org_name": org_name,
        "recipient": _header_field(header, "Recipient"),
    }

    cards: list[dict] = []
    for i, h in enumerate(headings):
        title = h.group(1).strip()
        # Block runs from after this heading to the next h2 (any), so trailing
        # sections like "## Sequencing summary" / "## Notes for Tom" are excluded.
        block_start = h.end()
        next_h2 = re.search(r"^##\s", text[block_start:], re.MULTILINE)
        block_end = block_start + next_h2.start() if next_h2 else len(text)
        block = text[block_start:block_end]

        fields = _split_value_by_labels(block)
        rtype = (fields.get("Type") or "").strip().lower()
        skip_raw = (fields.get("Skip") or "optional").strip().lower()
        attachment = (fields.get("Attachment") or "").strip() or None
        if attachment and PLACEHOLDER_RE.search(attachment):
            attachment = None  # template placeholder, not a real path

        card = {
            "n": i + 1,
            "title": title,
            "category": (fields.get("Category") or "").strip(),
            "context": (fields.get("Context") or "").strip(),
            "question": (fields.get("Question") or "").strip(),
            "response_type": rtype,
            "options": _parse_options(fields.get("Options", "")) or None,
            "skip_allowed": "required" not in skip_raw,
            "attachment_path": attachment,
        }
        cards.append(card)
    return engagement, cards


# ── Validation ──────────────────────────────────────────────────────────────

def validate(engagement: dict, cards: list[dict]) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []

    if PLACEHOLDER_RE.search(engagement["name"]):
        errors.append(
            f"Client name still has a placeholder: {engagement['name']!r}. "
            "Fill in the real recipient before applying."
        )

    for c in cards:
        where = f"Card {c['n']} ({c['title'] or 'untitled'})"
        if not c["title"] or len(c["title"]) > 300:
            errors.append(f"{where}: title missing or >300 chars.")
        if not c["category"] or len(c["category"]) > 100:
            errors.append(f"{where}: category missing or >100 chars.")
        if not c["context"]:
            errors.append(f"{where}: missing Context.")
        if not c["question"]:
            errors.append(f"{where}: missing Question.")
        if c["response_type"] not in RESPONSE_TYPES:
            errors.append(
                f"{where}: Type {c['response_type']!r} is not one of "
                f"{', '.join(RESPONSE_TYPES)}."
            )
        if c["response_type"] in SELECT_TYPES:
            if not c["options"] or len(c["options"]) < 2:
                errors.append(f"{where}: {c['response_type']} needs 2+ Options.")
        elif c["options"]:
            warnings.append(f"{where}: Options ignored for type {c['response_type']}.")
        for fld in ("context", "question"):
            if PLACEHOLDER_RE.search(c[fld]):
                warnings.append(f"{where}: {fld} still contains a <placeholder>.")

    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    return errors


# ── HTTP ────────────────────────────────────────────────────────────────────

class PulseClient:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except Exception:
                pass
            raise DeckError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise DeckError(f"Cannot reach {url}: {e.reason}") from None

    def login(self, email: str, password: str) -> None:
        self._request("POST", "/api/auth/login",
                      {"email": email, "password": password})

    def list_clients(self) -> list[dict]:
        return self._request("GET", "/api/admin/clients")

    def create_client(self, name: str, org_name: str | None,
                      engagement_name: str | None) -> dict:
        return self._request("POST", "/api/admin/clients", {
            "name": name, "org_name": org_name, "engagement_name": engagement_name,
        })

    def create_card(self, client_id: str, card: dict) -> dict:
        body = {
            "category": card["category"],
            "title": card["title"],
            "context": card["context"],
            "question": card["question"],
            "response_type": card["response_type"],
            "options": card["options"],
            "skip_allowed": card["skip_allowed"],
            "attachment_path": card["attachment_path"],
        }
        return self._request("POST", f"/api/admin/clients/{client_id}/cards", body)


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Apply a Pulse deck markdown to the live portal.")
    ap.add_argument("deck", help="Path to the deck markdown file.")
    ap.add_argument("--base-url", default=os.environ.get("PULSE_BASE_URL", DEFAULT_BASE_URL),
                    help=f"Portal base URL (default {DEFAULT_BASE_URL}).")
    ap.add_argument("--org", default=None, help="org_name for the engagement (optional).")
    ap.add_argument("--email", default=os.environ.get("PULSE_ADMIN_EMAIL"))
    ap.add_argument("--password", default=os.environ.get("PULSE_ADMIN_PASSWORD"))
    ap.add_argument("--dry-run", action="store_true", help="Parse + validate only; no network calls.")
    ap.add_argument("--force", action="store_true", help="Create even if an engagement with the same name exists.")
    args = ap.parse_args()

    try:
        with open(args.deck, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"error: cannot read {args.deck}: {e}", file=sys.stderr)
        return 1

    try:
        engagement, cards = parse_deck(text)
    except DeckError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.org:
        engagement["org_name"] = args.org

    print(f"Parsed deck: {engagement['engagement_name']!r}")
    print(f"  Client:     {engagement['name']}")
    print(f"  Cards:      {len(cards)} "
          f"({sum(not c['skip_allowed'] for c in cards)} required)")

    errors = validate(engagement, cards)
    if errors:
        print(f"\n{len(errors)} validation error(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("  Validation: passed")

    if args.dry_run:
        print("\n--dry-run: not applying. Card summary:")
        for c in cards:
            req = "required" if not c["skip_allowed"] else "optional"
            print(f"  {c['n']:>2}. [{c['response_type']:<14}] {c['title']}  ({req})")
        return 0

    if not args.email or not args.password:
        print("error: set PULSE_ADMIN_EMAIL and PULSE_ADMIN_PASSWORD "
              "(or pass --email/--password).", file=sys.stderr)
        return 1

    client = PulseClient(args.base_url)
    try:
        client.login(args.email, args.password)
        print(f"\nLogged in to {args.base_url} as {args.email}.")

        if not args.force:
            existing = client.list_clients()
            dupe = [c for c in existing
                    if (c.get("engagement_name") or "").strip().lower()
                    == engagement["engagement_name"].strip().lower()]
            if dupe:
                print(f"error: an engagement named "
                      f"{engagement['engagement_name']!r} already exists "
                      f"(id {dupe[0].get('id')}). Re-run with --force to create "
                      "a second one.", file=sys.stderr)
                return 1

        row = client.create_client(
            name=engagement["name"],
            org_name=engagement["org_name"],
            engagement_name=engagement["engagement_name"],
        )
        client_id, token = row["id"], row.get("token")
        print(f"Created engagement {client_id}.")

        for c in cards:
            client.create_card(client_id, c)
            print(f"  + card {c['n']:>2}/{len(cards)}: {c['title']}")
    except DeckError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        print("(Engagement may be partially created — check /admin/ and delete "
              "it before retrying, or fix the deck and use --force.)", file=sys.stderr)
        return 1

    live = f"{args.base_url}/?t={token}" if token else "(token not returned)"
    print(f"\nDone. {len(cards)} cards live.")
    print(f"  Live URL:  {live}")
    print(f"  Admin:     {args.base_url}/admin/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
