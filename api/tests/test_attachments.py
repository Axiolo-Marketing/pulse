"""Tests for the attachment upload + serve endpoints."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import AsyncClient

from pulse_api.config import settings


# ── POST /api/admin/attachments ──────────────────────────────────────────


@pytest.mark.parametrize(
    "filename, expected_mime",
    [
        ("report.html", "text/html; charset=utf-8"),
        ("report.htm", "text/html; charset=utf-8"),
        ("report.pdf", "application/pdf"),
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("photo.png", "image/png"),
        ("photo.gif", "image/gif"),
        ("photo.webp", "image/webp"),
        ("icon.svg", "image/svg+xml"),
    ],
)
async def test_upload_accepts_all_allowed_types(
    admin_authed: AsyncClient,
    tmp_uploads_dir: Path,
    filename: str,
    expected_mime: str,
) -> None:
    r = await admin_authed.post(
        "/api/admin/attachments",
        files={"file": (filename, b"<some bytes>", "application/octet-stream")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["mime_type"] == expected_mime
    # path is `attachments/<uuid>.<ext>` and the file lives on disk
    assert re.match(r"^attachments/[0-9a-f-]+\.[a-z]+$", body["path"])
    assert (tmp_uploads_dir / body["path"]).exists()


async def test_upload_rejects_unsupported_extension(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.post(
        "/api/admin/attachments",
        files={"file": ("malware.exe", b"MZ\x00\x00", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "extension" in r.json()["detail"].lower()


async def test_upload_rejects_no_extension(admin_authed: AsyncClient) -> None:
    r = await admin_authed.post(
        "/api/admin/attachments",
        files={"file": ("README", b"hello", "text/plain")},
    )
    assert r.status_code == 400


async def test_upload_rejects_empty_file(admin_authed: AsyncClient) -> None:
    r = await admin_authed.post(
        "/api/admin/attachments",
        files={"file": ("empty.html", b"", "text/html")},
    )
    assert r.status_code == 400


async def test_upload_rejects_oversize(
    admin_authed: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_upload_bytes", 10)
    r = await admin_authed.post(
        "/api/admin/attachments",
        files={"file": ("big.html", b"x" * 11, "text/html")},
    )
    assert r.status_code == 413


async def test_upload_requires_admin(client: AsyncClient) -> None:
    r = await client.post(
        "/api/admin/attachments",
        files={"file": ("ok.html", b"<p>hi</p>", "text/html")},
    )
    assert r.status_code == 401


# ── GET /api/attachments/{filename} ──────────────────────────────────────


async def test_serve_round_trip(
    admin_authed: AsyncClient, client: AsyncClient
) -> None:
    """Upload then fetch via the public endpoint without auth."""
    content = b"<!doctype html><p>hello</p>"
    r = await admin_authed.post(
        "/api/admin/attachments",
        files={"file": ("doc.html", content, "text/html")},
    )
    path = r.json()["path"]  # `attachments/<uuid>.html`
    filename = path.rsplit("/", 1)[-1]

    # No auth on the GET — public endpoint
    fresh = AsyncClient(transport=client._transport, base_url=client.base_url)
    try:
        r2 = await fresh.get(f"/api/attachments/{filename}")
        assert r2.status_code == 200
        assert r2.content == content
        assert r2.headers["content-type"].startswith("text/html")
    finally:
        await fresh.aclose()


async def test_serve_svg_includes_csp_header(admin_authed: AsyncClient) -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    r = await admin_authed.post(
        "/api/admin/attachments",
        files={"file": ("logo.svg", svg, "image/svg+xml")},
    )
    filename = r.json()["path"].rsplit("/", 1)[-1]

    r2 = await admin_authed.get(f"/api/attachments/{filename}")
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("image/svg+xml")
    csp = r2.headers.get("content-security-policy", "")
    assert "script-src 'none'" in csp


@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd",         # traversal
        "..%2Fetc%2Fpasswd",        # encoded — would need normalization, but `/` not allowed in segment
        "not-a-uuid.html",          # right shape but stem isn't a UUID
        "abcdef.exe",               # bad extension
        "abcdef",                   # no extension
    ],
)
async def test_serve_rejects_malformed_filenames(
    admin_authed: AsyncClient, bad: str
) -> None:
    r = await admin_authed.get(f"/api/attachments/{bad}")
    assert r.status_code == 404


async def test_serve_returns_404_for_unknown_uuid(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.get(
        "/api/attachments/00000000-0000-0000-0000-000000000000.html"
    )
    assert r.status_code == 404
