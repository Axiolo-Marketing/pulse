"""Active-reference attachments: operator-uploaded HTML/PDF/images that
cards link to via `cards.attachment_path`.

Two routes:
- POST /api/admin/attachments — admin-only multipart upload. The wire
  body carries the filename; we keep only the extension and mint a
  UUID-based name so the public GET URL is unguessable.
- GET /api/attachments/{filename} — public. The filename must match the
  ``<uuid>.<ext>`` shape we minted; arbitrary names are rejected so this
  endpoint can't be turned into a directory walk.

Storage lives under ``settings.upload_dir/attachments/`` — the same dir
as client-uploaded files but a distinct prefix. Attachments are not
org-scoped on disk (they're public via the unguessable URL once
uploaded), so the admin upload route uses ``get_current_org_member``
purely as the auth gate.
"""
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import storage
from pulse_api.audit import record_audit
from pulse_api.auth.middleware import (
    get_current_org_member,
    get_org_scoped_session,
)
from pulse_api.config import settings
from pulse_api.models import OrganizationMembership, User
from pulse_api.observability import limiter

admin_router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_org_member)],
)
public_router = APIRouter(prefix="/api", tags=["client"])


@admin_router.post("/attachments", status_code=201)
@limiter.limit(settings.rate_limit_upload)
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    session: AsyncSession = Depends(get_org_scoped_session),
) -> dict[str, str]:
    user, membership = org_member
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="file too large")

    try:
        relative_path, mime_type = storage.build_attachment_path(
            file.filename or ""
        )
    except storage.StoragePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    storage.write_upload(relative_path=relative_path, content=content)
    # Audit + commit on the org-scoped session. The on-disk write
    # already happened above; if the audit insert (or its commit) trips
    # RLS or another constraint, the DB rolls back the audit row but
    # the file remains as an orphan — acceptable because attachments
    # are publicly-fetchable-by-UUID, not org-scoped on disk.
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="attachment.upload",
        target_type="attachment",
        target_id=relative_path,
        metadata={
            "filename": file.filename or "",
            "mime_type": mime_type,
            "size_bytes": len(content),
        },
    )
    await session.commit()
    return {"path": relative_path, "mime_type": mime_type}


@public_router.get("/attachments/{filename}")
async def serve_attachment(filename: str) -> FileResponse:
    try:
        path = storage.resolve_attachment_filename(filename)
    except storage.StoragePathError:
        raise HTTPException(status_code=404, detail="attachment not found") from None
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="attachment not found")

    mime_type = storage.ATTACHMENT_MIME_BY_EXT.get(
        path.suffix.lower(), "application/octet-stream"
    )

    headers: dict[str, str] = {}
    # SVG can embed scripts; the inline <img src> render path is safe
    # already, but we also serve a strict CSP so the file is safe when
    # opened directly in a tab too.
    if path.suffix.lower() == ".svg":
        headers["Content-Security-Policy"] = "script-src 'none'; default-src 'self' data:;"

    return FileResponse(path=path, media_type=mime_type, headers=headers)
