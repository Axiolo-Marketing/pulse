"""Endpoint tests for file upload, download, and delete.

The cross-client isolation tests are the most important: a client must
not be able to read or remove another client's files, even if they
correctly guess the upload_id. RLS on the `uploads` table is the
backstop; this layer also verifies the on-disk path is reconstructed
from authenticated state, never from request input.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import storage


# ── POST /api/uploads — happy path + variants ─────────────────────────────


async def test_upload_writes_file_and_inserts_row(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
) -> None:
    card_id = seed_cards[0]["id"]
    r = await client_authed.post(
        "/api/uploads",
        data={"card_id": card_id},
        files={"file": ("report.pdf", b"hello pdf bytes", "application/pdf")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["file_name"] == "report.pdf"
    assert body["file_size_bytes"] == len(b"hello pdf bytes")
    assert body["mime_type"] == "application/pdf"
    # Storage path lives under the uploads dir, with the expected prefix shape
    on_disk = storage.resolve_within_upload_dir(body["storage_path"])
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"hello pdf bytes"
    # client_id segment is the first directory component
    assert body["storage_path"].split("/")[1] == card_id


async def test_upload_rejects_oversize(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pulse_api.config import settings
    monkeypatch.setattr(settings, "max_upload_bytes", 10)

    r = await client_authed.post(
        "/api/uploads",
        data={"card_id": seed_cards[0]["id"]},
        files={"file": ("big.bin", b"x" * 1024, "application/octet-stream")},
    )
    assert r.status_code == 413


async def test_upload_rejects_empty(
    client_authed: AsyncClient, seed_cards: list[dict[str, str]]
) -> None:
    r = await client_authed.post(
        "/api/uploads",
        data={"card_id": seed_cards[0]["id"]},
        files={"file": ("empty.bin", b"", "application/octet-stream")},
    )
    assert r.status_code == 400


async def test_upload_unknown_card_returns_404(client_authed: AsyncClient) -> None:
    r = await client_authed.post(
        "/api/uploads",
        data={"card_id": str(uuid.uuid4())},
        files={"file": ("x.bin", b"x", "application/octet-stream")},
    )
    assert r.status_code == 404


async def test_upload_other_clients_card_returns_404(
    client_authed: AsyncClient,
    other_seeded_client: dict[str, str],
    db: AsyncSession,
) -> None:
    """A request body's card_id pointing to another client's card must
    404 — the storage path is constructed server-side from the caller's
    client_id, so even if the row could be inserted (it can't, RLS) the
    file wouldn't land under the right prefix anyway."""
    row = (
        await db.execute(
            text(
                "insert into public.cards "
                "(client_id, order_index, category, title, context, question, response_type) "
                "values (cast(:c as uuid), 1, 'C', 'their card', 'X', 'Q', 'file-upload') "
                "returning id::text"
            ),
            {"c": other_seeded_client["id"]},
        )
    ).mappings().one()
    r = await client_authed.post(
        "/api/uploads",
        data={"card_id": row["id"]},
        files={"file": ("x.bin", b"x", "application/octet-stream")},
    )
    assert r.status_code == 404


async def test_upload_sanitizes_filename_with_traversal(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
    tmp_uploads_dir: Path,
) -> None:
    """The user-supplied filename may contain `../`; the sanitizer must
    keep the on-disk path inside the upload root."""
    r = await client_authed.post(
        "/api/uploads",
        data={"card_id": seed_cards[0]["id"]},
        files={"file": ("../../../etc/passwd", b"bytes", "application/octet-stream")},
    )
    assert r.status_code == 201
    # storage_path ends with the sanitized name, no traversal segments
    body = r.json()
    assert "../" not in body["storage_path"]
    on_disk = storage.resolve_within_upload_dir(body["storage_path"])
    assert on_disk.is_relative_to(tmp_uploads_dir.resolve())


# ── GET /api/files/{upload_id} ────────────────────────────────────────────


@pytest.fixture
async def own_upload(
    client_authed: AsyncClient,
    seed_cards: list[dict[str, str]],
) -> dict:
    r = await client_authed.post(
        "/api/uploads",
        data={"card_id": seed_cards[0]["id"]},
        files={"file": ("notes.txt", b"my private notes", "text/plain")},
    )
    return r.json()


async def test_download_streams_own_file(
    client_authed: AsyncClient, own_upload: dict
) -> None:
    r = await client_authed.get(f"/api/files/{own_upload['id']}")
    assert r.status_code == 200
    assert r.content == b"my private notes"
    assert "text/plain" in r.headers.get("content-type", "")


async def test_download_unknown_id_returns_404(client_authed: AsyncClient) -> None:
    r = await client_authed.get(f"/api/files/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_download_cannot_target_other_clients_upload(
    client_authed: AsyncClient,
    other_seeded_client: dict[str, str],
    db: AsyncSession,
    tmp_uploads_dir: Path,
) -> None:
    """Insert an uploads row owned by the OTHER client, with a real file
    on disk. Token A asks for it via /files/{id} — must 404. The RLS
    SELECT hides it; we never reach the disk path."""
    card_row = (
        await db.execute(
            text(
                "insert into public.cards "
                "(client_id, order_index, category, title, context, question, response_type) "
                "values (cast(:c as uuid), 1, 'C', 'theirs', 'X', 'Q', 'file-upload') "
                "returning id::text"
            ),
            {"c": other_seeded_client["id"]},
        )
    ).mappings().one()

    rel = storage.build_storage_path(
        client_id=other_seeded_client["id"],
        card_id=card_row["id"],
        filename="secret.txt",
    )
    storage.write_upload(relative_path=rel, content=b"secret content")

    upload_row = (
        await db.execute(
            text(
                "insert into public.uploads "
                "(card_id, client_id, file_name, file_size_bytes, storage_path, mime_type) "
                "values (cast(:k as uuid), cast(:c as uuid), 'secret.txt', 14, :sp, 'text/plain') "
                "returning id::text"
            ),
            {"k": card_row["id"], "c": other_seeded_client["id"], "sp": rel},
        )
    ).mappings().one()

    r = await client_authed.get(f"/api/files/{upload_row['id']}")
    assert r.status_code == 404


async def test_download_when_file_missing_on_disk_returns_404(
    client_authed: AsyncClient, own_upload: dict, tmp_uploads_dir: Path
) -> None:
    """A row exists but the file was nuked out-of-band (manual cleanup,
    disk failure). The route should report 404 rather than 500."""
    on_disk = storage.resolve_within_upload_dir(own_upload["storage_path"])
    on_disk.unlink()
    r = await client_authed.get(f"/api/files/{own_upload['id']}")
    assert r.status_code == 404
    assert "missing" in r.json()["detail"].lower()


# ── DELETE /api/uploads/{id} ──────────────────────────────────────────────


async def test_delete_removes_row_and_file(
    client_authed: AsyncClient, own_upload: dict
) -> None:
    on_disk = storage.resolve_within_upload_dir(own_upload["storage_path"])
    assert on_disk.exists()

    r = await client_authed.delete(f"/api/uploads/{own_upload['id']}")
    assert r.status_code == 204
    assert not on_disk.exists()

    # And /files/{id} now 404s
    r = await client_authed.get(f"/api/files/{own_upload['id']}")
    assert r.status_code == 404


async def test_delete_unknown_id_returns_404(client_authed: AsyncClient) -> None:
    r = await client_authed.delete(f"/api/uploads/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_delete_other_clients_upload_returns_404(
    client_authed: AsyncClient,
    other_seeded_client: dict[str, str],
    db: AsyncSession,
) -> None:
    """RLS gates the DELETE — caller's token must match the row's client_id."""
    card_row = (
        await db.execute(
            text(
                "insert into public.cards "
                "(client_id, order_index, category, title, context, question, response_type) "
                "values (cast(:c as uuid), 1, 'C', 'theirs', 'X', 'Q', 'file-upload') "
                "returning id::text"
            ),
            {"c": other_seeded_client["id"]},
        )
    ).mappings().one()
    upload_row = (
        await db.execute(
            text(
                "insert into public.uploads "
                "(card_id, client_id, file_name, file_size_bytes, storage_path, mime_type) "
                "values (cast(:k as uuid), cast(:c as uuid), 'x.bin', 1, 'fake/path', null) "
                "returning id::text"
            ),
            {"k": card_row["id"], "c": other_seeded_client["id"]},
        )
    ).mappings().one()

    r = await client_authed.delete(f"/api/uploads/{upload_row['id']}")
    assert r.status_code == 404


# ── GET /api/admin/uploads/{id}/download ──────────────────────────────────


async def test_admin_download_streams_any_upload(
    admin_authed: AsyncClient,
    own_upload: dict,
) -> None:
    r = await admin_authed.get(f"/api/admin/uploads/{own_upload['id']}/download")
    assert r.status_code == 200
    assert r.content == b"my private notes"


async def test_admin_download_unknown_id_returns_404(admin_authed: AsyncClient) -> None:
    r = await admin_authed.get(f"/api/admin/uploads/{uuid.uuid4()}/download")
    assert r.status_code == 404


async def test_admin_download_rejects_anonymous(
    client: AsyncClient, own_upload: dict
) -> None:
    r = await client.get(f"/api/admin/uploads/{own_upload['id']}/download")
    assert r.status_code == 401


# ── Token gate across the upload routes ───────────────────────────────────


@pytest.mark.parametrize(
    "method, path, kwargs",
    [
        ("POST",   "/api/uploads",                                     {"data": {"card_id": str(uuid.uuid4())}}),
        ("DELETE", f"/api/uploads/{uuid.uuid4()}",                     {}),
        ("GET",    f"/api/files/{uuid.uuid4()}",                       {}),
    ],
)
async def test_upload_endpoints_require_token(
    client: AsyncClient, method: str, path: str, kwargs: dict
) -> None:
    r = await client.request(method, path, **kwargs)
    assert r.status_code == 401
