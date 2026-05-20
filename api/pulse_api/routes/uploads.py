"""File upload + download + delete endpoints.

Client-facing: 3 routes, all token-authenticated via `get_anon_session`
(RLS enforces per-client isolation in the DB; this module adds a path-
traversal guard on the disk side).

Order of operations on POST:
  1. Validate the card_id belongs to the caller (RLS-filtered SELECT).
  2. Read and size-check the request body.
  3. Build the storage path from authenticated client_id + card_id (NEVER
     the request body's idea of these).
  4. Write file to disk.
  5. Insert uploads row. If RLS rejects (impossible at this point given
     step 1, but defense-in-depth), delete the file we just wrote.

DELETE order: DB delete commits first, then on-disk file is removed. If
the file delete fails (already gone, permissions), the DB state is still
clean. Orphan files are cheaper to recover from than dangling rows.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import storage
from pulse_api.config import settings
from pulse_api.db import get_anon_session
from pulse_api.repos import uploads as uploads_repo

router = APIRouter(prefix="/api", tags=["client"])


@router.post("/uploads", status_code=201)
async def upload_file(
    card_id: str = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_anon_session),
) -> dict:
    if not await uploads_repo.card_belongs_to_caller(session, card_id):
        raise HTTPException(status_code=404, detail="card not found")

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="file too large")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="empty file")

    client_id = await uploads_repo.get_current_client_id(session)
    if client_id is None:
        # Token didn't resolve to a client — same shape as RLS rejection.
        raise HTTPException(status_code=404, detail="client not found")

    try:
        relative_path = storage.build_storage_path(
            client_id=client_id,
            card_id=card_id,
            filename=file.filename or "file",
        )
    except storage.StoragePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    storage.write_upload(relative_path=relative_path, content=content)

    row = await uploads_repo.create_upload(
        session,
        card_id=card_id,
        file_name=file.filename or "file",
        file_size_bytes=len(content),
        storage_path=relative_path,
        mime_type=file.content_type,
    )
    if row is None:
        # Belt-and-suspenders: card_belongs_to_caller already validated
        # this, but if something raced, clean up the orphan file.
        storage.delete_upload(relative_path)
        raise HTTPException(status_code=404, detail="card not found")

    await session.commit()
    return row


@router.delete("/uploads/{upload_id}", status_code=204)
async def delete_upload(
    upload_id: str,
    session: AsyncSession = Depends(get_anon_session),
) -> None:
    row = await uploads_repo.delete_for_caller(session, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="upload not found")
    await session.commit()
    # Best-effort file delete AFTER the row delete commits; failure here
    # leaves an orphan file (cheap to clean up), not a dangling row.
    storage.delete_upload(row["storage_path"])


@router.get("/files/{upload_id}")
async def download_file(
    upload_id: str,
    session: AsyncSession = Depends(get_anon_session),
) -> FileResponse:
    row = await uploads_repo.get_by_id_for_caller(session, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="upload not found")
    try:
        path = storage.resolve_within_upload_dir(row["storage_path"])
    except storage.StoragePathError:
        # A DB row whose storage_path escapes the upload root would be a
        # serious data-integrity bug. Refuse rather than serve.
        raise HTTPException(status_code=500, detail="invalid storage path") from None
    if not path.exists():
        raise HTTPException(status_code=404, detail="file missing on disk")
    return FileResponse(
        path=path,
        media_type=row["mime_type"] or "application/octet-stream",
        filename=row["file_name"],
    )
