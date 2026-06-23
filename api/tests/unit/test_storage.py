"""Unit tests for `pulse_api.storage`. No DB, no FastAPI.

The traversal-defense tests are the load-bearing ones: every path the
disk layer hands back must live under `settings.upload_dir`, no
exceptions, regardless of how creative the input is.
"""
from __future__ import annotations

import os
import uuid

import pytest

from pulse_api import storage
from pulse_api.storage import StoragePathError


# ── sanitize_filename ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("report.pdf",                "report.pdf"),
        ("My File.pdf",               "My_File.pdf"),
        ("../../etc/passwd",          "passwd"),
        ("/abs/path/notes.txt",       "notes.txt"),
        ("..\\..\\windows\\thing",    "windows_thing"),  # backslash isn't a path sep on POSIX
        ("",                          "file"),
        (".",                         "file"),
        ("..",                        "file"),
        ("....",                      "file"),
        ("only-spaces .txt",          "only-spaces_.txt"),
        ("naïve résumé.docx",         "na_ve_r_sum_.docx"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert storage.sanitize_filename(raw) == expected


def test_sanitize_filename_caps_length() -> None:
    huge = "a" * 5000 + ".pdf"
    out = storage.sanitize_filename(huge)
    assert len(out) <= 200


# ── build_storage_path ────────────────────────────────────────────────────


def test_build_storage_path_uses_uuid_prefix() -> None:
    import re

    cid, kid = str(uuid.uuid4()), str(uuid.uuid4())
    p = storage.build_storage_path(engagement_id=cid, card_id=kid, filename="x.pdf")
    parts = p.split("/")
    assert parts[0] == cid
    assert parts[1] == kid
    # Third segment: <uuid>-<sanitized_filename>. UUIDs contain dashes so
    # a simple split on '-' would mis-parse. Match the 36-char UUID prefix.
    m = re.match(r"^([0-9a-f-]{36})-(.+)$", parts[2])
    assert m, f"unexpected third segment shape: {parts[2]!r}"
    assert uuid.UUID(m.group(1))
    assert m.group(2) == "x.pdf"


@pytest.mark.parametrize(
    "bad_client_id, bad_card_id",
    [
        ("not-a-uuid",          str(uuid.uuid4())),
        (str(uuid.uuid4()),     "../traversal"),
        ("",                    str(uuid.uuid4())),
        (str(uuid.uuid4()),     ""),
    ],
)
def test_build_storage_path_rejects_non_uuid_ids(bad_client_id: str, bad_card_id: str) -> None:
    with pytest.raises(StoragePathError):
        storage.build_storage_path(engagement_id=bad_client_id, card_id=bad_card_id, filename="x")


def test_build_storage_path_sanitizes_filename() -> None:
    cid, kid = str(uuid.uuid4()), str(uuid.uuid4())
    p = storage.build_storage_path(engagement_id=cid, card_id=kid, filename="../../../etc/passwd")
    # Last segment must NOT contain ..
    assert "/.." not in p
    assert p.endswith("-passwd")


# ── resolve_within_upload_dir — the traversal defense ─────────────────────


def test_resolve_within_upload_dir_accepts_normal_path(tmp_uploads_dir) -> None:
    cid = str(uuid.uuid4())
    path = storage.resolve_within_upload_dir(f"{cid}/foo/bar.pdf")
    assert str(path).startswith(str(tmp_uploads_dir.resolve()))


@pytest.mark.parametrize(
    "evil",
    [
        "/etc/passwd",                 # absolute
        "/var/lib/pulse/uploads/x",    # absolute even if it looks innocent
        "../escape",                   # parent traversal
        "../../escape",                # multiple parents
        "foo/../../escape",            # nested
        "",                            # empty
    ],
)
def test_resolve_within_upload_dir_rejects_traversal(evil: str) -> None:
    with pytest.raises(StoragePathError):
        storage.resolve_within_upload_dir(evil)


def test_resolve_within_upload_dir_normalizes_redundant_dot_segments(tmp_uploads_dir) -> None:
    """A path with `./` segments resolves cleanly as long as the final
    target stays under the root."""
    cid = str(uuid.uuid4())
    p = storage.resolve_within_upload_dir(f"./{cid}/./foo/bar.pdf")
    assert p.is_relative_to(tmp_uploads_dir.resolve())


# ── write_upload + delete_upload roundtrip ────────────────────────────────


def test_write_and_delete_roundtrip(tmp_uploads_dir) -> None:
    cid, kid = str(uuid.uuid4()), str(uuid.uuid4())
    rel = storage.build_storage_path(engagement_id=cid, card_id=kid, filename="data.bin")
    storage.write_upload(relative_path=rel, content=b"hello world")

    target = storage.resolve_within_upload_dir(rel)
    assert target.read_bytes() == b"hello world"

    assert storage.delete_upload(rel) is True
    assert not target.exists()
    # Second delete is a no-op
    assert storage.delete_upload(rel) is False


def test_delete_upload_swallows_traversal_paths() -> None:
    """`delete_upload` is called from cleanup paths where raising would
    create dangling state. It must never raise on malicious input."""
    assert storage.delete_upload("../escape") is False
    assert storage.delete_upload("/etc/passwd") is False
    assert storage.delete_upload("") is False
