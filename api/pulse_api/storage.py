"""Local-disk file storage for uploads.

Path convention: `{client_id}/{card_id}/{uuid}-{sanitized-filename}` under
`settings.upload_dir`. The client_id segment is the trust boundary — a
caller must NEVER produce a path with another client's prefix, and every
read/write reconstructs that prefix from authenticated state, not from
the wire body.

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


def build_storage_path(*, client_id: str, card_id: str, filename: str) -> str:
    """Build the relative storage path for a new upload.

    Both ids must be valid UUIDs — non-UUID inputs raise StoragePathError
    so a caller can't sneak `..` or other path components into the prefix.
    """
    if not _is_uuid(client_id):
        raise StoragePathError(f"invalid client_id: {client_id!r}")
    if not _is_uuid(card_id):
        raise StoragePathError(f"invalid card_id: {card_id!r}")
    safe = sanitize_filename(filename)
    return f"{client_id}/{card_id}/{uuid.uuid4()}-{safe}"


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
