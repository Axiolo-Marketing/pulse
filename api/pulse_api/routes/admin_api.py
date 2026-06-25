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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import email as email_module
from pulse_api import storage
from pulse_api.audit import record_audit
from pulse_api.auth.email_messages import engagement_invite_email
from pulse_api.auth.middleware import (
    get_current_org_member,
    get_org_scoped_session,
)
from pulse_api.card_import import CardImportError, parse_markdown
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models import OrganizationMembership, User
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import clients as clients_repo
from pulse_api.repos import engagements as engagements_repo
from pulse_api.repos import recipients as recipients_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import uploads as uploads_repo

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_org_member)],
)


# ── Request/response models ────────────────────────────────────────────────


class CreateEngagementRequest(BaseModel):
    """New-engagement payload.

    Provide EITHER an existing ``client_id`` (chosen from the
    autocomplete) OR a ``client_name`` (typed free-form); the route
    resolves a real client, get-or-creating one by name when only
    ``client_name`` is given. At least one of the two is required.
    """

    client_id: str | None = None
    client_name: str | None = Field(default=None, min_length=1, max_length=200)
    engagement_name: str | None = None


class UpdateEngagementRequest(BaseModel):
    """Partial update. Fields omitted from the request body stay as-is.

    The customer-facing name lives on the owning ``Client`` now and is
    not editable through this path; magic-link tokens live on recipients.
    ``voice_enabled`` toggles the per-engagement voice recorder and
    ``reminders_enabled`` pauses/resumes the scheduled reminder fan-out;
    omitting either (the `model_dump(exclude_unset=True)` path) leaves it
    untouched."""

    engagement_name: str | None = None
    brief: str | None = None
    voice_enabled: bool | None = None
    reminders_enabled: bool | None = None


class AddRecipientRequest(BaseModel):
    """Add a respondent to an engagement. ``email`` is required (it's the
    identifier and where invites/reminders go); ``name`` is an optional
    label used to greet the recipient."""

    email: str = Field(min_length=3, max_length=320)
    name: str | None = Field(default=None, max_length=200)


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
    admin_session: AsyncSession = Depends(get_admin_session),
    _: tuple[User, OrganizationMembership] = Depends(get_current_org_member),
) -> list[dict[str, Any]]:
    """List engagements visible to the active org — RLS handles the scope.

    The owner display fields (``owner_name`` / ``owner_email``) are
    enriched in a second pass against the BYPASSRLS ``admin_session``
    because ``users`` is not granted to ``pulse_member`` (same two-pass
    pattern the activity feed uses).
    """
    rows = await engagements_repo.list_all_with_counts(session)
    # Drop any SET LOCAL ROLE the test harness left on this shared admin session
    # from a prior member-scoped query, so enrich_owner_display's unrestricted
    # `users` read runs as the BYPASSRLS admin role (no-op in production, where
    # the admin session is a separate engine).
    await admin_session.execute(text("reset role"))
    return await engagements_repo.enrich_owner_display(admin_session, rows)


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
        "recipients": await recipients_repo.list_for_engagement(session, engagement_id),
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

    Resolves the owning client first: an existing ``client_id`` (verified
    in-org via RLS) or a ``client_name`` that get-or-creates a real
    client. ``org_id`` + ``created_by`` come from the resolved membership,
    never the wire body — RLS WITH CHECK would reject any other org anyway.

    The real client is auto-created as a side effect of the engagement
    create; it is intentionally NOT separately audited (the
    ``engagement.create`` row covers it).
    """
    user, membership = org_member
    client_id = await _resolve_client_id(session, req, membership.org_id)
    row = await engagements_repo.create_engagement(
        session,
        client_id=client_id,
        engagement_name=req.engagement_name,
        org_id=str(membership.org_id),
        created_by=str(user.id),
    )
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="engagement.create",
        target_type="engagement",
        target_id=row["id"],
        metadata={"name": row.get("name")},
    )
    await session.commit()
    return row


async def _resolve_client_id(
    session: AsyncSession,
    req: CreateEngagementRequest,
    org_id: Any,
) -> str:
    """Resolve the owning client for a new engagement.

    Prefers an explicit ``client_id`` (404 if it doesn't resolve in the
    active org — RLS hides cross-org clients). Falls back to
    get-or-creating a client by ``client_name``. Raises 422 when neither
    is provided.
    """
    if req.client_id is not None:
        client = await clients_repo.get_by_id(session, req.client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="client not found")
        return str(client["id"])
    if req.client_name is not None:
        name = req.client_name.strip()
        if not name:
            raise HTTPException(
                status_code=422, detail="client_name must not be blank"
            )
        client = await clients_repo.get_or_create(
            session, org_id=org_id, name=name
        )
        return str(client["id"])
    raise HTTPException(
        status_code=422, detail="client_id or client_name is required"
    )


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
    row = await engagements_repo.update_engagement(session, engagement_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="engagement.update",
        target_type="engagement",
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
        action="engagement.delete",
        target_type="engagement",
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
        action="engagement.reset",
        target_type="engagement",
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


# ── Recipients (per-engagement respondents) ────────────────────────────────


@router.get("/engagements/{engagement_id}/recipients")
async def list_recipients(
    engagement_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    _: tuple[User, OrganizationMembership] = Depends(get_current_org_member),
) -> list[dict[str, Any]]:
    """Recipients on one engagement, each with its own progress rollup. RLS
    scopes to the active org."""
    if (await engagements_repo.get_by_id(session, engagement_id)) is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    return await recipients_repo.list_for_engagement(session, engagement_id)


@router.post("/engagements/{engagement_id}/recipients", status_code=201)
async def add_recipient(
    engagement_id: str,
    req: AddRecipientRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    """Add a respondent and mint their private deck token. The operator
    sends the invite separately. 404 if the engagement isn't in the active
    org; 409 if the email is already a recipient of this engagement."""
    user, membership = org_member
    if (await engagements_repo.get_by_id(session, engagement_id)) is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    email = req.email.strip()
    if await recipients_repo.email_exists(
        session, engagement_id=engagement_id, email=email
    ):
        raise HTTPException(status_code=409, detail="recipient already added")
    row = await recipients_repo.add(
        session,
        engagement_id=engagement_id,
        org_id=str(membership.org_id),
        email=email,
        name=(req.name.strip() or None) if req.name else None,
    )
    if row is None:
        raise HTTPException(status_code=400, detail="could not add recipient")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="recipient.add",
        target_type="recipient",
        target_id=row["id"],
        metadata={"engagement_id": engagement_id, "email": email},
    )
    await session.commit()
    return row


@router.delete(
    "/engagements/{engagement_id}/recipients/{recipient_id}", status_code=204
)
async def remove_recipient(
    engagement_id: str,
    recipient_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> None:
    """Remove a recipient — the FK cascade wipes their responses/uploads and
    their deck link stops working. On-disk files are cleaned best-effort
    after commit."""
    user, membership = org_member
    upload_paths = await recipients_repo.list_upload_paths_for_recipient(
        session, recipient_id
    )
    removed = await recipients_repo.remove(
        session, engagement_id=engagement_id, recipient_id=recipient_id
    )
    if removed is None:
        raise HTTPException(status_code=404, detail="recipient not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="recipient.remove",
        target_type="recipient",
        target_id=recipient_id,
        metadata={"engagement_id": engagement_id, "email": removed.get("email")},
    )
    await session.commit()
    for path in upload_paths:
        storage.delete_upload(path)


@router.post("/engagements/{engagement_id}/send-invites")
async def send_invites(
    engagement_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, int]:
    """Email the deck link to every recipient who has an email but hasn't
    been invited yet (``invited_at is null``), then stamp ``invited_at`` —
    this replaces the operator's manual link-share. Refuses if the deck has
    no cards (nothing to answer yet). Sends are best-effort (``send_email``
    never raises); a recipient is marked invited once the attempt is made.
    Returns the number actually emailed (re-running only mails newcomers)."""
    user, membership = org_member
    engagement = await engagements_repo.get_by_id(session, engagement_id)
    if engagement is None:
        raise HTTPException(status_code=404, detail="engagement not found")
    cards = await cards_repo.list_for_engagement(session, engagement_id)
    if not cards:
        raise HTTPException(
            status_code=400,
            detail="add at least one card before sending invites",
        )
    pending = await recipients_repo.list_pending_invites(session, engagement_id)
    if not pending:
        return {"sent": 0}

    org_name = (
        await session.execute(
            text("select name from public.organizations where id = cast(:o as uuid)"),
            {"o": str(membership.org_id)},
        )
    ).scalar_one_or_none() or "Your consultant"

    base = settings.frontend_base_url.rstrip("/")
    for r in pending:
        subject, body = engagement_invite_email(
            deck_url=f"{base}/?t={r['token']}",
            org_name=str(org_name),
            recipient_name=r.get("name"),
            engagement_name=engagement.get("engagement_name"),
        )
        await email_module.send_email(r["email"], subject, body)

    await recipients_repo.mark_invited(session, [r["id"] for r in pending])
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="engagement.invites_sent",
        target_type="engagement",
        target_id=engagement_id,
        metadata={"count": len(pending)},
    )
    await session.commit()
    return {"sent": len(pending)}


# ── Clients (real clients/companies) ───────────────────────────────────────


@router.get("/clients")
async def list_clients(
    session: AsyncSession = Depends(get_org_scoped_session),
    _: tuple[User, OrganizationMembership] = Depends(get_current_org_member),
) -> list[dict[str, Any]]:
    """List the active org's real clients, ordered by name.

    Powers the admin list's client grouping and the new-engagement
    autocomplete. RLS narrows the result to the active org. Clients are
    auto-created as a side effect of ``POST /engagements`` (with a
    ``client_name``), so there is no create/update/delete route here.
    """
    return await clients_repo.list_for_org(session)


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
            "engagement_id": engagement_id,
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
        target_type="engagement",
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
