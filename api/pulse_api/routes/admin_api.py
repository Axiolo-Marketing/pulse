"""Admin-only endpoints. Every route is gated by the
``get_current_org_member`` dep and runs queries on the ``pulse_member``
session yielded by ``get_org_scoped_session``.

The role-flip happens at dep injection: the session has no BYPASSRLS
and the ``pulse.org_id`` GUC is set to the operator's active org. A
forgotten ``where org_id = ...`` in a route handler therefore cannot
leak — RLS narrows every query down to the active org's rows.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import storage
from pulse_api.audit import record_audit
from pulse_api.auth.middleware import (
    get_current_org_member,
    get_org_scoped_session,
)
from pulse_api.card_import import CardImportError, parse_markdown
from pulse_api.models import OrganizationMembership, User
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import engagements as engagements_repo
from pulse_api.repos import groups as groups_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import uploads as uploads_repo

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_org_member)],
)


# ── Request/response models ────────────────────────────────────────────────


class CreateEngagementRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    org_name: str | None = None
    engagement_name: str | None = None


class UpdateEngagementRequest(BaseModel):
    """Partial update. Fields omitted from the request body stay as-is.
    `token` is intentionally not accepted here — rotation goes through
    its own POST endpoint so it's an explicit action.

    `group_id` moves the engagement into a folder; send `null` to
    ungroup it (move to the implicit "Ungrouped" bucket). It's
    distinguished from "not provided" via `model_dump(exclude_unset=True)`
    in the handler, so a body without the key never touches the column.

    `voice_enabled` toggles the per-engagement voice recorder. Omitting it
    (the same `exclude_unset` path) leaves the flag untouched."""

    name: str | None = None
    org_name: str | None = None
    engagement_name: str | None = None
    brief: str | None = None
    group_id: str | None = None
    voice_enabled: bool | None = None


class GroupRequest(BaseModel):
    """Create/rename payload for an engagement folder."""

    name: str = Field(min_length=1, max_length=200)


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


class ImportMarkdownRequest(BaseModel):
    markdown: str = Field(min_length=1, max_length=500_000)


# ── Engagement (engagements table) ─────────────────────────────────────────


@router.get("/engagements")
async def list_engagements(
    session: AsyncSession = Depends(get_org_scoped_session),
    _: tuple[User, OrganizationMembership] = Depends(get_current_org_member),
) -> list[dict[str, Any]]:
    """List engagements visible to the active org — RLS handles the scope."""
    return await engagements_repo.list_all_with_counts(session)


@router.get("/engagements/{engagement_id}")
async def get_engagement(
    engagement_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    _: tuple[User, OrganizationMembership] = Depends(get_current_org_member),
) -> dict[str, Any]:
    engagement = await engagements_repo.get_by_id(session, engagement_id)
    if engagement is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return {
        "engagement": engagement,
        "cards": await cards_repo.list_for_engagement(session, engagement_id),
        "responses": await responses_repo.list_for_engagement(session, engagement_id),
        "uploads": await uploads_repo.list_for_engagement(session, engagement_id),
    }


@router.post("/engagements", status_code=201)
async def create_engagement(
    req: CreateEngagementRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    """Create a new engagement under the operator's active organization.

    ``org_id`` comes from the resolved membership — never from the wire
    body. RLS WITH CHECK would reject any other value anyway.
    """
    user, membership = org_member
    row = await engagements_repo.create_engagement(
        session,
        name=req.name,
        org_name=req.org_name,
        engagement_name=req.engagement_name,
        org_id=str(membership.org_id),
    )
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="client.create",
        target_type="client",
        target_id=row["id"],
        metadata={"name": row.get("name")},
    )
    await session.commit()
    return row


@router.patch("/engagements/{engagement_id}")
async def update_engagement(
    engagement_id: str,
    req: UpdateEngagementRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    user, membership = org_member
    fields = req.model_dump(exclude_unset=True)
    # A non-null group_id must reference a folder in the caller's org.
    # RLS already blocks cross-org folders (a foreign id matches no row,
    # so the FK update would null it silently), but we reject explicitly
    # with 404 so a typo / stale id surfaces instead of quietly ungrouping.
    target_group = fields.get("group_id")
    if target_group is not None:
        if await groups_repo.get_by_id(session, target_group) is None:
            raise HTTPException(status_code=404, detail="folder not found")
    row = await engagements_repo.update_engagement(session, engagement_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="client.update",
        target_type="client",
        target_id=engagement_id,
        metadata={
            "changed_fields": sorted(fields.keys()),
            "name": row.get("name"),
        },
    )
    await session.commit()
    return row


@router.delete("/engagements/{engagement_id}", status_code=204)
async def delete_engagement(
    engagement_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> None:
    """Permanent delete. FK cascades wipe cards/responses/uploads in the
    same transaction; on-disk upload files are removed best-effort after
    the commit. The token's URL stops working immediately.
    """
    user, membership = org_member
    # Capture the name before the delete so the audit log can render
    # something more useful than a UUID once the row is gone.
    snapshot = await engagements_repo.get_by_id(session, engagement_id)
    upload_paths = await engagements_repo.list_upload_paths_for_engagement(
        session, engagement_id
    )
    deleted = await engagements_repo.delete_engagement(session, engagement_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="engagement not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="client.delete",
        target_type="client",
        target_id=engagement_id,
        metadata={"name": (snapshot or {}).get("name") if snapshot else None},
    )
    await session.commit()

    # Best-effort cleanup. A failure here leaves an orphaned file under
    # the upload dir, but the DB is the source of truth — no one can
    # reach the file via the API anymore.
    for path in upload_paths:
        storage.delete_upload(path)


@router.post("/engagements/{engagement_id}/reset")
async def reset_engagement(
    engagement_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, int]:
    """Reset an engagement for a clean restart: wipe every response and
    every upload (rows + on-disk files), returning all cards to an
    unanswered state. The cards, the engagement, and the magic link are
    left intact, so the same URL can be re-run by the client (or a fresh
    reviewer). Distinct from delete, which removes everything.

    Use when multiple people need to take the deck, or a client wants to
    start over."""
    user, membership = org_member
    if (await engagements_repo.get_by_id(session, engagement_id)) is None:
        raise HTTPException(status_code=404, detail="engagement not found")

    removed_uploads = await uploads_repo.delete_all_for_engagement(session, engagement_id)
    responses_cleared = await responses_repo.delete_all_for_engagement(session, engagement_id)
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="client.reset",
        target_type="client",
        target_id=engagement_id,
        metadata={
            "responses_cleared": responses_cleared,
            "uploads_cleared": len(removed_uploads),
        },
    )
    await session.commit()

    # Files removed after the DB change is durable, mirroring delete: a
    # failure here only leaves orphan files, never a row pointing at a
    # missing file. delete_upload is best-effort and never raises.
    for up in removed_uploads:
        storage.delete_upload(up["storage_path"])

    return {
        "responses_cleared": responses_cleared,
        "uploads_cleared": len(removed_uploads),
    }


# ── Engagement folders (engagement_groups table) ───────────────────────────


@router.get("/groups")
async def list_groups(
    session: AsyncSession = Depends(get_org_scoped_session),
    _: tuple[User, OrganizationMembership] = Depends(get_current_org_member),
) -> list[dict[str, Any]]:
    """List the active org's folders + per-folder engagement counts.

    RLS narrows the result to the active org. The list is ordered by
    name; empty folders are included (the UI still renders them).
    """
    return await groups_repo.list_for_org(session)


@router.post("/groups", status_code=201)
async def create_group(
    req: GroupRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    """Create a folder under the operator's active organization.

    ``org_id`` comes from the resolved membership — never the wire body.
    RLS WITH CHECK would reject any other value anyway.
    """
    user, membership = org_member
    row = await groups_repo.create(
        session, name=req.name, org_id=membership.org_id
    )
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="group.create",
        target_type="group",
        target_id=row["id"],
        metadata={"name": row.get("name")},
    )
    await session.commit()
    return row


@router.patch("/groups/{group_id}")
async def rename_group(
    group_id: str,
    req: GroupRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    user, membership = org_member
    row = await groups_repo.rename(session, group_id, req.name)
    if row is None:
        raise HTTPException(status_code=404, detail="folder not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="group.update",
        target_type="group",
        target_id=group_id,
        metadata={"name": row.get("name")},
    )
    await session.commit()
    return row


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> None:
    """Delete a folder. Its engagements are ungrouped, never deleted.

    The ``clients.group_id`` FK is ``on delete set null``, so any
    engagements in this folder return to the implicit "Ungrouped" bucket
    in the same transaction.
    """
    user, membership = org_member
    # Snapshot the name BEFORE the delete so the activity row renders a
    # label instead of a stale UUID once the row is gone.
    snapshot = await groups_repo.get_by_id(session, group_id)
    deleted = await groups_repo.delete(session, group_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="folder not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="group.delete",
        target_type="group",
        target_id=group_id,
        metadata={"name": (snapshot or {}).get("name") if snapshot else None},
    )
    await session.commit()


# ── Cards ──────────────────────────────────────────────────────────────────


@router.post("/engagements/{engagement_id}/cards", status_code=201)
async def add_card(
    engagement_id: str,
    req: CreateCardRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    # Verify the engagement exists; cleaner 404 than a FK violation.
    # RLS hides out-of-org engagements, so this also covers cross-org.
    if (await engagements_repo.get_by_id(session, engagement_id)) is None:
        raise HTTPException(status_code=404, detail="engagement not found")

    user, membership = org_member
    row = await cards_repo.create_card(
        session,
        engagement_id=engagement_id,
        category=req.category,
        title=req.title,
        context=req.context,
        question=req.question,
        response_type=req.response_type,
        options=req.options,
        default_value=req.default_value,
        skip_allowed=req.skip_allowed,
        attachment_path=req.attachment_path,
        org_id=str(membership.org_id),
    )
    if row is None:
        raise HTTPException(status_code=500, detail="card creation failed")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="card.create",
        target_type="card",
        target_id=row["id"],
        metadata={
            "client_id": engagement_id,
            "title": row.get("title"),
            "response_type": req.response_type,
        },
    )
    await session.commit()
    return row


@router.post("/engagements/{engagement_id}/cards/import-markdown", status_code=201)
async def import_cards_markdown(
    engagement_id: str,
    req: ImportMarkdownRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    """Bulk-import cards from a Pulse-card markdown document.

    Atomic: parse first, then insert all-or-nothing. Cards append to the
    end of the existing deck — the repo's `coalesce(max(order_index),0)+1`
    insert sees prior uncommitted rows in the same session, so ordering
    is stable.
    """
    if (await engagements_repo.get_by_id(session, engagement_id)) is None:
        raise HTTPException(status_code=404, detail="engagement not found")

    try:
        parsed = parse_markdown(req.markdown)
    except CardImportError as exc:
        # Join the per-card errors into a single message so the UI's
        # generic detail-string handler can surface them. Newline-joined
        # so the frontend can split for multi-line display.
        detail = "\n".join(exc.errors) if exc.errors else str(exc)
        raise HTTPException(status_code=400, detail=detail) from exc

    user, membership = org_member
    org_id = str(membership.org_id)
    created: list[dict[str, Any]] = []
    for card in parsed:
        row = await cards_repo.create_card(
            session,
            engagement_id=engagement_id,
            org_id=org_id,
            **card.to_create_kwargs(),
        )
        if row is None:
            raise HTTPException(
                status_code=500,
                detail=f"failed to insert card {card.title!r}",
            )
        created.append(row)

    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="card.import",
        target_type="client",
        target_id=engagement_id,
        # One audit row per import call, not per card — the bulk import
        # is the operator's single user action. ``count`` lets the UI
        # render "Tom imported 14 cards" without joining card rows.
        metadata={"count": len(created)},
    )
    await session.commit()
    return {"created": created}


@router.patch("/cards/{card_id}")
async def update_card(
    card_id: str,
    req: UpdateCardRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    user, membership = org_member
    fields = req.model_dump(exclude_unset=True)
    row = await cards_repo.update_card(session, card_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="card not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="card.update",
        target_type="card",
        target_id=card_id,
        metadata={
            "changed_fields": sorted(fields.keys()),
            "title": row.get("title"),
        },
    )
    await session.commit()
    return row


@router.delete("/cards/{card_id}", status_code=204)
async def delete_card(
    card_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> None:
    user, membership = org_member
    # Snapshot the card title BEFORE deletion so the activity row can
    # render "deleted card 'X'" instead of a stale UUID. The delete and
    # the audit insert commit atomically below.
    snapshot_title = await _peek_card_title(session, card_id)
    deleted = await cards_repo.delete_card(session, card_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="card not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="card.delete",
        target_type="card",
        target_id=card_id,
        metadata={"title": snapshot_title},
    )
    await session.commit()


async def _peek_card_title(
    session: AsyncSession, card_id: str
) -> str | None:
    """Return the card's title, or None if the row doesn't resolve.

    Used by the delete handler to capture the title BEFORE the row
    cascades away so the audit log can render a human-readable label.
    RLS scopes the read to the active org's cards.
    """
    from sqlalchemy import text as _text

    try:
        result = await session.execute(
            _text(
                "select title from public.cards where id = cast(:c as uuid)"
            ),
            {"c": card_id},
        )
    except Exception:
        # A malformed UUID raises before the where evaluates; the
        # surrounding handler will 404 on the delete anyway.
        return None
    row = result.mappings().one_or_none()
    return None if row is None else row.get("title")


# ── Admin downloads (org-scoped via RLS) ───────────────────────────────────


@router.get("/uploads/{upload_id}/download")
async def admin_download_upload(
    upload_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    _: tuple[User, OrganizationMembership] = Depends(get_current_org_member),
) -> FileResponse:
    """Stream the file behind ``upload_id``.

    The org-scoped session hides uploads tagged with a different org's
    id, so a cross-org download attempt yields 404 even if the operator
    knows the upload UUID.
    """
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
