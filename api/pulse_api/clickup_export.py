"""Server-side ports of `src/lib/status-suggest.ts` and the body builder
from `src/lib/markdown-export.ts`.

Pulse historically built ClickUp markdown blocks on the client (the
operator clicked "Copy as Markdown" and pasted into ClickUp). The push
flow runs server-side, so we need the same logic in Python. A parity
fixture (`api/tests/fixtures/clickup_markdown_blocks.json`) holds
known-good outputs captured from the TypeScript version; the unit test
asserts the Python port matches byte-for-byte.

If you change a body shape here, change the matching TS file AND
regenerate the fixture in the same commit. The fixture is the contract.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

STATUS_VALUES = (
    "Waiting on Axiolo",
    "Waiting on Good Life",
    "Needs Attention",
    "Axiolo Review",
    "Client Review",
    "Blocked",
    "Approved",
    "Complete",
)

_NEEDS_HELP = re.compile(r"(need .*help|i need help|cannot|can not|unable)", re.IGNORECASE)
_BLOCKED = re.compile(r"(blocked|stuck|waiting on)", re.IGNORECASE)
_DONE_OPTION = re.compile(r"(done|approved|complete|in use|in place)", re.IGNORECASE)


def suggest_status(card: dict, response: dict | None) -> str:
    """Mirrors `suggestStatus(card, response)` in status-suggest.ts."""
    if response is None:
        return "Waiting on Good Life"
    state = response.get("state")
    if state in ("not_started", "viewed", "skipped"):
        return "Waiting on Good Life"

    v: dict[str, Any] = response.get("response_value") or {}
    response_type = card["response_type"]

    if response_type == "confirm-edit":
        return "Axiolo Review"

    if response_type == "single-select":
        sel = v.get("selected") or ""
        if _NEEDS_HELP.search(sel):
            return "Needs Attention"
        if _BLOCKED.search(sel):
            return "Blocked"
        if _DONE_OPTION.search(sel):
            return "Approved"
        return "Axiolo Review"

    if response_type == "multi-select":
        arr = v.get("selected") if isinstance(v.get("selected"), list) else []
        if not arr:
            return "Waiting on Good Life"
        if any(_NEEDS_HELP.search(s) for s in arr):
            return "Needs Attention"
        if any(_BLOCKED.search(s) for s in arr):
            return "Blocked"
        return "Axiolo Review"

    if response_type in ("short-text", "long-text"):
        text = v.get("text") or ""
        if _NEEDS_HELP.search(text):
            return "Needs Attention"
        if _BLOCKED.search(text):
            return "Blocked"
        return "Axiolo Review"

    if response_type == "document-link":
        return "Axiolo Review" if v.get("url") else "Waiting on Good Life"

    if response_type == "contact-share":
        return "Axiolo Review" if v.get("email") else "Waiting on Good Life"

    if response_type == "file-upload":
        ids = v.get("file_ids") or []
        return "Axiolo Review" if ids else "Waiting on Good Life"

    return "Axiolo Review"


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def render_response_body(
    card: dict,
    response: dict | None,
    uploads: Iterable[dict],
) -> str:
    """Mirrors `renderResponseBody` from markdown-export.ts. `uploads` is
    a sequence of `{name, sizeBytes}` dicts (or {file_name, file_size_bytes};
    we read both shapes)."""
    if response is None or response.get("state") == "not_started":
        return "_Not yet viewed._"
    if response.get("state") == "viewed":
        return "_Card opened, no response yet._"

    v: dict[str, Any] = response.get("response_value") or {}
    note = v.get("note")
    note_suffix = f"\n\n**Note:** {note}" if note else ""

    if response.get("state") == "skipped":
        return f"_Skipped._{note_suffix}" if note else "_Skipped._"

    response_type = card["response_type"]
    body: str

    if response_type == "confirm-edit":
        if v.get("confirmed"):
            body = "Confirmed as written."
        else:
            correction = v.get("correction") or ""
            quoted = "\n".join(f"> {line}" for line in correction.split("\n"))
            body = f"Edited:\n\n{quoted}"

    elif response_type == "single-select":
        body = f"**{v.get('selected') or ''}**"

    elif response_type == "multi-select":
        arr = v.get("selected") if isinstance(v.get("selected"), list) else []
        body = "_None selected._" if not arr else "\n".join(f"- {s}" for s in arr)

    elif response_type in ("short-text", "long-text"):
        body = v.get("text") or ""

    elif response_type == "document-link":
        url = v.get("url")
        body = f"<{url}>" if url else ""

    elif response_type == "contact-share":
        parts: list[str] = []
        name = v.get("name") or ""
        role = v.get("role")
        if name:
            parts.append(f"**{name}**" + (f" ({role})" if role else ""))
        elif role:
            parts.append(f"** ({role})")
        email = v.get("email")
        if email:
            parts.append(email)
        body = "\n".join(parts)

    elif response_type == "file-upload":
        ups = list(uploads)
        if not ups:
            body = "_No files uploaded._"
        else:
            lines = []
            for u in ups:
                fname = u.get("name") or u.get("file_name") or ""
                size = u.get("sizeBytes") if "sizeBytes" in u else u.get("file_size_bytes", 0)
                lines.append(f"- `{fname}` ({_format_bytes(int(size))})")
            list_block = "\n".join(lines)
            body = (
                f"**Files attached ({len(ups)}):**\n{list_block}\n\n"
                "_Files live in the Pulse admin. Search for the file names above to locate them in your local archive._"
            )

    else:
        body = ""

    return body + note_suffix


def render_card_markdown(
    *, card: dict, client: dict, response: dict | None, status: str, uploads: Iterable[dict]
) -> str:
    """Mirrors `renderCardMarkdown` from markdown-export.ts. Caller joins
    multiple blocks with `\\n`; each block ends with `---\\n`."""
    body = render_response_body(card, response, uploads)
    return "\n".join([
        f"# {card['title']}",
        "",
        f"**Status:** {status}",
        "",
        f"## Response from {client['name']}",
        body,
        "",
        "## Original Context",
        card["context"],
        "",
        "## Original Question",
        card["question"],
        "",
        "---",
        "",
    ])
