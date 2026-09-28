"""Voice-answer transcription.

When a respondent records a voice answer, the upload route schedules
``run_transcription`` for it. The job sends the audio to the configured
speech-to-text provider and stores the text on the ``uploads`` row
(``transcript`` / ``transcript_status`` / …, migration 0019) so the operator
sees words, not just an audio player, and the Markdown export carries them.

Gates (both default-off, re-read on every run):
  1. Deployment: ``settings.transcription_enabled`` + a usable provider
     (``available()``).
  2. Engagement: ``engagements.transcription_enabled`` — the operator's
     opt-in. Transcription sends a recording to a third party, which some
     clients (e.g. law firms) must never allow, so it stays off unless
     chosen per engagement.

Scheduling follows ``reactive.schedule_generation``: a detached
``asyncio.create_task`` with a strong-reference registry, never FastAPI
``BackgroundTasks`` — the upload row only commits at ``get_anon_session``
teardown, which runs AFTER background tasks, so a job waiting on that row
would deadlock the request. The job retries the row load briefly instead.

All DB access uses the BYPASSRLS ``admin_engine`` on short sessions; the
provider call runs with NO session open, so a slow provider never pins a
pooled connection. Failures mark the row ``failed`` (with the error) and
never surface to the respondent.

Claiming: a recording is sent to the provider at most once at a time. Every
run first flips the row to ``pending`` with a conditional UPDATE
(``uploads_repo.CLAIM_SQL``) that matches only when no other attempt holds it — the job
does this itself for fresh uploads, and the admin retry route does it on its
own session (so the UI sees ``pending`` at once and a second click gets a
409). While ``pending``, ``transcribed_at`` records when the claim was made,
so a claim orphaned by a crashed worker can be retaken after
``STALE_CLAIM_SECONDS``.

Providers: ``fake`` (dev only — canned text, no network) today. A real
provider is a function ``(audio: bytes, mime_type: str | None) ->
TranscriptResult`` registered in ``_PROVIDERS``.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import storage
from pulse_api.config import settings
from pulse_api.db import admin_engine
from pulse_api.repos.uploads import CLAIM_SQL, STALE_CLAIM_SECONDS

logger = logging.getLogger(__name__)

# The upload row commits when the request's session tears down, shortly
# after the task is scheduled. Poll briefly for it to become visible.
_LOAD_MAX_ATTEMPTS = 10
_LOAD_RETRY_SECONDS = 0.3

_MAX_ERROR_CHARS = 500

_pending_tasks: set[asyncio.Task] = set()


@dataclass(frozen=True)
class TranscriptResult:
    text: str


Provider = Callable[[bytes, str | None], Awaitable[TranscriptResult]]


async def _fake_provider(audio: bytes, mime_type: str | None) -> TranscriptResult:
    """Dev-only stand-in: canned text, zero outbound HTTP. Exercises every
    gate, state transition and UI path without a provider account."""
    await asyncio.sleep(0.5)  # feel like a network call in the UI
    kb = max(1, round(len(audio) / 1024))
    return TranscriptResult(
        text=(
            "[Sample transcript — fake provider] This is where the "
            f"respondent's words would appear ({kb} KB of "
            f"{mime_type or 'audio'}). Set TRANSCRIPTION_PROVIDER to a real "
            "provider to transcribe recordings."
        )
    )


_PROVIDERS: dict[str, Provider] = {
    "fake": _fake_provider,
}


def available() -> bool:
    """True when this deployment can transcribe: the master switch is on and
    the configured provider exists (and has any credentials it needs). The
    ``fake`` provider never counts in production — respondents' recordings
    must never get placeholder text."""
    provider = settings.transcription_provider
    if provider == "fake" and settings.environment == "production":
        return False
    return settings.transcription_enabled and provider in _PROVIDERS


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


@asynccontextmanager
async def _admin_session() -> AsyncIterator[AsyncSession]:
    """Short-lived BYPASSRLS session. Its own seam so tests can bind it to
    the rolled-back test connection (same as ``reactive._admin_session``)."""
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


def schedule_transcription(upload_id: str, *, claimed: bool = False) -> None:
    """Fire-and-forget ``run_transcription`` (see module docstring for why
    this is ``asyncio.create_task`` and not ``BackgroundTasks``). Pass
    ``claimed=True`` when the caller already took the claim."""
    task = asyncio.create_task(run_transcription(upload_id, claimed=claimed))
    _pending_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _pending_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("transcription: task escaped unexpectedly", exc_info=exc)

    task.add_done_callback(_done)


async def wait_for_pending_transcriptions() -> None:
    """Test helper: block until every scheduled transcription finishes."""
    while _pending_tasks:
        await asyncio.gather(*list(_pending_tasks), return_exceptions=True)


async def _load_target(upload_id: str) -> dict | None:
    """The voice upload + its engagement's opt-in flag, retrying briefly
    while the triggering request's commit lands. None if it never appears."""
    for attempt in range(_LOAD_MAX_ATTEMPTS):
        async with _admin_session() as session:
            row = (
                await session.execute(
                    text(
                        "select u.id::text, u.kind, u.storage_path, u.mime_type, "
                        "       e.transcription_enabled "
                        "from public.uploads u "
                        "join public.engagements e on e.id = u.engagement_id "
                        "where u.id = cast(:uid as uuid)"
                    ),
                    {"uid": upload_id},
                )
            ).mappings().one_or_none()
        if row is not None:
            return dict(row)
        if attempt < _LOAD_MAX_ATTEMPTS - 1:
            await asyncio.sleep(_LOAD_RETRY_SECONDS)
    return None


async def _write(upload_id: str, **fields: object) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    async with _admin_session() as session:
        await session.execute(
            text(f"update public.uploads set {sets} where id = cast(:uid as uuid)"),
            {"uid": upload_id, **fields},
        )
        await session.commit()


async def _claim(upload_id: str) -> bool:
    async with _admin_session() as session:
        claimed = (
            await session.execute(
                text(CLAIM_SQL), {"uid": upload_id, "stale": STALE_CLAIM_SECONDS}
            )
        ).scalar_one_or_none()
        await session.commit()
    return claimed is not None


async def run_transcription(upload_id: str, *, claimed: bool = False) -> None:
    """Transcribe one voice upload. Never raises: every failure past the
    gates is recorded on the row as ``failed``. Without ``claimed`` the job
    takes the claim itself and does nothing if another attempt holds it."""
    if not _valid_uuid(upload_id):
        return
    target = await _load_target(upload_id) if available() else None
    if target is None or target["kind"] != "voice" or not target["transcription_enabled"]:
        if target is None and available():
            logger.warning("transcription: upload %s never became visible", upload_id)
        if claimed:
            # Don't leave the caller's claim spinning until it goes stale.
            await _write(
                upload_id,
                transcript_status="failed",
                transcript_error="transcription is no longer enabled",
            )
        return
    if not claimed and not await _claim(upload_id):
        return

    provider_name = settings.transcription_provider
    provider = _PROVIDERS[provider_name]
    try:
        path = storage.resolve_within_upload_dir(target["storage_path"])
        audio = await asyncio.to_thread(path.read_bytes)
        # No DB session is open across the provider call.
        result = await provider(audio, target["mime_type"])
        await _write(
            upload_id,
            transcript=result.text.strip(),
            transcript_status="done",
            transcript_provider=provider_name,
            transcript_error=None,
            transcribed_at=datetime.now(UTC),
        )
    except Exception as exc:  # noqa: BLE001 — recorded, never surfaced
        logger.exception("transcription: failed for upload %s", upload_id)
        try:
            await _write(
                upload_id,
                transcript_status="failed",
                transcript_provider=provider_name,
                transcript_error=f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS],
            )
        except Exception:  # noqa: BLE001
            logger.exception("transcription: couldn't record failure for %s", upload_id)

