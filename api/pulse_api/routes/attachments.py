"""Active-reference attachments: operator-uploaded HTML/PDF/images that
cards link to via `cards.attachment_path`.

Two routes:
- POST /api/admin/attachments — admin-only multipart upload. The wire body
  carries the filename; we keep only the extension and mint a UUID-based
  name so the public GET URL is unguessable.
- GET /api/attachments/{filename} — public. The filename must match the
  `<uuid>.<ext>` shape we minted; arbitrary names are rejected so this
  endpoint can't be turned into a directory walk.

Storage lives under `settings.upload_dir/attachments/` — the same dir
as client-uploaded files but a distinct prefix.
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from pulse_api import storage
from pulse_api.auth.middleware import get_current_admin
from pulse_api.config import settings
from pulse_api.models import User

admin_router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_current_admin)]
)
public_router = APIRouter(prefix="/api", tags=["client"])


@admin_router.post("/attachments", status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    _: User = Depends(get_current_admin),
) -> dict[str, str]:
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
