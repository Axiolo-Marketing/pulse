"""Voice transcription (``pulse_api/transcription.py``, migration 0019).

The job runs as a detached task on a BYPASSRLS session; the autouse fixture
binds that session through the rolled-back test connection (same seam and
lock discipline as ``test_reactive_cards.py``). Every test that triggers a
transcription awaits ``wait_for_pending_transcriptions()`` before asserting.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from pulse_api import transcription
from pulse_api.config import settings


@pytest.fixture(autouse=True)
def _transcription_admin_session_via_db_conn(
    db_conn: AsyncConnection,
    db_conn_lock: asyncio.Lock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def _override() -> AsyncIterator[AsyncSession]:
        async with db_conn_lock:
            await db_conn.execute(text("reset role"))
            factory = async_sessionmaker(
                bind=db_conn, expire_on_commit=False, class_=AsyncSession
            )
            async with factory() as session:
                yield session

    monkeypatch.setattr(transcription, "_admin_session", _override)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deployment gate on, fake provider, no artificial latency."""
    monkeypatch.setattr(settings, "transcription_enabled", True)
    monkeypatch.setattr(settings, "transcription_provider", "fake")

    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr(transcription.asyncio, "sleep", _instant)


async def _set_flags(
    db: AsyncSession, engagement_id: str, *, voice: bool = True, transcribe: bool
) -> None:
    await db.execute(
        text(
            "update public.engagements set voice_enabled = :v, "
            "transcription_enabled = :t where id = cast(:e as uuid)"
        ),
        {"v": voice, "t": transcribe, "e": engagement_id},
    )


async def _upload(
    client_authed: AsyncClient, card_id: str, *, kind: str = "voice"
) -> dict:
    r = await client_authed.post(
        "/api/uploads",
        data={"card_id": card_id, "kind": kind},
        files={"file": ("voice.webm", b"audio bytes " * 200, "audio/webm")},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _row(db: AsyncSession, upload_id: str) -> dict:
    return dict(
        (
            await db.execute(
                text(
                    "select transcript, transcript_status, transcript_provider, "
                    "transcript_error, transcribed_at from public.uploads "
                    "where id = cast(:u as uuid)"
                ),
                {"u": upload_id},
            )
        ).mappings().one()
    )


# ── the job ─────────────────────────────────────────────────────────────────


async def test_voice_upload_is_transcribed_when_opted_in(
    enabled: None,
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
) -> None:
    await _set_flags(db, seed_client["id"], transcribe=True)
    up = await _upload(client_authed, seed_cards[0]["id"])
    await transcription.wait_for_pending_transcriptions()

    row = await _row(db, up["id"])
    assert row["transcript_status"] == "done"
    assert row["transcript_provider"] == "fake"
    assert "fake provider" in row["transcript"]
    assert row["transcribed_at"] is not None


async def test_not_transcribed_when_engagement_not_opted_in(
    enabled: None,
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
) -> None:
    await _set_flags(db, seed_client["id"], transcribe=False)
    up = await _upload(client_authed, seed_cards[0]["id"])
    await transcription.wait_for_pending_transcriptions()
    assert (await _row(db, up["id"]))["transcript_status"] is None


async def test_not_transcribed_when_deployment_disabled(
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "transcription_enabled", False)
    await _set_flags(db, seed_client["id"], transcribe=True)
    up = await _upload(client_authed, seed_cards[0]["id"])
    await transcription.wait_for_pending_transcriptions()
    assert (await _row(db, up["id"]))["transcript_status"] is None


async def test_file_uploads_are_never_transcribed(
    enabled: None,
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
) -> None:
    await _set_flags(db, seed_client["id"], transcribe=True)
    up = await _upload(client_authed, seed_cards[0]["id"], kind="file")
    await transcription.wait_for_pending_transcriptions()
    assert (await _row(db, up["id"]))["transcript_status"] is None


async def test_provider_failure_is_recorded_not_raised(
    enabled: None,
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(audio: bytes, mime: str | None) -> transcription.TranscriptResult:
        raise RuntimeError("provider down")

    monkeypatch.setitem(transcription._PROVIDERS, "fake", _boom)
    await _set_flags(db, seed_client["id"], transcribe=True)
    up = await _upload(client_authed, seed_cards[0]["id"])
    await transcription.wait_for_pending_transcriptions()

    row = await _row(db, up["id"])
    assert row["transcript_status"] == "failed"
    assert "provider down" in row["transcript_error"]
    assert row["transcript"] is None


async def test_available_requires_known_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "transcription_enabled", True)
    monkeypatch.setattr(settings, "transcription_provider", "nope")
    assert transcription.available() is False
    monkeypatch.setattr(settings, "transcription_provider", "fake")
    assert transcription.available() is True
    monkeypatch.setattr(settings, "transcription_enabled", False)
    assert transcription.available() is False


async def test_fake_provider_never_available_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "transcription_enabled", True)
    monkeypatch.setattr(settings, "transcription_provider", "fake")
    monkeypatch.setattr(settings, "environment", "production")
    assert transcription.available() is False


# ── admin surface ───────────────────────────────────────────────────────────


async def test_detail_reports_availability(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "transcription_enabled", False)
    r = await admin_authed.get(f"/api/admin/engagements/{seed_client['id']}")
    assert r.json()["transcription_available"] is False
    assert r.json()["engagement"]["transcription_enabled"] is False
    monkeypatch.setattr(settings, "transcription_enabled", True)
    r = await admin_authed.get(f"/api/admin/engagements/{seed_client['id']}")
    assert r.json()["transcription_available"] is True


async def test_enabling_toggle_requires_server_support(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"/api/admin/engagements/{seed_client['id']}"
    monkeypatch.setattr(settings, "transcription_enabled", False)
    r = await admin_authed.patch(url, json={"transcription_enabled": True})
    assert r.status_code == 400

    monkeypatch.setattr(settings, "transcription_enabled", True)
    r = await admin_authed.patch(url, json={"transcription_enabled": True})
    assert r.status_code == 200
    assert r.json()["transcription_enabled"] is True
    # Turning it off is always allowed.
    monkeypatch.setattr(settings, "transcription_enabled", False)
    r = await admin_authed.patch(url, json={"transcription_enabled": False})
    assert r.status_code == 200


async def test_transcribe_route_retries_a_recording(
    enabled: None,
    client_authed: AsyncClient,
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
) -> None:
    # Recorded before the engagement opted in → not transcribed.
    await _set_flags(db, seed_client["id"], transcribe=False)
    up = await _upload(client_authed, seed_cards[0]["id"])
    await transcription.wait_for_pending_transcriptions()

    blocked = await admin_authed.post(f"/api/admin/uploads/{up['id']}/transcribe")
    assert blocked.status_code == 400

    await _set_flags(db, seed_client["id"], transcribe=True)
    r = await admin_authed.post(f"/api/admin/uploads/{up['id']}/transcribe")
    assert r.status_code == 202
    await transcription.wait_for_pending_transcriptions()
    assert (await _row(db, up["id"]))["transcript_status"] == "done"

    audit = (
        await db.execute(
            text(
                "select count(*) from public.audit_logs "
                "where action = 'upload.transcribe' and target_id = :u"
            ),
            {"u": up["id"]},
        )
    ).scalar()
    assert audit == 1


async def test_transcribe_route_rejects_non_voice_and_unknown(
    enabled: None,
    client_authed: AsyncClient,
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
) -> None:
    await _set_flags(db, seed_client["id"], transcribe=True)
    f = await _upload(client_authed, seed_cards[0]["id"], kind="file")
    r = await admin_authed.post(f"/api/admin/uploads/{f['id']}/transcribe")
    assert r.status_code == 400
    r = await admin_authed.post(f"/api/admin/uploads/{uuid.uuid4()}/transcribe")
    assert r.status_code == 404


async def test_transcribe_route_claims_up_front_and_rejects_a_double_click(
    enabled: None,
    client_authed: AsyncClient,
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry route marks the row ``pending`` before the job starts, so a
    second click while it runs is a 409 and the recording reaches the
    provider once."""
    await _set_flags(db, seed_client["id"], transcribe=False)
    up = await _upload(client_authed, seed_cards[0]["id"])
    await transcription.wait_for_pending_transcriptions()
    await _set_flags(db, seed_client["id"], transcribe=True)

    release = asyncio.Event()
    calls: list[int] = []

    async def _slow(audio: bytes, mime: str | None) -> transcription.TranscriptResult:
        calls.append(1)
        await release.wait()
        return transcription.TranscriptResult(text="hello")

    monkeypatch.setitem(transcription._PROVIDERS, "fake", _slow)

    first = await admin_authed.post(f"/api/admin/uploads/{up['id']}/transcribe")
    assert first.status_code == 202
    assert (await _row(db, up["id"]))["transcript_status"] == "pending"

    second = await admin_authed.post(f"/api/admin/uploads/{up['id']}/transcribe")
    assert second.status_code == 409

    release.set()
    await transcription.wait_for_pending_transcriptions()
    row = await _row(db, up["id"])
    assert row["transcript_status"] == "done"
    assert row["transcript"] == "hello"
    assert calls == [1]


@pytest.mark.parametrize(
    ("claim_age", "expect_run"),
    [(1, False), (20, True)],
    ids=["live-claim-skipped", "stale-claim-retaken"],
)
async def test_job_respects_a_live_claim_and_retakes_a_stale_one(
    enabled: None,
    client_authed: AsyncClient,
    db: AsyncSession,
    seed_client: dict[str, str],
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    claim_age: int,
    expect_run: bool,
) -> None:
    await _set_flags(db, seed_client["id"], transcribe=False)
    up = await _upload(client_authed, seed_cards[0]["id"])
    await transcription.wait_for_pending_transcriptions()
    await _set_flags(db, seed_client["id"], transcribe=True)
    # Another attempt claimed it `claim_age` minutes ago.
    await db.execute(
        text(
            "update public.uploads set transcript_status = 'pending', "
            "transcribed_at = now() - make_interval(mins => :age) "
            "where id = cast(:u as uuid)"
        ),
        {"age": claim_age, "u": up["id"]},
    )

    calls: list[int] = []

    async def _count(audio: bytes, mime: str | None) -> transcription.TranscriptResult:
        calls.append(1)
        return transcription.TranscriptResult(text="hi")

    monkeypatch.setitem(transcription._PROVIDERS, "fake", _count)
    await transcription.run_transcription(up["id"])

    row = await _row(db, up["id"])
    assert bool(calls) is expect_run
    assert row["transcript_status"] == ("done" if expect_run else "pending")
