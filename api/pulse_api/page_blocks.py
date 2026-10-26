"""Page blocks: parse a client document's ``<pulse-*>`` elements into cards.

A web page (e.g. a QuickHost deliverable) marks the parts a respondent fills
in with custom elements. The embed script (``public/embed/pulse-blocks.js``)
turns them into inputs that save to Pulse; this module reads the same markup
server-side so syncing the page creates/updates one card per block.

Markup::

    <pulse-question id="monthly-budget" owner="Adam"
                    context="This tells us which departments you staff.">
      How much do you want to spend on marketing each month?
    </pulse-question>

    <pulse-grid id="ownership" title="Who owns each department">
      <pulse-row label="Marketing and sales lead"></pulse-row>
      <pulse-row label="Content and brand"></pulse-row>
      <pulse-col label="Does the work"></pulse-col>
      <pulse-col label="How it's staffed"
                 options="Not set yet|Your team|Outside help"></pulse-col>
    </pulse-grid>

* ``id`` (required): stable key, ``[a-z0-9][a-z0-9_-]*`` up to 64 chars,
  unique within the page. It ties a block to its card across re-syncs, so
  rewording a question keeps its answers.
* ``pulse-question``: the prompt is the ``prompt`` attribute, else the
  element's text. Optional ``title``, ``category``, ``context``, ``owner``.
  Stored as a ``long-text`` card (``{"text": …}``).
* ``pulse-grid``: rows and columns are child elements; a column with
  ``options`` (``|``-separated) is a dropdown, otherwise free text. Stored as
  a ``grid`` card with the rows/columns in ``block_config`` and answers as
  ``{"cells": {row_key: {col_key: value}}}``. Row/column keys are slugs of
  their labels unless a ``key`` attribute is given.

The parser only reads markup — it never fetches URLs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

BLOCK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_BLOCKS = 200
MAX_GRID_ROWS = 100
MAX_GRID_COLS = 20
MAX_OPTIONS = 30
MAX_CELL_CHARS = 1000
MAX_TEXT_CHARS = 20_000
_TITLE_CHARS = 120


class PageBlockError(ValueError):
    """The page's blocks are invalid. ``errors`` lists every problem."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s[:64] or "item"


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Block:
    block_id: str
    kind: str  # "question" | "grid"
    attrs: dict[str, str]
    text: str = ""
    rows: list[dict[str, str]] = field(default_factory=list)
    cols: list[dict[str, Any]] = field(default_factory=list)

    @property
    def response_type(self) -> str:
        return "long-text" if self.kind == "question" else "grid"

    def card_fields(self) -> dict[str, Any]:
        """Column values for the card this block maps to."""
        a = self.attrs
        prompt = _squash(a.get("prompt") or self.text) or a.get("title", "")
        title = _squash(a.get("title") or prompt)
        if len(title) > _TITLE_CHARS:
            title = title[: _TITLE_CHARS - 1].rstrip() + "…"
        context = _squash(a.get("context", ""))
        if a.get("owner"):
            context = f"For {_squash(a['owner'])}." + (f" {context}" if context else "")
        config = None
        if self.kind == "grid":
            config = {"rows": self.rows, "columns": self.cols}
        return {
            "category": _squash(a.get("category", "")) or "Page",
            "title": title or self.block_id,
            "context": context,
            "question": prompt or title or self.block_id,
            "response_type": self.response_type,
            "block_config": config,
        }


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.errors: list[str] = []
        self._current: Block | None = None
        self._depth = 0  # nesting depth inside the current block

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag in ("pulse-question", "pulse-grid"):
            if self._current is not None:
                self.errors.append(f"<{tag}> can't be nested inside another block")
                return
            kind = "question" if tag == "pulse-question" else "grid"
            self._current = Block(block_id=a.get("id", "").strip(), kind=kind, attrs=a)
            self._depth = 0
            return
        cur = self._current
        if cur is None:
            return
        self._depth += 1
        if cur.kind == "grid" and tag in ("pulse-row", "pulse-col"):
            label = _squash(a.get("label", ""))
            if not label:
                self.errors.append(f"grid {cur.block_id!r}: <{tag}> needs a label")
                return
            key = slug(a.get("key") or label)
            if tag == "pulse-row":
                cur.rows.append({"key": key, "label": label})
            else:
                opts = [o.strip() for o in a.get("options", "").split("|") if o.strip()]
                cur.cols.append({"key": key, "label": label, "options": opts or None})

    def handle_endtag(self, tag: str) -> None:
        cur = self._current
        if cur is None:
            return
        if tag in ("pulse-question", "pulse-grid"):
            self.blocks.append(cur)
            self._current = None
        else:
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._current.kind == "question":
            self._current.text += data


def parse_blocks(html: str) -> list[Block]:
    """Every ``<pulse-*>`` block in ``html``, in page order. Raises
    ``PageBlockError`` listing all problems; never returns a partial list."""
    p = _Parser()
    p.feed(html)
    p.close()
    errors = list(p.errors)
    if p._current is not None:
        errors.append(f"block {p._current.block_id!r} is never closed")
    seen: set[str] = set()
    for b in p.blocks:
        name = b.block_id or "(no id)"
        if not BLOCK_ID_RE.match(b.block_id):
            errors.append(
                f"block {name!r}: id must be lowercase letters, digits, - or _ "
                "(up to 64 characters)"
            )
        elif b.block_id in seen:
            errors.append(f"block id {b.block_id!r} is used twice")
        seen.add(b.block_id)
        if b.kind == "question" and not (_squash(b.attrs.get("prompt") or b.text)):
            errors.append(f"question {name!r} has no text")
        if b.kind == "grid":
            if not b.rows or not b.cols:
                errors.append(f"grid {name!r} needs at least one <pulse-row> and <pulse-col>")
            if len(b.rows) > MAX_GRID_ROWS or len(b.cols) > MAX_GRID_COLS:
                errors.append(f"grid {name!r} is too large")
            for kind, items in (("row", b.rows), ("column", b.cols)):
                keys = [i["key"] for i in items]
                if len(set(keys)) != len(keys):
                    errors.append(f"grid {name!r} has two {kind}s with the same label")
            for c in b.cols:
                if c["options"] and len(c["options"]) > MAX_OPTIONS:
                    errors.append(f"grid {name!r}: column {c['label']!r} has too many options")
    if len(p.blocks) > MAX_BLOCKS:
        errors.append(f"a page can have at most {MAX_BLOCKS} blocks")
    if not p.blocks and not errors:
        errors.append("no <pulse-question> or <pulse-grid> blocks found")
    if errors:
        raise PageBlockError(errors)
    return p.blocks


def clean_answer(card: dict[str, Any], value: Any) -> dict[str, Any] | None:
    """Validate an answer posted from a page for ``card``. Returns the
    ``response_value`` to store, or None when the answer is empty (the
    respondent cleared it). Raises ``ValueError`` on a malformed value."""
    if not isinstance(value, dict):
        raise ValueError("value must be an object")
    if card["response_type"] == "grid":
        cfg = card.get("block_config") or {}
        rows = {r["key"] for r in cfg.get("rows", [])}
        cols = {c["key"]: c.get("options") for c in cfg.get("columns", [])}
        cells_in = value.get("cells")
        if not isinstance(cells_in, dict):
            raise ValueError("grid value needs cells")
        cells: dict[str, dict[str, str]] = {}
        for rk, row in cells_in.items():
            if rk not in rows or not isinstance(row, dict):
                raise ValueError(f"unknown row {rk!r}")
            for ck, cell in row.items():
                if ck not in cols:
                    raise ValueError(f"unknown column {ck!r}")
                if not isinstance(cell, str):
                    raise ValueError("cell values must be text")
                cell = cell.strip()[:MAX_CELL_CHARS]
                if not cell:
                    continue
                opts = cols[ck]
                if opts and cell not in opts:
                    raise ValueError(f"{cell!r} isn't an option for {ck!r}")
                cells.setdefault(rk, {})[ck] = cell
        return {"cells": cells} if cells else None
    text = value.get("text")
    if not isinstance(text, str):
        raise ValueError("value needs text")
    text = text.strip()
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("answer is too long")
    return {"text": text} if text else None
