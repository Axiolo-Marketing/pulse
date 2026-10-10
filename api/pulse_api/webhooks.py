"""Outbound webhook: tell another system what a respondent does with a deck.

Each organization may set ``organizations.webhook_url`` (https) and
``organizations.webhook_secret`` (migration 0021, owner-managed in Settings).
With both set, Pulse POSTs one JSON event per respondent action. Built for
Reba, Axiolo's SDR agent ("signal loop" PR 4); Pulse only sends, it never
acts on anything the receiver does.

Events (the contract the receiver is built to; don't rename anything):

* ``deck_opened`` — the first time a recipient opens their deck (``GET
  /api/me``), once per recipient ever. ``recipients.first_opened_at`` is
  the claim, set whether or not a webhook was configured at the time.
  ``id``: ``opened:<recipient_id>``.
* ``deck_answered`` — ``save_response`` saved an ``answered`` response to a
  confirm-edit, select or text card (AI follow-ups included). Carries a
  ``fact`` (``{text, source: "your answers: <card title>"}``) plus, for
  confirm-edit, ``confirmed`` and (when corrected) ``corrects``. Uploads,
  document links, skips and empty answers send nothing.
  ``id``: ``answered:<response_id>:<epoch of updated_at>``.
* ``contact_shared`` — the same save on a contact-share card, with
  ``contact: {email, name, company?}``.
  ``id``: ``contact:<response_id>:<epoch of updated_at>``.
* ``deck_completed`` — ``completion.py``'s claim succeeded (the moment the
  owner email goes out), whether or not that email sends.
  ``id``: ``completed:<recipient_id>:<epoch of the claim>``.

Every body also has ``kind``, ``occurred_at`` (ISO 8601 UTC),
``engagement_id``, ``title`` (the engagement name) and, when the recipient
has one, ``email`` (lowercase).

Signing: header ``X-Reba-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of
"<t>.<raw body>" keyed with the secret>``. The receiver rejects anything
older than 3 minutes, so each attempt is signed at send time.

Scheduling follows ``completion.py``/``reactive.py``: a detached
``asyncio.create_task`` with a strong-reference registry, never FastAPI
``BackgroundTasks`` (the request's commit lands only at
``get_anon_session`` teardown). The answer job waits, briefly, for the
saved response to become visible before it sends, so nothing is reported
for a save that never committed. All DB access is on the BYPASSRLS
``admin_engine`` with short sessions, and no session is open during HTTP.
Delivery retries network errors and 5xx a few times with backoff; failures
are logged and never reach the respondent.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.db import admin_engine

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Reba-Signature"

# Delivery: attempts in total, and the pause before each retry.
_TIMEOUT_SECONDS = 5.0
_RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0, 9.0)

# The answer job waits for the request's commit to land (normally a few ms),
# same budget as completion.py's claim retry.
_LOAD_MAX_ATTEMPTS = 10
_LOAD_RETRY_SECONDS = 0.3

_TEXT_TYPES = ("short-text", "long-text")
_SELECT_TYPES = ("single-select", "multi-select")

_pending_tasks: set[asyncio.Task] = set()


# ── signing + body helpers (pure) ─────────────────────────────────────────


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """The ``X-Reba-Signature`` header value for ``body`` at ``timestamp``."""
    mac = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={mac}"


def _iso(ts: datetime) -> str:
    """ISO 8601 UTC with a ``Z`` suffix. Naive datetimes are taken as UTC."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _epoch(ts: datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp())


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def answer_fields(
    card: dict[str, Any], value: dict[str, Any] | None
) -> tuple[str, dict[str, Any]] | None:
    """The event kind and kind-specific fields for an answered card, or
    ``None`` when the answer isn't one we report (uploads, document links,
    empty answers, unknown types).

    ``card`` needs ``title``, ``response_type`` and ``default_value``.
    """
    v = value or {}
    title = _clean(card.get("title")) or "Untitled card"
    source = f"your answers: {title}"
    rt = card.get("response_type")

    if rt == "confirm-edit":
        statement = _clean(card.get("default_value"))
        if v.get("confirmed") is True:
            text_ = statement or f"{title}: confirmed"
            return "deck_answered", {
                "fact": {"text": text_, "source": source},
                "confirmed": True,
            }
        correction = _clean(v.get("correction"))
        if v.get("confirmed") is False and correction:
            fields: dict[str, Any] = {
                "fact": {"text": correction, "source": source},
                "confirmed": False,
            }
            if statement:
                fields["corrects"] = statement
            return "deck_answered", fields
        return None

    if rt == "contact-share":
        email = _clean(v.get("email")).lower()
        if not email:
            return None
        contact: dict[str, Any] = {"email": email, "name": _clean(v.get("name"))}
        company = _clean(v.get("company")) or _clean(v.get("role"))
        if company:
            contact["company"] = company
        return "contact_shared", {"contact": contact}

    answer = ""
    if rt in _SELECT_TYPES:
        selected = v.get("selected")
        if isinstance(selected, list):
            answer = ", ".join(_clean(s) for s in selected if _clean(s))
        else:
            answer = _clean(selected)
        note = _clean(v.get("note"))
        if note:
            answer = f"{answer}. Note: {note}" if answer else note
    elif rt in _TEXT_TYPES:
        answer = _clean(v.get("text"))
    else:
        return None
    if not answer:
        return None
    return "deck_answered", {"fact": {"text": f"{title}: {answer}", "source": source}}


def _base_body(
    *, event_id: str, kind: str, occurred_at: datetime, info: dict[str, Any]
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": event_id,
        "kind": kind,
        "occurred_at": _iso(occurred_at),
        "engagement_id": str(info["engagement_id"]),
    }
    email = _clean(info.get("email")).lower()
    if email:
        body["email"] = email
    body["title"] = info.get("engagement_name") or ""
    return body


# ── delivery ──────────────────────────────────────────────────────────────


async def deliver(url: str, secret: str, payload: dict[str, Any]) -> bool:
    """POST ``payload`` to ``url``, signed. Retries network errors and 5xx
    with backoff, re-signing each attempt. Returns True on a 2xx. Never
    raises."""
    body = json.dumps(payload, separators=(",", ":")).encode()
    attempts = len(_RETRY_DELAYS) + 1
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        for attempt in range(attempts):
            headers = {
                "Content-Type": "application/json",
                SIGNATURE_HEADER: sign(secret, body, int(time.time())),
            }
            retryable = False
            try:
                resp = await client.post(url, content=body, headers=headers)
                if 200 <= resp.status_code < 300:
                    return True
                retryable = resp.status_code >= 500
                logger.warning(
                    "webhooks: %s for event %s (attempt %d)",
                    resp.status_code,
                    payload.get("id"),
                    attempt + 1,
                )
            except httpx.HTTPError as exc:
                retryable = True
                logger.warning(
                    "webhooks: %s for event %s (attempt %d)",
                    type(exc).__name__,
                    payload.get("id"),
                    attempt + 1,
                )
            if not retryable or attempt == attempts - 1:
                break
            await asyncio.sleep(_RETRY_DELAYS[attempt])
    logger.error("webhooks: gave up on event %s", payload.get("id"))
    return False


# ── scheduling ────────────────────────────────────────────────────────────


@asynccontextmanager
async def _admin_session() -> AsyncIterator[AsyncSession]:
    """Short-lived BYPASSRLS session. Its own seam so tests can bind it to
    the rolled-back test connection (same as ``completion._admin_session``)."""
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


def _spawn(coro: Awaitable[None]) -> None:
    task = asyncio.ensure_future(coro)
    _pending_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _pending_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("webhooks: task escaped unexpectedly", exc_info=exc)

    task.add_done_callback(_done)


async def wait_for_pending_deliveries() -> None:
    """Test helper: block until every scheduled webhook job finishes."""
    while _pending_tasks:
        await asyncio.gather(*list(_pending_tasks), return_exceptions=True)


def schedule_deck_opened(recipient_id: str) -> None:
    """Fire-and-forget ``run_deck_opened``."""
    _spawn(run_deck_opened(recipient_id))


def schedule_answer(response_id: str, saved_at: datetime) -> None:
    """Fire-and-forget ``run_answer``. ``saved_at`` is the ``updated_at``
    the saving request got back, so the job can tell when its commit
    landed (the row may already exist from an earlier ``viewed`` save)."""
    _spawn(run_answer(response_id, saved_at))


def schedule_deck_completed(recipient_id: str, claimed_at: datetime) -> None:
    """Fire-and-forget ``run_deck_completed``."""
    _spawn(run_deck_completed(recipient_id, claimed_at))


# ── jobs ──────────────────────────────────────────────────────────────────

# The recipient, its engagement and its org's webhook settings.
_RECIPIENT_INFO_SQL = (
    "select r.id::text as recipient_id, r.email, "
    "       r.engagement_id::text as engagement_id, e.engagement_name, "
    "       o.webhook_url, o.webhook_secret "
    "from public.recipients r "
    "join public.engagements e on e.id = r.engagement_id "
    "join public.organizations o on o.id = r.org_id "
    "where r.id = cast(:rid as uuid)"
)


def _configured(info: dict[str, Any] | None) -> bool:
    return bool(info and info.get("webhook_url") and info.get("webhook_secret"))


async def _load_recipient(recipient_id: str) -> dict[str, Any] | None:
    async with _admin_session() as session:
        row = (
            await session.execute(text(_RECIPIENT_INFO_SQL), {"rid": recipient_id})
        ).mappings().one_or_none()
    return dict(row) if row else None


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def run_deck_opened(recipient_id: str) -> None:
    """Claim ``first_opened_at`` and, when the org has a webhook, send
    ``deck_opened``. Never raises."""
    if not _valid_uuid(recipient_id):
        return
    try:
        async with _admin_session() as session:
            opened_at = (
                await session.execute(
                    text(
                        "update public.recipients set first_opened_at = clock_timestamp() "
                        "where id = cast(:rid as uuid) and first_opened_at is null "
                        "returning first_opened_at"
                    ),
                    {"rid": recipient_id},
                )
            ).scalar_one_or_none()
            await session.commit()
        if opened_at is None:
            return
        info = await _load_recipient(recipient_id)
        if not _configured(info):
            return
        assert info is not None
        payload = _base_body(
            event_id=f"opened:{recipient_id}",
            kind="deck_opened",
            occurred_at=opened_at,
            info=info,
        )
        await deliver(info["webhook_url"], info["webhook_secret"], payload)
    except Exception:
        logger.exception("webhooks: deck_opened failed for recipient %s", recipient_id)


async def _load_answer(response_id: str, saved_at: datetime) -> dict[str, Any] | None:
    """The saved response with its card and recipient, once the saving
    request's commit is visible (``updated_at`` at or past ``saved_at``);
    ``None`` if it never shows up."""
    sql = text(
        "select rr.id::text as response_id, rr.state, rr.response_value, "
        "       rr.updated_at, rr.recipient_id::text as recipient_id, "
        "       c.title, c.response_type, c.default_value "
        "from public.responses rr join public.cards c on c.id = rr.card_id "
        "where rr.id = cast(:id as uuid) and rr.updated_at >= cast(:ts as timestamptz)"
    )
    for attempt in range(_LOAD_MAX_ATTEMPTS):
        async with _admin_session() as session:
            row = (
                await session.execute(sql, {"id": response_id, "ts": saved_at})
            ).mappings().one_or_none()
        if row is not None:
            return dict(row)
        if attempt < _LOAD_MAX_ATTEMPTS - 1:
            await asyncio.sleep(_LOAD_RETRY_SECONDS)
    return None


async def run_answer(response_id: str, saved_at: datetime) -> None:
    """Send ``deck_answered`` or ``contact_shared`` for a saved answer.
    Reads the committed response (so a later edit that already landed is
    what gets reported, under its own id). Never raises."""
    if not _valid_uuid(response_id):
        return
    try:
        row = await _load_answer(response_id, saved_at)
        if row is None or row["state"] != "answered":
            return
        value = row["response_value"]
        if isinstance(value, str):
            value = json.loads(value)
        fields = answer_fields(row, value)
        if fields is None:
            return
        info = await _load_recipient(row["recipient_id"])
        if not _configured(info):
            return
        assert info is not None
        kind, extra = fields
        prefix = "contact" if kind == "contact_shared" else "answered"
        updated_at: datetime = row["updated_at"]
        payload = _base_body(
            event_id=f"{prefix}:{response_id}:{_epoch(updated_at)}",
            kind=kind,
            occurred_at=updated_at,
            info=info,
        )
        payload.update(extra)
        await deliver(info["webhook_url"], info["webhook_secret"], payload)
    except Exception:
        logger.exception("webhooks: answer event failed for response %s", response_id)


async def run_deck_completed(recipient_id: str, claimed_at: datetime) -> None:
    """Send ``deck_completed`` for a successful completion claim. Never
    raises."""
    try:
        info = await _load_recipient(recipient_id)
        if not _configured(info):
            return
        assert info is not None
        payload = _base_body(
            event_id=f"completed:{recipient_id}:{_epoch(claimed_at)}",
            kind="deck_completed",
            occurred_at=claimed_at,
            info=info,
        )
        await deliver(info["webhook_url"], info["webhook_secret"], payload)
    except Exception:
        logger.exception("webhooks: deck_completed failed for recipient %s", recipient_id)
