"""Parse the Pulse-card authoring markdown format into card payloads.

The format is documented in the `pulse-card-creator` skill — heading-
delimited card blocks like:

    ## Card 1: <Title>

    **Category:** Confirm What We Know
    **Type:** confirm-edit
    **Skip:** required
    **Attachment:** deliverables/foo.html

    **Context:**
    Two to four sentences of what we already believe.

    **Question:**
    One question.

    **Options:**
    - option one
    - option two

The parser ignores any non-card top-level sections (engagement header,
sequencing summary, notes for Tom). Validation errors include the card
index + title so the operator can fix the source file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

VALID_TYPES = {
    "confirm-edit",
    "single-select",
    "multi-select",
    "short-text",
    "long-text",
    "file-upload",
    "document-link",
    "contact-share",
}

SELECT_TYPES = {"single-select", "multi-select"}


class CardImportError(ValueError):
    """One or more cards failed to parse or validate."""

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or [message]


@dataclass
class ParsedCard:
    title: str
    category: str
    response_type: str
    context: str
    question: str
    skip_allowed: bool
    options: list[str] | None
    attachment_path: str | None

    def to_create_kwargs(self) -> dict:
        return {
            "category": self.category,
            "title": self.title,
            "context": self.context,
            "question": self.question,
            "response_type": self.response_type,
            "options": self.options,
            "default_value": None,
            "skip_allowed": self.skip_allowed,
            "attachment_path": self.attachment_path,
        }


_CARD_HEADING = re.compile(r"^##\s+Card\s+\d+\s*:\s*(.+?)\s*$", re.IGNORECASE)
_OTHER_HEADING = re.compile(r"^##\s+")
_LABEL = re.compile(r"^\*\*([A-Za-z][A-Za-z _-]*):\*\*\s*(.*?)\s*$")
_OPTION_LINE = re.compile(r"^[-*]\s+(.+?)\s*$")

_INLINE_FIELDS = {"category", "type", "skip", "attachment"}
_BLOCK_FIELDS = {"context", "question", "options"}


def parse_markdown(markdown: str) -> list[ParsedCard]:
    """Split the markdown into card blocks and parse each. Raises
    CardImportError with the list of issues if any block is invalid."""
    blocks = _split_into_card_blocks(markdown)
    if not blocks:
        raise CardImportError("no cards found in markdown")

    parsed: list[ParsedCard] = []
    errors: list[str] = []
    for idx, (title, body) in enumerate(blocks, start=1):
        try:
            parsed.append(_parse_card_block(title, body))
        except CardImportError as exc:
            errors.append(f"card {idx} ({title!r}): {exc}")

    if errors:
        raise CardImportError(
            f"{len(errors)} card(s) failed to parse", errors=errors
        )
    return parsed


def _split_into_card_blocks(markdown: str) -> list[tuple[str, str]]:
    """Return (title, body) pairs, one per `## Card N:` heading. Any other
    `##` heading (e.g. `## Sequencing summary`, `## Notes for Tom`) ends
    the current block and is ignored."""
    blocks: list[tuple[str, str]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        card_match = _CARD_HEADING.match(line)
        if card_match:
            if current_title is not None:
                blocks.append((current_title, "\n".join(current_lines).strip()))
            current_title = card_match.group(1).strip()
            current_lines = []
            continue
        if _OTHER_HEADING.match(line) and current_title is not None:
            blocks.append((current_title, "\n".join(current_lines).strip()))
            current_title = None
            current_lines = []
            continue
        if current_title is not None:
            current_lines.append(line)

    if current_title is not None:
        blocks.append((current_title, "\n".join(current_lines).strip()))

    return blocks


def _parse_card_block(title: str, body: str) -> ParsedCard:
    """Parse a single card body into a ParsedCard. Inline fields
    (Category/Type/Skip/Attachment) are one-liners; block fields
    (Context/Question/Options) span until the next labeled field."""
    if not title:
        raise CardImportError("missing title in heading")

    fields = _extract_fields(body)
    category = fields.get("category", "").strip()
    type_value = fields.get("type", "").strip().lower()
    skip_value = fields.get("skip", "").strip().lower()
    attachment = fields.get("attachment", "").strip() or None
    context = fields.get("context", "").strip()
    question = _normalize_question(fields.get("question", ""))
    options_raw = fields.get("options", "").strip()

    missing = [
        name
        for name, val in [
            ("Category", category),
            ("Type", type_value),
            ("Skip", skip_value),
            ("Context", context),
            ("Question", question),
        ]
        if not val
    ]
    if missing:
        raise CardImportError(f"missing fields: {', '.join(missing)}")

    if type_value not in VALID_TYPES:
        raise CardImportError(
            f"invalid Type {type_value!r}; expected one of "
            f"{', '.join(sorted(VALID_TYPES))}"
        )

    if skip_value not in {"required", "optional"}:
        raise CardImportError(
            f"invalid Skip {skip_value!r}; expected 'required' or 'optional'"
        )
    skip_allowed = skip_value == "optional"

    options: list[str] | None = None
    if type_value in SELECT_TYPES:
        options = _parse_options(options_raw)
        if not options:
            raise CardImportError(
                f"Type {type_value!r} requires an Options list of 2 or more items"
            )
        if len(options) < 2:
            raise CardImportError(
                f"Type {type_value!r} requires at least 2 options"
            )
    elif options_raw:
        # Options on a non-select type is a likely mistake; reject so the
        # operator notices rather than silently dropping the options.
        raise CardImportError(
            f"Options provided but Type {type_value!r} doesn't take options"
        )

    return ParsedCard(
        title=title,
        category=category,
        response_type=type_value,
        context=context,
        question=question,
        skip_allowed=skip_allowed,
        options=options,
        attachment_path=attachment,
    )


def _extract_fields(body: str) -> dict[str, str]:
    """Walk the lines, accumulating field name → value. Inline fields end
    on the same line; block fields run until the next labeled field."""
    fields: dict[str, str] = {}
    current_field: str | None = None
    current_value: list[str] = []

    def flush() -> None:
        if current_field is not None:
            fields[current_field] = "\n".join(current_value).strip()

    for line in body.splitlines():
        m = _LABEL.match(line)
        if m:
            flush()
            name = m.group(1).strip().lower()
            inline_value = m.group(2).strip()
            if name in _INLINE_FIELDS:
                fields[name] = inline_value
                current_field = None
                current_value = []
            elif name in _BLOCK_FIELDS:
                current_field = name
                current_value = [inline_value] if inline_value else []
            else:
                # Unknown label — accept it as inline (so the parser is
                # forward-compatible with new fields the skill might add)
                # but don't surface it through the API.
                fields[name] = inline_value
                current_field = None
                current_value = []
            continue
        if current_field is not None:
            current_value.append(line)

    flush()
    return fields


def _parse_options(raw: str) -> list[str]:
    options: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _OPTION_LINE.match(stripped)
        if m:
            options.append(m.group(1).strip())
    return options


def _normalize_question(raw: str) -> str:
    """Questions are one-liners. Collapse internal whitespace so a
    line-wrapped question in the source becomes a single string."""
    return " ".join(raw.split()).strip()
