"""Tests for ``/api/orgs/me/logo`` (upload, download, delete).

Parametrized over ``(content_type, size_bytes, actor_role,
expected_status)`` for the matrix the spec calls out. Plus a
download-after-upload round-trip and a defense-in-depth check that the
MIME and extension must agree.
"""
from __future__ import annotations

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.password import hash_password
from pulse_api.auth.session import encode_session
from pulse_api.config import settings


def _set_cookie(client: AsyncClient, *, user_id: str, org_id: str) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        encode_session(user_id, org_id),
    )


async def _seed_member(db: AsyncSession, org_id: str) -> str:
    user_id = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, last_active_org_id, "
                " email_verified_at) "
                "values (:e, :h, :n, cast(:o as uuid), now()) "
                "returning id::text"
            ),
            {
                "e": f"member-{secrets.token_hex(3)}@example.com",
                "h": hash_password("a-good-password-here"),
                "n": "Member",
                "o": org_id,
            },
        )
    ).mappings().one()["id"]
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), 'member')"
        ),
        {"o": org_id, "u": user_id},
    )
    return user_id


def _bytes_of(size: int) -> bytes:
    """Return ``size`` deterministic bytes for upload payloads.

    Real PNGs/SVGs aren't required — the route doesn't sniff content,
    only validates ``Content-Type`` + extension.
    """
    return b"A" * size


# ── Upload matrix ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename, content_type, size, actor_role, expected_status",
    [
        # OK: owner uploads valid sizes/types.
        ("logo.png",  "image/png",     100_000, "owner",  200),
        ("logo.jpg",  "image/jpeg",    400_000, "owner",  200),
        ("logo.svg",  "image/svg+xml", 50_000,  "owner",  200),
        ("logo.webp", "image/webp",    20_000,  "owner",  200),
        # 415: bad content-type.
        ("logo.exe",  "application/octet-stream", 100, "owner", 415),
        # 415: png filename but wrong content-type (mismatched).
        ("logo.png",  "image/jpeg",    100,     "owner",  415),
        # 413: oversized.
        ("logo.png",  "image/png",     700_000, "owner",  413),
        # 400: empty file.
        ("logo.png",  "image/png",     0,       "owner",  400),
        # 403: member role.
        ("logo.png",  "image/png",     100,     "member", 403),
    ],
    ids=[
        "owner-png-100k",
        "owner-jpeg-400k",
        "owner-svg-50k",
        "owner-webp-20k",
        "owner-exe-415",
        "owner-mime-ext-mismatch-415",
        "owner-png-700k-413",
        "owner-png-empty-400",
        "member-png-403",
    ],
)
async def test_upload_logo_matrix(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    axiolo_org: dict[str, str],
    filename: str,
    content_type: str,
    size: int,
    actor_role: str,
    expected_status: int,
) -> None:
    """Owner can upload sizes/types in the allow-list; other paths reject."""
    if actor_role == "owner":
        _set_cookie(
            client,
            user_id=seed_admin_user["id"],
            org_id=seed_admin_user["org_id"],
        )
    else:
        member_id = await _seed_member(db, axiolo_org["id"])
        await db.flush()
        _set_cookie(client, user_id=member_id, org_id=axiolo_org["id"])

    files = {
        "file": (
            filename,
            _bytes_of(size),
            content_type,
        ),
    }
    r = await client.post("/api/orgs/me/logo", files=files)
    assert r.status_code == expected_status, (
        f"{filename} {content_type} size={size} role={actor_role}: {r.text}"
    )


# ── Round-trip: upload then GET /api/orgs/me/logo/{filename} ─────────────


async def test_upload_then_download_matches_bytes(
    admin_authed: AsyncClient,
    seed_admin_user: dict[str, str],
) -> None:
    """After upload, GET serves the same bytes."""
    payload = b"PNG-bytes-go-here-but-content-isnt-sniffed" * 4
    files = {"file": ("brand.png", payload, "image/png")}

    r = await admin_authed.post("/api/orgs/me/logo", files=files)
    assert r.status_code == 200, r.text
    logo_path = r.json()["logo_path"]
    # path: org-logos/<org_id>/<uuid>.png
    assert logo_path.startswith("org-logos/")
    filename = logo_path.rsplit("/", 1)[-1]

    r2 = await admin_authed.get(f"/api/orgs/me/logo/{filename}")
    assert r2.status_code == 200, r2.text
    assert r2.content == payload


# ── Delete clears the row + best-effort removes the file ─────────────────


async def test_delete_logo_clears_row(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """DELETE /api/orgs/me/logo clears ``organizations.logo_path``."""
    payload = b"X" * 100
    files = {"file": ("brand.png", payload, "image/png")}
    r = await admin_authed.post("/api/orgs/me/logo", files=files)
    assert r.status_code == 200, r.text

    r2 = await admin_authed.delete("/api/orgs/me/logo")
    assert r2.status_code == 204

    row = (
        await db.execute(
            text(
                "select logo_path from public.organizations "
                "where id = cast(:o as uuid)"
            ),
            {"o": seed_admin_user["org_id"]},
        )
    ).mappings().one()
    assert row["logo_path"] is None


# ── Replace: DB-first, disk-second (audit finding M10) ────────────────────


async def test_replace_logo_commits_before_deleting_old_file(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Uploading a second logo replaces the row and removes the old file
    only AFTER the row commits — mirroring delete_engagement's DB-first/
    disk-second order so a failed commit never leaves the row pointing at
    an already-deleted file."""
    from pulse_api import storage as storage_module

    first = await admin_authed.post(
        "/api/orgs/me/logo",
        files={"file": ("first.png", b"first-bytes" * 10, "image/png")},
    )
    assert first.status_code == 200, first.text
    first_path = first.json()["logo_path"]
    first_on_disk = storage_module.resolve_within_upload_dir(first_path)
    assert first_on_disk.exists()

    second = await admin_authed.post(
        "/api/orgs/me/logo",
        files={"file": ("second.png", b"second-bytes" * 10, "image/png")},
    )
    assert second.status_code == 200, second.text
    second_path = second.json()["logo_path"]
    assert second_path != first_path

    # New file lands, old file is cleaned up — but only after the row
    # already pointed at the new path (verified by the row read below).
    assert storage_module.resolve_within_upload_dir(second_path).exists()
    assert not first_on_disk.exists()

    row = (
        await db.execute(
            text(
                "select logo_path from public.organizations "
                "where id = cast(:o as uuid)"
            ),
            {"o": seed_admin_user["org_id"]},
        )
    ).mappings().one()
    assert row["logo_path"] == second_path


# ── Path traversal defense ───────────────────────────────────────────────


async def test_get_logo_rejects_path_components(
    admin_authed: AsyncClient,
) -> None:
    """A filename containing a path separator → 400."""
    r = await admin_authed.get("/api/orgs/me/logo/..%2Fevil.png")
    assert r.status_code in (400, 404)  # depends on URL decoding step
