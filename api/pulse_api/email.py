"""Outbound email.

Transactional mail (org invites, email verification, password reset) goes
out via Resend's HTTPS API — a plain `POST https://api.resend.com/emails`,
no SMTP sockets, reusing the `httpx` stack the OAuth flows already depend on.

`send_email` is **best-effort and never raises**: it catches every delivery
error and logs it. Two callers (`routes/superadmin.py` create_org,
`routes/auth.py` forgot-password) send *before* their `session.commit()`, so a
raising send would roll back tenant/account state on a transient Resend blip.
Swallow-and-log keeps those mutations decoupled from Resend's uptime, and the
INFO log line below preserves the link as a recoverable fallback.

When `settings.resend_api_key` is empty (dev, CI) the function stays in
log-only mode and makes no network call. Tests monkeypatch `send_email`
wholesale to capture messages, so the function is importable at module level
rather than hidden behind a class — straightforward substitution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from pulse_api.config import settings

log = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_REQUEST_TIMEOUT_SECONDS = 10.0


@dataclass
class OutboundEmail:
    to: str
    subject: str
    body: str


async def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email via Resend. Best-effort — never raises.

    With no `resend_api_key` configured, logs the message and returns without
    contacting Resend (dev/test no-op). With a key, POSTs to the Resend API
    and logs the delivery outcome; any failure (non-2xx, network, timeout) is
    logged at ERROR and swallowed so callers' transactions are never rolled
    back by an email hiccup.
    """
    if not settings.resend_api_key:
        log.info("email(log-only)→%s subject=%r body=%r", to, subject, body)
        return

    payload = {
        "from": settings.email_from,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(RESEND_API_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        log.error("email→%s subject=%r delivery=failed error=%r", to, subject, exc)
        return

    if resp.is_success:
        log.info("email→%s subject=%r delivery=sent", to, subject)
    else:
        log.error(
            "email→%s subject=%r delivery=failed status=%s body=%r",
            to,
            subject,
            resp.status_code,
            resp.text,
        )
