"""Local-disk file storage for uploads.

Path convention: `{engagement_id}/{card_id}/{uuid}-{sanitized-filename}`
under `settings.upload_dir`. The engagement_id segment is the trust
boundary — a caller must NEVER produce a path with another engagement's
prefix, and every read/write reconstructs that prefix from authenticated
state, not from the wire body.

Path traversal defense lives in `resolve_within_upload_dir`: any input
that resolves outside the upload root (via `..`, absolute paths, or
symlinks) raises ValueError. Routes catch that and return 404 rather than
leak existence of files outside the trust boundary.

For production, files live under `/var/lib/pulse/uploads/`. In tests the
`tmp_uploads_dir` fixture monkeypatches `settings.upload_dir` to a
per-test tempdir so disk artifacts can't leak across tests.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from pulse_api.config import settings

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")
_MAX_FILENAME_LENGTH = 200

# Active-reference attachments (operator-uploaded HTML/PDF/images) live
# under this prefix inside settings.upload_dir. The filename is
# `<uuid>.<ext>` — the UUID gates access since this endpoint is public.
ATTACHMENTS_PREFIX = "attachments"

ATTACHMENT_MIME_BY_EXT: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".png": "image/png",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


class StoragePathError(ValueError):
    """Raised when a path attempts to escape the upload directory."""


def sanitize_filename(name: str) -> str:
    """Strip path components and unsafe characters from a filename.

    Preserves dots so extensions survive. Capped at 200 chars so absurd
    filenames don't bloat the on-disk path. Returns 'file' if the input
    sanitizes down to nothing."""
    # Strip any directory components by taking just the last segment
    base = Path(name).name
    safe = _SAFE_FILENAME.sub("_", base).strip("_.")
    safe = safe[:_MAX_FILENAME_LENGTH]
    return safe or "file"


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


def build_attachment_path(filename: str) -> tuple[str, str]:
    """Allocate a relative storage path for an operator-uploaded attachment.

    Returns (relative_path, mime_type). The path is
    `attachments/<uuid>.<ext>` — the UUID is the access gate for the
    public GET endpoint, and the extension drives the served MIME.
    Raises StoragePathError if the extension isn't in the allow-list.
    """
    ext = Path(filename or "").suffix.lower()
    if ext not in ATTACHMENT_MIME_BY_EXT:
        raise StoragePathError(
            f"unsupported extension {ext!r}; allowed: "
            f"{', '.join(sorted(ATTACHMENT_MIME_BY_EXT))}"
        )
    return f"{ATTACHMENTS_PREFIX}/{uuid.uuid4()}{ext}", ATTACHMENT_MIME_BY_EXT[ext]


def resolve_attachment_filename(filename: str) -> Path:
    """Resolve a public attachment GET filename to its on-disk path.

    Rejects anything that doesn't match the `<uuid>.<ext>` shape we mint
    in `build_attachment_path` — this is the gate that stops the public
    endpoint from being abused to read arbitrary files under upload_dir.
    """
    name = Path(filename).name  # strip any path components defensively
    if name != filename:
        raise StoragePathError(f"path components rejected: {filename!r}")
    suffix = Path(name).suffix.lower()
    stem = name[: -len(suffix)] if suffix else name
    if suffix not in ATTACHMENT_MIME_BY_EXT or not _is_uuid(stem):
        raise StoragePathError(f"invalid attachment filename: {filename!r}")
    return resolve_within_upload_dir(f"{ATTACHMENTS_PREFIX}/{name}")


def build_storage_path(*, engagement_id: str, card_id: str, filename: str) -> str:
    """Build the relative storage path for a new upload.

    Both ids must be valid UUIDs — non-UUID inputs raise StoragePathError
    so a caller can't sneak `..` or other path components into the prefix.
    """
    if not _is_uuid(engagement_id):
        raise StoragePathError(f"invalid engagement_id: {engagement_id!r}")
    if not _is_uuid(card_id):
        raise StoragePathError(f"invalid card_id: {card_id!r}")
    safe = sanitize_filename(filename)
    return f"{engagement_id}/{card_id}/{uuid.uuid4()}-{safe}"


def resolve_within_upload_dir(relative_path: str) -> Path:
    """Map a relative storage path to an absolute path under upload_dir.

    Raises StoragePathError on any attempt to escape the root, including
    absolute paths, `..` traversal, and symlinks that point outside.
    """
    if not relative_path:
        raise StoragePathError("empty path")
    if Path(relative_path).is_absolute():
        raise StoragePathError(f"absolute path rejected: {relative_path!r}")

    base = Path(settings.upload_dir).resolve()
    # Resolve with strict=False — the file may not exist yet on writes.
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base):
        raise StoragePathError(f"path escapes upload root: {relative_path!r}")
    return target


def write_upload(*, relative_path: str, content: bytes) -> None:
    target = resolve_within_upload_dir(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def delete_upload(relative_path: str) -> bool:
    """Best-effort delete. Returns True if a file was removed, False if
    the path was invalid or no file existed. Never raises."""
    try:
        target = resolve_within_upload_dir(relative_path)
    except StoragePathError:
        return False
    if not target.exists() or not target.is_file():
        return False
    target.unlink()
    return True
