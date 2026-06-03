"""Unit tests for the Pulse-card markdown parser."""
from __future__ import annotations

import pytest

from pulse_api.card_import import CardImportError, parse_markdown


VALID_DECK = """\
# Acme Engagement

**Engagement:** Acme Kickoff
**Client:** Acme Co

---

## Card 1: Buying Trigger

**Category:** Confirm What We Know
**Type:** single-select
**Skip:** required

**Context:**
We have you positioned around regulatory pressure as the primary buying
trigger, but you flagged that tech debt may be the stronger signal.

**Question:**
Which trigger should we lead with?

**Options:**
- Regulatory pressure
- Tech debt
- Both equally
- Something else

---

## Card 2: Target Segment

**Category:** Confirm What We Know
**Type:** short-text
**Skip:** required

**Context:**
The ICP draft has the segment as TBD.

**Question:**
What is the primary segment we should lead with?

---

## Card 3: SOW Template

**Category:** Documents and Access
**Type:** file-upload
**Skip:** optional
**Attachment:** deliverables/sow-template.html

**Context:**
We need the current SOW template to align our deliverable wording.

**Question:**
Upload the SOW template you currently use.

---

## Sequencing summary

| Card | Type |
|---|---|
| 1 | single-select |
| 2 | short-text |
| 3 | file-upload |

---

## Notes for Tom

1. The pricing tier card is the highest-leverage decision.
"""


def test_parses_full_deck() -> None:
    cards = parse_markdown(VALID_DECK)
    assert len(cards) == 3

    c1 = cards[0]
    assert c1.title == "Buying Trigger"
    assert c1.category == "Confirm What We Know"
    assert c1.response_type == "single-select"
    assert c1.skip_allowed is False
    assert c1.options == [
        "Regulatory pressure",
        "Tech debt",
        "Both equally",
        "Something else",
    ]
    assert "regulatory pressure" in c1.context.lower()
    assert c1.question == "Which trigger should we lead with?"
    assert c1.attachment_path is None

    c3 = cards[2]
    assert c3.response_type == "file-upload"
    assert c3.skip_allowed is True
    assert c3.attachment_path == "deliverables/sow-template.html"
    assert c3.options is None


def test_ignores_sequencing_summary_and_notes() -> None:
    cards = parse_markdown(VALID_DECK)
    titles = [c.title for c in cards]
    assert "Sequencing summary" not in titles
    assert "Notes for Tom" not in titles


def test_zero_cards_raises() -> None:
    with pytest.raises(CardImportError) as exc:
        parse_markdown("# Just a header\n\nNo cards here.\n")
    assert "no cards" in str(exc.value).lower()


@pytest.mark.parametrize(
    "missing_label",
    ["Category", "Type", "Skip", "Context", "Question"],
)
def test_missing_required_field_reported(missing_label: str) -> None:
    full = """\
## Card 1: Test

**Category:** Cat
**Type:** short-text
**Skip:** optional

**Context:**
ctx

**Question:**
q?
"""
    bad = full.replace(f"**{missing_label}:**", "**Unused:**")
    with pytest.raises(CardImportError) as exc:
        parse_markdown(bad)
    assert missing_label in str(exc.value.errors[0])


def test_invalid_type_reported() -> None:
    md = """\
## Card 1: Test

**Category:** Cat
**Type:** rambling-essay
**Skip:** optional

**Context:** ctx

**Question:** q?
"""
    with pytest.raises(CardImportError) as exc:
        parse_markdown(md)
    assert "invalid Type" in exc.value.errors[0]


def test_invalid_skip_reported() -> None:
    md = """\
## Card 1: Test

**Category:** Cat
**Type:** short-text
**Skip:** yes please

**Context:** ctx

**Question:** q?
"""
    with pytest.raises(CardImportError) as exc:
        parse_markdown(md)
    assert "Skip" in exc.value.errors[0]


def test_select_without_options_reported() -> None:
    md = """\
## Card 1: Test

**Category:** Cat
**Type:** single-select
**Skip:** optional

**Context:** ctx

**Question:** q?
"""
    with pytest.raises(CardImportError) as exc:
        parse_markdown(md)
    assert "Options" in exc.value.errors[0]


def test_select_with_one_option_reported() -> None:
    md = """\
## Card 1: Test

**Category:** Cat
**Type:** multi-select
**Skip:** optional

**Context:** ctx

**Question:** q?

**Options:**
- only one
"""
    with pytest.raises(CardImportError) as exc:
        parse_markdown(md)
    assert "2" in exc.value.errors[0]


def test_options_on_non_select_reported() -> None:
    md = """\
## Card 1: Test

**Category:** Cat
**Type:** short-text
**Skip:** optional

**Context:** ctx

**Question:** q?

**Options:**
- nope
- still nope
"""
    with pytest.raises(CardImportError) as exc:
        parse_markdown(md)
    assert "doesn't take options" in exc.value.errors[0]


def test_multiple_errors_collected() -> None:
    md = """\
## Card 1: Bad Type

**Category:** Cat
**Type:** wrong
**Skip:** optional

**Context:** ctx

**Question:** q?

---

## Card 2: Bad Skip

**Category:** Cat
**Type:** short-text
**Skip:** sure

**Context:** ctx

**Question:** q?
"""
    with pytest.raises(CardImportError) as exc:
        parse_markdown(md)
    assert len(exc.value.errors) == 2
    assert "card 1" in exc.value.errors[0]
    assert "card 2" in exc.value.errors[1]


def test_question_collapses_line_wraps() -> None:
    md = """\
## Card 1: Test

**Category:** Cat
**Type:** short-text
**Skip:** optional

**Context:** ctx

**Question:**
What is the
primary segment?
"""
    cards = parse_markdown(md)
    assert cards[0].question == "What is the primary segment?"


def test_inline_context_and_question() -> None:
    """The skill sample uses `**Context:** ctx` on one line. Should work."""
    md = """\
## Card 1: Test

**Category:** Cat
**Type:** short-text
**Skip:** optional

**Context:** inline context value

**Question:** inline question?
"""
    cards = parse_markdown(md)
    assert cards[0].context == "inline context value"
    assert cards[0].question == "inline question?"


def test_to_create_kwargs_shape() -> None:
    cards = parse_markdown(VALID_DECK)
    kwargs = cards[0].to_create_kwargs()
    assert set(kwargs.keys()) == {
        "category",
        "title",
        "context",
        "question",
        "response_type",
        "options",
        "default_value",
        "skip_allowed",
        "attachment_path",
    }
    assert kwargs["default_value"] is None
