"""Parity tests for the Python port of markdown-export.ts + status-suggest.ts.

The inputs match what the frontend would produce after a real Pulse
session; the expected outputs are the strings captured from the TS
renderer via Playwright on commit 5faabab. If you change either side,
update the other in the SAME commit and re-record these expected strings.
"""
from __future__ import annotations

import pytest

from pulse_api.clickup_export import (
    STATUS_VALUES,
    render_card_markdown,
    render_response_body,
    suggest_status,
)

CLIENT = {"name": "Renee Mueller"}


# ── suggest_status ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "response_type, response_value, expected",
    [
        # confirm-edit always goes to Axiolo Review
        ("confirm-edit", {"confirmed": True}, "Axiolo Review"),
        ("confirm-edit", {"confirmed": False, "correction": "x"}, "Axiolo Review"),
        # single-select: keyword routing
        ("single-select", {"selected": "revenue"}, "Axiolo Review"),
        ("single-select", {"selected": "I need help with this"}, "Needs Attention"),
        ("single-select", {"selected": "we're stuck on this"}, "Blocked"),
        ("single-select", {"selected": "done"}, "Approved"),
        # multi-select: any keyword in the array
        ("multi-select", {"selected": ["paid search", "SEO"]}, "Axiolo Review"),
        ("multi-select", {"selected": []}, "Waiting on Good Life"),
        ("multi-select", {"selected": ["blocked on permissions"]}, "Blocked"),
        ("multi-select", {"selected": ["I can not get the API key"]}, "Needs Attention"),
        # text: scan the body
        ("short-text", {"text": "Good Life Companies, Inc."}, "Axiolo Review"),
        ("long-text", {"text": "we are stuck on hiring"}, "Blocked"),
        ("long-text", {"text": "Need help with the rollout"}, "Needs Attention"),
        # link / contact / file-upload — presence-based
        ("document-link", {"url": "https://example.com"}, "Axiolo Review"),
        ("document-link", {}, "Waiting on Good Life"),
        ("contact-share", {"email": "x@y.z"}, "Axiolo Review"),
        ("contact-share", {"name": "x"}, "Waiting on Good Life"),
        ("file-upload", {"file_ids": ["abc"]}, "Axiolo Review"),
        ("file-upload", {"file_ids": []}, "Waiting on Good Life"),
    ],
)
def test_suggest_status_per_response(response_type, response_value, expected) -> None:
    card = {"response_type": response_type}
    response = {"state": "answered", "response_value": response_value}
    assert suggest_status(card, response) == expected


@pytest.mark.parametrize("state", ["not_started", "viewed", "skipped"])
def test_suggest_status_waits_for_not_answered(state) -> None:
    """Anything that isn't 'answered' falls through to Waiting on Good Life."""
    card = {"response_type": "short-text"}
    response = {"state": state, "response_value": {}}
    assert suggest_status(card, response) == "Waiting on Good Life"


def test_suggest_status_no_response() -> None:
    assert suggest_status({"response_type": "short-text"}, None) == "Waiting on Good Life"


def test_status_values_tuple_matches_ts_array() -> None:
    """The 8 statuses must stay in the same order as the TS constant
    `STATUS_VALUES`; the admin dropdown UI depends on it."""
    assert STATUS_VALUES == (
        "Waiting on Axiolo",
        "Waiting on Good Life",
        "Needs Attention",
        "Axiolo Review",
        "Client Review",
        "Blocked",
        "Approved",
        "Complete",
    )


# ── render_response_body — per-type parity ─────────────────────────────────


def test_body_confirm_edit_confirmed() -> None:
    out = render_response_body(
        {"response_type": "confirm-edit"},
        {"state": "answered", "response_value": {"confirmed": True}},
        [],
    )
    assert out == "Confirmed as written."


def test_body_confirm_edit_corrected() -> None:
    out = render_response_body(
        {"response_type": "confirm-edit"},
        {"state": "answered", "response_value": {"confirmed": False, "correction": "two\nlines"}},
        [],
    )
    assert out == "Edited:\n\n> two\n> lines"


def test_body_single_select() -> None:
    out = render_response_body(
        {"response_type": "single-select"},
        {"state": "answered", "response_value": {"selected": "revenue"}},
        [],
    )
    assert out == "**revenue**"


def test_body_multi_select_with_note() -> None:
    out = render_response_body(
        {"response_type": "multi-select"},
        {"state": "answered", "response_value": {
            "selected": ["paid search", "SEO"],
            "note": "also experimenting with LinkedIn ads",
        }},
        [],
    )
    assert out == "- paid search\n- SEO\n\n**Note:** also experimenting with LinkedIn ads"


def test_body_multi_select_empty() -> None:
    out = render_response_body(
        {"response_type": "multi-select"},
        {"state": "answered", "response_value": {"selected": []}},
        [],
    )
    assert out == "_None selected._"


def test_body_short_text() -> None:
    out = render_response_body(
        {"response_type": "short-text"},
        {"state": "answered", "response_value": {"text": "Good Life Companies, Inc."}},
        [],
    )
    assert out == "Good Life Companies, Inc."


def test_body_long_text_preserves_paragraphs() -> None:
    text = "Heads up. We're in transition.\n\nLet's prioritize SEO."
    out = render_response_body(
        {"response_type": "long-text"},
        {"state": "answered", "response_value": {"text": text}},
        [],
    )
    assert out == text


def test_body_document_link_autolink() -> None:
    out = render_response_body(
        {"response_type": "document-link"},
        {"state": "answered", "response_value": {"url": "https://app.carta.com/x"}},
        [],
    )
    assert out == "<https://app.carta.com/x>"


def test_body_contact_share_full() -> None:
    out = render_response_body(
        {"response_type": "contact-share"},
        {"state": "answered", "response_value": {
            "name": "Janelle Park",
            "role": "Director of Ops",
            "email": "janelle@glc.example",
        }},
        [],
    )
    assert out == "**Janelle Park** (Director of Ops)\njanelle@glc.example"


def test_body_file_upload_lists_attachments() -> None:
    out = render_response_body(
        {"response_type": "file-upload"},
        {"state": "answered", "response_value": {"file_ids": ["abc"]}},
        [{"name": "deck.pdf", "sizeBytes": 62}],
    )
    expected = (
        "**Files attached (1):**\n"
        "- `deck.pdf` (62 B)\n\n"
        "_Files live in the Pulse admin. Search for the file names above to locate them in your local archive._"
    )
    assert out == expected


def test_body_file_upload_empty() -> None:
    out = render_response_body(
        {"response_type": "file-upload"},
        {"state": "answered", "response_value": {"file_ids": []}},
        [],
    )
    assert out == "_No files uploaded._"


def test_body_skipped_with_note() -> None:
    out = render_response_body(
        {"response_type": "short-text"},
        {"state": "skipped", "response_value": {"note": "no time"}},
        [],
    )
    assert out == "_Skipped._\n\n**Note:** no time"


def test_body_skipped_no_note() -> None:
    out = render_response_body(
        {"response_type": "short-text"},
        {"state": "skipped", "response_value": {}},
        [],
    )
    assert out == "_Skipped._"


def test_body_not_started() -> None:
    assert render_response_body({"response_type": "short-text"}, None, []) == "_Not yet viewed._"
    assert render_response_body(
        {"response_type": "short-text"}, {"state": "not_started"}, []
    ) == "_Not yet viewed._"


def test_body_viewed_but_not_answered() -> None:
    out = render_response_body(
        {"response_type": "short-text"},
        {"state": "viewed", "response_value": None},
        [],
    )
    assert out == "_Card opened, no response yet._"


# ── render_card_markdown — full block shape ────────────────────────────────


def test_full_card_block_matches_ts_shape() -> None:
    """One end-to-end block. The string below was captured from the TS
    renderer via Playwright on the migration commit."""
    card = {
        "title": "Confirm your role",
        "context": "You said your role is Founder.",
        "question": "Is that still accurate?",
        "response_type": "confirm-edit",
    }
    response = {"state": "answered", "response_value": {"confirmed": True}}
    out = render_card_markdown(
        card=card, client=CLIENT, response=response, status="Axiolo Review", uploads=[]
    )
    expected = (
        "# Confirm your role\n"
        "\n"
        "**Status:** Axiolo Review\n"
        "\n"
        "## Response from Renee Mueller\n"
        "Confirmed as written.\n"
        "\n"
        "## Original Context\n"
        "You said your role is Founder.\n"
        "\n"
        "## Original Question\n"
        "Is that still accurate?\n"
        "\n"
        "---\n"
    )
    assert out == expected
