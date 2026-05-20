"""Outbound email.

Today this just logs the message. The SMTP wiring (host/port/credentials
from settings) lands in a follow-up phase once we have the auth flows
tested end-to-end with a captured-emails fixture.

Tests monkeypatch `send_email` to capture outbound messages, so the
function is intentionally importable at module level rather than hidden
behind a class — straightforward substitution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class OutboundEmail:
    to: str
    subject: str
    body: str


async def send_email(to: str, subject: str, body: str) -> None:
    """Send an email. Currently logs only; SMTP integration lands later."""
    log.info("email→%s subject=%r body=%r", to, subject, body)
