"""Admin-only endpoints. Every route is gated by `Depends(get_current_admin)`
which requires a valid session cookie AND `users.is_admin = true`.

The DB session here is the BYPASSRLS one (`get_admin_session`) — admin
operations need to read and write across all clients, which RLS would
otherwise block.
"""
from typing import Any

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import clickup, clickup_export, storage
from pulse_api.auth.middleware import get_current_admin
from pulse_api.db import get_admin_session
from pulse_api.models import User
from pulse_api.observability import log
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import clients as clients_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import uploads as uploads_repo
from pulse_api.repos import users as users_repo

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])


# ── Request/response models ────────────────────────────────────────────────


class CreateClientRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    org_name: str | None = None
    engagement_name: str | None = None


class UpdateClientRequest(BaseModel):
    """Partial update. Fields omitted from the request body stay as-is.
    `token` is intentionally not accepted here — rotation goes through
    its own POST endpoint so it's an explicit action."""

    name: str | None = None
    org_name: str | None = None
    engagement_name: str | None = None
    brief: str | None = None


RESPONSE_TYPES = (
    "confirm-edit", "single-select", "multi-select", "short-text",
    "long-text", "file-upload", "document-link", "contact-share",
)


class CreateCardRequest(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    context: str
    question: str
    response_type: str = Field(
        pattern=r"^(confirm-edit|single-select|multi-select|short-text|"
                r"long-text|file-upload|document-link|contact-share)$"
    )
    options: list[str] | None = None
    default_value: str | None = None
    skip_allowed: bool = True
    attachment_path: str | None = None


class UpdateCardRequest(BaseModel):
    """response_type is intentionally not accepted — changing it would
    invalidate existing responses whose `response_value` shape depends on it."""

    category: str | None = None
    title: str | None = None
    context: str | None = None
    question: str | None = None
    options: list[str] | None = None
    default_value: str | None = None
    skip_allowed: bool | None = None
    attachment_path: str | None = None


# ── Engagement (clients table) ─────────────────────────────────────────────


@router.get("/clients")
async def list_engagements(
    session: AsyncSession = Depends(get_admin_session),
    _: User = Depends(get_current_admin),
) -> list[dict[str, Any]]:
    return await clients_repo.list_all_with_counts(session)


@router.get("/clients/{client_id}")
async def get_engagement(
    client_id: str,
    session: AsyncSession = Depends(get_admin_session),
    _: User = Depends(get_current_admin),
) -> dict[str, Any]:
    client = await clients_repo.get_by_id(session, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return {
        "client": client,
        "cards": await cards_repo.list_for_client(session, client_id),
        "responses": await responses_repo.admin_list_for_client(session, client_id),
        "uploads": await uploads_repo.list_for_client(session, client_id),
    }


@router.post("/clients", status_code=201)
async def create_engagement(
    req: CreateClientRequest,
    session: AsyncSession = Depends(get_admin_session),
    _: User = Depends(get_current_admin),
) -> dict[str, Any]:
    row = await clients_repo.create_engagement(
        session,
        name=req.name,
        org_name=req.org_name,
        engagement_name=req.engagement_name,
    )
    await session.commit()
    return row


@router.patch("/clients/{client_id}")
async def update_engagement(
    client_id: str,
    req: UpdateClientRequest,
    session: AsyncSession = Depends(get_admin_session),
    _: User = Depends(get_current_admin),
) -> dict[str, Any]:
    fields = req.model_dump(exclude_unset=True)
    row = await clients_repo.update_engagement(session, client_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    await session.commit()
    return row


class SetClickUpListRequest(BaseModel):
    """Operator pastes a ClickUp list URL (or bare list id). Backend
    extracts the numeric id and stores it. Empty string clears the
    binding (disables the Push button for that engagement)."""
    url_or_id: str = Field(default="", max_length=500)


_CLICKUP_LIST_URL_RE = re.compile(r"/li/(\d+)")


def _parse_clickup_list_id(value: str) -> str | None:
    """Accept either:
      - Bare numeric id: '901234567'
      - List URL: 'https://app.clickup.com/12345/v/li/901234567'
    Returns the numeric id, or None if the input doesn't look like either.
    Empty/whitespace input returns None (caller treats as 'clear binding')."""
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return value
    m = _CLICKUP_LIST_URL_RE.search(value)
    return m.group(1) if m else None


@router.patch("/clients/{client_id}/clickup-list")
async def set_clickup_list(
    client_id: str,
    req: SetClickUpListRequest,
    session: AsyncSession = Depends(get_admin_session),
    user: User = Depends(get_current_admin),
) -> dict:
    list_id = _parse_clickup_list_id(req.url_or_id)
    list_name: str | None = None
    # If a token is connected AND a list id was provided, fetch the list
    # name so the UI can display it without the operator re-typing.
    if list_id:
        token = await users_repo.get_clickup_token(session, user.id)
        if token:
            try:
                lst = await clickup.ClickUpClient(token).get_list(list_id)
                list_name = lst.get("name")
            except clickup.ClickUpError as exc:
                log.warning("clickup.list_lookup_failed", list_id=list_id, detail=str(exc))

    row = await clients_repo.set_clickup_list(session, client_id, list_id, list_name)
    if row is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    await session.commit()
    return row


@router.post("/clients/{client_id}/push-clickup")
async def push_to_clickup(
    client_id: str,
    session: AsyncSession = Depends(get_admin_session),
    user: User = Depends(get_current_admin),
) -> dict:
    """Push (create or update) one ClickUp task per Pulse card. Cards with
    `clickup_task_id` already set get UPDATEd; others get CREATEd and the
    returned id is stored. File-upload cards push attachments after the
    task is created/updated.

    Returns a structured summary so the operator sees exactly what
    happened — including per-card errors when a single push fails."""
    # Pre-flight: user connected? engagement configured?
    token = await users_repo.get_clickup_token(session, user.id)
    if not token:
        raise HTTPException(status_code=400, detail="clickup not connected for this user")

    engagement = await clients_repo.get_by_id(session, client_id)
    if engagement is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    list_id = engagement.get("clickup_list_id")
    if not list_id:
        raise HTTPException(status_code=400, detail="engagement has no clickup_list_id; set one first")

    cards = await cards_repo.list_for_client(session, client_id)
    responses_list = await responses_repo.list_for_client(session, client_id)
    uploads = await uploads_repo.list_for_client(session, client_id)

    # Index responses by card_id and uploads by card_id for fast lookup.
    response_by_card = {r["card_id"]: r for r in responses_list}
    uploads_by_card: dict[str, list[dict]] = {}
    for u in uploads:
        uploads_by_card.setdefault(u["card_id"], []).append(u)

    api = clickup.ClickUpClient(token)
    created: list[str] = []
    updated: list[str] = []
    attached = 0
    errors: list[dict] = []

    # Load each card with its existing clickup_task_id from the DB so we
    # know create-vs-update. list_for_client doesn't include that column
    # by design (admin-only), so do a separate small SELECT.
    from sqlalchemy import text as _sql_text
    task_id_rows = (
        await session.execute(
            _sql_text("select id::text, clickup_task_id from public.cards where client_id = cast(:c as uuid)"),
            {"c": client_id},
        )
    ).mappings().all()
    task_id_by_card = {r["id"]: r["clickup_task_id"] for r in task_id_rows}

    for card in cards:
        response = response_by_card.get(card["id"])
        status = clickup_export.suggest_status(card, response)
        body = clickup_export.render_response_body(
            card, response, uploads_by_card.get(card["id"], [])
        )
        task_payload = {
            "name": card["title"],
            "description": body,
            "status": status,
        }

        existing_task_id = task_id_by_card.get(card["id"])
        try:
            if existing_task_id:
                await api.update_task(existing_task_id, task_payload)
                task_id = existing_task_id
                updated.append(card["id"])
            else:
                resp = await api.create_task(list_id, task_payload)
                task_id = str(resp.get("id") or "")
                if task_id:
                    await cards_repo.set_clickup_task_id(session, card["id"], task_id)
                    created.append(card["id"])
                else:
                    errors.append({"card_id": card["id"], "error": "create_task returned no id"})
                    continue
        except clickup.ClickUpError as exc:
            errors.append({"card_id": card["id"], "error": str(exc)})
            continue

        # Push attachments. Each failure is per-file, not fatal.
        for u in uploads_by_card.get(card["id"], []):
            try:
                path = storage.resolve_within_upload_dir(u["storage_path"])
                if not path.exists():
                    errors.append({"upload_id": u["id"], "error": "file missing on disk"})
                    continue
                await api.upload_attachment(
                    task_id,
                    filename=u["file_name"],
                    content=path.read_bytes(),
                    mime_type=u.get("mime_type") or "application/octet-stream",
                )
                attached += 1
            except (clickup.ClickUpError, storage.StoragePathError) as exc:
                errors.append({"upload_id": u["id"], "error": str(exc)})

    await session.commit()
    return {
        "created": created,
        "updated": updated,
        "attached": attached,
        "errors": errors,
    }


@router.post("/clients/{client_id}/rotate-token")
async def rotate_token(
    client_id: str,
    session: AsyncSession = Depends(get_admin_session),
    _: User = Depends(get_current_admin),
) -> dict[str, Any]:
    row = await clients_repo.rotate_token(session, client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    await session.commit()
    return row


# ── Cards ──────────────────────────────────────────────────────────────────


@router.post("/clients/{client_id}/cards", status_code=201)
async def add_card(
    client_id: str,
    req: CreateCardRequest,
    session: AsyncSession = Depends(get_admin_session),
    _: User = Depends(get_current_admin),
) -> dict[str, Any]:
    # Verify the engagement exists; cleaner 404 than a FK violation
    if (await clients_repo.get_by_id(session, client_id)) is None:
        raise HTTPException(status_code=404, detail="engagement not found")

    row = await cards_repo.create_card(
        session,
        client_id=client_id,
        category=req.category,
        title=req.title,
        context=req.context,
        question=req.question,
        response_type=req.response_type,
        options=req.options,
        default_value=req.default_value,
        skip_allowed=req.skip_allowed,
        attachment_path=req.attachment_path,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="card creation failed")
    await session.commit()
    return row


@router.patch("/cards/{card_id}")
async def update_card(
    card_id: str,
    req: UpdateCardRequest,
    session: AsyncSession = Depends(get_admin_session),
    _: User = Depends(get_current_admin),
) -> dict[str, Any]:
    fields = req.model_dump(exclude_unset=True)
    row = await cards_repo.update_card(session, card_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="card not found")
    await session.commit()
    return row


@router.delete("/cards/{card_id}", status_code=204)
async def delete_card(
    card_id: str,
    session: AsyncSession = Depends(get_admin_session),
    _: User = Depends(get_current_admin),
) -> None:
    deleted = await cards_repo.delete_card(session, card_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="card not found")
    await session.commit()


# ── Admin downloads (BYPASSRLS — admin can fetch any client's file) ────────


@router.get("/uploads/{upload_id}/download")
async def admin_download_upload(
    upload_id: str,
    session: AsyncSession = Depends(get_admin_session),
    _: User = Depends(get_current_admin),
) -> FileResponse:
    row = await uploads_repo.admin_get_by_id(session, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="upload not found")
    try:
        path = storage.resolve_within_upload_dir(row["storage_path"])
    except storage.StoragePathError:
        raise HTTPException(status_code=500, detail="invalid storage path") from None
    if not path.exists():
        raise HTTPException(status_code=404, detail="file missing on disk")
    return FileResponse(
        path=path,
        media_type=row["mime_type"] or "application/octet-stream",
        filename=row["file_name"],
    )
