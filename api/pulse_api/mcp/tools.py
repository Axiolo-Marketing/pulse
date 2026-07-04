"""MCP tool implementations.

Every tool here is a thin wrapper over an existing
``pulse_api.repos.*`` helper or ``pulse_api.storage`` function. The MCP
layer holds zero business logic — if you find yourself reaching for a
new behaviour, fix it in the REST endpoint first and re-export it here.

Mappings to the REST routes (see ``routes/admin_api.py`` and
``routes/attachments.py``):

    pulse_list_engagements      ←  GET    /api/admin/engagements
    pulse_get_engagement        ←  GET    /api/admin/engagements/{id}
    pulse_create_engagement     ←  POST   /api/admin/engagements
    pulse_update_engagement     ←  PATCH  /api/admin/engagements/{id}
    pulse_delete_engagement     ←  DELETE /api/admin/engagements/{id}
    pulse_list_recipients       ←  GET    /api/admin/engagements/{id}/recipients
    pulse_add_recipient         ←  POST   /api/admin/engagements/{id}/recipients
    pulse_import_deck           ←  POST   /api/admin/engagements/{id}/cards/import-markdown
    pulse_add_card              ←  POST   /api/admin/engagements/{id}/cards
    pulse_update_card           ←  PATCH  /api/admin/cards/{id}
    pulse_delete_card           ←  DELETE /api/admin/cards/{id}
    pulse_upload_attachment     ←  POST   /api/admin/attachments

Each tool authenticates first via ``authenticate_request``, which
returns ``(user, org_id)`` read off the access token validated at the
HTTP layer (a legacy ``pulse_<key>`` API key or an OAuth grant). The
tool then opens a member-scoped session against ``org_id`` so every
query is RLS-narrowed to the right tenant. A missing / invalid /
revoked credential, or one whose user lost their membership, never
reaches a tool body — ``RequireAuthMiddleware`` 401s the request first
(same outcome as the REST middleware's 403).

Every mutating tool also writes the same ``audit_logs`` row its REST
twin writes, via ``pulse_api.audit.record_audit`` on the same
member-scoped session, before the commit — an MCP-driven mutation must
show up in the org's Activity feed exactly like its REST equivalent
does. ``pulse_add_recipient`` additionally fires the pending-invite
send (``routes.admin_api._send_pending_invites``, imported rather than
duplicated) so a respondent added via MCP gets the same auto-invite
email a respondent added through the admin UI gets.
"""
from __future__ import annotations

import base64
from typing import Any

from mcp.server.fastmcp.server import Context

from pulse_api import storage
from pulse_api.audit import record_audit
from pulse_api.card_import import CardImportError, parse_markdown
from pulse_api.config import settings
from pulse_api.mcp.server import (
    _open_admin_session,
    _open_member_session,
    authenticate_request,
    mcp,
)
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import clients as clients_repo
from pulse_api.repos import engagements as engagements_repo
from pulse_api.repos import recipients as recipients_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import uploads as uploads_repo
from pulse_api.routes.admin_api import _send_pending_invites

# ── Engagements ──────────────────────────────────────────────────────────


@mcp.tool(
    name="pulse_list_engagements",
    description=(
        "List every engagement with a per-recipient progress rollup "
        "(`recipients_count`, `completed_recipients`, `total_cards`) plus "
        "its owning client (`client_id` + `client_name`) and owner "
        "(`owner_name` / `owner_email`, null when unattributed). No arguments."
    ),
)
async def pulse_list_engagements(ctx: Context) -> list[dict[str, Any]]:
    _, org_id = await authenticate_request(ctx)
    async with _open_member_session(org_id) as session:
        rows = await engagements_repo.list_all_with_counts(session)
    # Owner display fields come from a separate BYPASSRLS session — the
    # member session has no grant on ``users`` (same two-pass pattern the
    # REST list + activity feed use).
    async with _open_admin_session() as admin_session:
        return await engagements_repo.enrich_owner_display(admin_session, rows)


@mcp.tool(
    name="pulse_get_engagement",
    description=(
        "Fetch one engagement with its recipients, cards, responses, and "
        "uploads. Each response/upload carries a `recipient_id`; match it "
        "against `recipients[].id` to attribute answers to a respondent. "
        "Returns 'not found' if no engagement matches the id."
    ),
)
async def pulse_get_engagement(
    ctx: Context, engagement_id: str
) -> dict[str, Any]:
    _, org_id = await authenticate_request(ctx)
    async with _open_member_session(org_id) as session:
        engagement = await engagements_repo.get_by_id(session, engagement_id)
        if engagement is None:
            raise ValueError("engagement not found")
        return {
            "engagement": engagement,
            "recipients": await recipients_repo.list_for_engagement(
                session, engagement_id
            ),
            "cards": await cards_repo.list_for_engagement(session, engagement_id),
            "responses": await responses_repo.list_for_engagement(session, engagement_id),
            "uploads": await uploads_repo.list_for_engagement(session, engagement_id),
        }


@mcp.tool(
    name="pulse_create_engagement",
    description=(
        "Create a new engagement under a client. Pass `client_name` (the "
        "company name); an existing client is reused, otherwise one is "
        "created. Returns the new engagement row (id, `client_id`, `name`, "
        "etc.) — NOT a deck link. The magic-link `?t=` URL lives on a "
        "recipient: call `pulse_add_recipient` next to mint one."
    ),
)
async def pulse_create_engagement(
    ctx: Context,
    client_name: str,
    engagement_name: str | None = None,
) -> dict[str, Any]:
    user, org_id = await authenticate_request(ctx)
    async with _open_member_session(org_id) as session:
        client = await clients_repo.get_or_create(
            session, org_id=org_id, name=client_name
        )
        row = await engagements_repo.create_engagement(
            session,
            client_id=str(client["id"]),
            engagement_name=engagement_name,
            org_id=org_id,
            created_by=str(user.id),
        )
        await record_audit(
            session,
            org_id=org_id,
            user_id=user.id,
            action="engagement.create",
            target_type="engagement",
            target_id=row["id"],
            metadata={"name": row.get("name")},
        )
        await session.commit()
        return row


@mcp.tool(
    name="pulse_update_engagement",
    description=(
        "Patch an engagement. Only the provided fields change; unspecified "
        "fields stay as-is. The customer-facing name lives on the client "
        "(not editable here); deck links live on recipients. Set "
        "`reminders_enabled` to pause/resume scheduled reminders for this "
        "engagement."
    ),
)
async def pulse_update_engagement(
    ctx: Context,
    engagement_id: str,
    engagement_name: str | None = None,
    brief: str | None = None,
    reminders_enabled: bool | None = None,
) -> dict[str, Any]:
    user, org_id = await authenticate_request(ctx)
    fields: dict[str, Any] = {}
    if engagement_name is not None:
        fields["engagement_name"] = engagement_name
    if brief is not None:
        fields["brief"] = brief
    if reminders_enabled is not None:
        fields["reminders_enabled"] = reminders_enabled

    async with _open_member_session(org_id) as session:
        row = await engagements_repo.update_engagement(session, engagement_id, fields)
        if row is None:
            raise ValueError("engagement not found")
        await record_audit(
            session,
            org_id=org_id,
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


# ── Recipients ───────────────────────────────────────────────────────────


@mcp.tool(
    name="pulse_list_recipients",
    description=(
        "List an engagement's recipients (respondents). Each carries its own "
        "`token` (the `?t=` deck link), `email`, and per-recipient progress "
        "(`completed_count` / `total_cards`)."
    ),
)
async def pulse_list_recipients(
    ctx: Context, engagement_id: str
) -> list[dict[str, Any]]:
    _, org_id = await authenticate_request(ctx)
    async with _open_member_session(org_id) as session:
        if await engagements_repo.get_by_id(session, engagement_id) is None:
            raise ValueError("engagement not found")
        return await recipients_repo.list_for_engagement(session, engagement_id)


@mcp.tool(
    name="pulse_add_recipient",
    description=(
        "Add a respondent to an engagement and mint their private deck link. "
        "Pass `email` (required) and optional `name`. Returns the recipient "
        "row including `token`; the deck URL is `{frontend_base_url}/?t={token}`. "
        "Errors if the email is already a recipient of this engagement."
    ),
)
async def pulse_add_recipient(
    ctx: Context,
    engagement_id: str,
    email: str,
    name: str | None = None,
) -> dict[str, Any]:
    user, org_id = await authenticate_request(ctx)
    async with _open_member_session(org_id) as session:
        engagement = await engagements_repo.get_by_id(session, engagement_id)
        if engagement is None:
            raise ValueError("engagement not found")
        clean = email.strip()
        if await recipients_repo.email_exists(
            session, engagement_id=engagement_id, email=clean
        ):
            raise ValueError("recipient already added")
        row = await recipients_repo.add(
            session,
            engagement_id=engagement_id,
            org_id=org_id,
            email=clean,
            name=(name.strip() or None) if name else None,
        )
        if row is None:
            raise ValueError("could not add recipient")
        await record_audit(
            session,
            org_id=org_id,
            user_id=user.id,
            action="recipient.add",
            target_type="recipient",
            target_id=row["id"],
            metadata={"engagement_id": engagement_id, "email": clean},
        )
        await session.commit()
        # Send the invite immediately, same as the REST twin — runs AFTER
        # the recipient-add commit above so the email network call never
        # holds that write's transaction open (audit finding M7).
        await _send_pending_invites(
            session, engagement=engagement, org_id=org_id, user=user
        )
        return row


@mcp.tool(
    name="pulse_delete_engagement",
    description=(
        "Permanently delete an engagement. FK cascades wipe its cards, "
        "responses, and uploads in the same transaction; on-disk files are "
        "removed best-effort after commit. The access URL stops working "
        "immediately."
    ),
)
async def pulse_delete_engagement(
    ctx: Context, engagement_id: str
) -> dict[str, bool]:
    user, org_id = await authenticate_request(ctx)
    async with _open_member_session(org_id) as session:
        # Capture the name before the delete so the audit log can render
        # something more useful than a UUID once the row is gone.
        snapshot = await engagements_repo.get_by_id(session, engagement_id)
        upload_paths = await engagements_repo.list_upload_paths_for_engagement(
            session, engagement_id
        )
        deleted = await engagements_repo.delete_engagement(session, engagement_id)
        if not deleted:
            raise ValueError("engagement not found")
        await record_audit(
            session,
            org_id=org_id,
            user_id=user.id,
            action="engagement.delete",
            target_type="engagement",
            target_id=engagement_id,
            metadata={"name": (snapshot or {}).get("name") if snapshot else None},
        )
        await session.commit()

    # Best-effort disk cleanup after the DB-side commit, identical to the
    # REST DELETE flow.
    for path in upload_paths:
        storage.delete_upload(path)
    return {"ok": True}


# ── Cards ────────────────────────────────────────────────────────────────


@mcp.tool(
    name="pulse_import_deck",
    description=(
        "Bulk-import cards from a Pulse-card markdown document. Atomic: "
        "all cards land or none do. Cards append to the end of the "
        "existing deck."
    ),
)
async def pulse_import_deck(
    ctx: Context, engagement_id: str, markdown: str
) -> dict[str, Any]:
    user, org_id = await authenticate_request(ctx)
    async with _open_member_session(org_id) as session:
        if (await engagements_repo.get_by_id(session, engagement_id)) is None:
            raise ValueError("engagement not found")
        try:
            parsed = parse_markdown(markdown)
        except CardImportError as exc:
            detail = "\n".join(exc.errors) if exc.errors else str(exc)
            raise ValueError(detail) from exc

        created: list[dict[str, Any]] = []
        for card in parsed:
            row = await cards_repo.create_card(
                session,
                engagement_id=engagement_id,
                org_id=org_id,
                **card.to_create_kwargs(),
            )
            if row is None:
                raise ValueError(f"failed to insert card {card.title!r}")
            created.append(row)
        await record_audit(
            session,
            org_id=org_id,
            user_id=user.id,
            action="card.import",
            target_type="engagement",
            target_id=engagement_id,
            metadata={"count": len(created)},
        )
        await session.commit()
        return {"created": created}


@mcp.tool(
    name="pulse_add_card",
    description=(
        "Append one card to an engagement's deck. `response_type` must be "
        "one of: confirm-edit, single-select, multi-select, short-text, "
        "long-text, file-upload, document-link, contact-share."
    ),
)
async def pulse_add_card(
    ctx: Context,
    engagement_id: str,
    category: str,
    title: str,
    context: str,
    question: str,
    response_type: str,
    options: list[str] | None = None,
    default_value: str | None = None,
    skip_allowed: bool = True,
    attachment_path: str | None = None,
) -> dict[str, Any]:
    user, org_id = await authenticate_request(ctx)
    async with _open_member_session(org_id) as session:
        if (await engagements_repo.get_by_id(session, engagement_id)) is None:
            raise ValueError("engagement not found")
        row = await cards_repo.create_card(
            session,
            engagement_id=engagement_id,
            category=category,
            title=title,
            context=context,
            question=question,
            response_type=response_type,
            options=options,
            default_value=default_value,
            skip_allowed=skip_allowed,
            attachment_path=attachment_path,
            org_id=org_id,
        )
        if row is None:
            raise ValueError("card creation failed")
        await record_audit(
            session,
            org_id=org_id,
            user_id=user.id,
            action="card.create",
            target_type="card",
            target_id=row["id"],
            metadata={
                "engagement_id": engagement_id,
                "title": row.get("title"),
                "response_type": response_type,
            },
        )
        await session.commit()
        return row


@mcp.tool(
    name="pulse_update_card",
    description=(
        "Patch a card. response_type is intentionally not patchable — "
        "changing it would invalidate existing responses."
    ),
)
async def pulse_update_card(
    ctx: Context,
    card_id: str,
    category: str | None = None,
    title: str | None = None,
    context: str | None = None,
    question: str | None = None,
    options: list[str] | None = None,
    default_value: str | None = None,
    skip_allowed: bool | None = None,
    attachment_path: str | None = None,
) -> dict[str, Any]:
    user, org_id = await authenticate_request(ctx)
    fields: dict[str, Any] = {}
    if category is not None:
        fields["category"] = category
    if title is not None:
        fields["title"] = title
    if context is not None:
        fields["context"] = context
    if question is not None:
        fields["question"] = question
    if options is not None:
        fields["options"] = options
    if default_value is not None:
        fields["default_value"] = default_value
    if skip_allowed is not None:
        fields["skip_allowed"] = skip_allowed
    if attachment_path is not None:
        fields["attachment_path"] = attachment_path

    async with _open_member_session(org_id) as session:
        row = await cards_repo.update_card(session, card_id, fields)
        if row is None:
            raise ValueError("card not found")
        await record_audit(
            session,
            org_id=org_id,
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


@mcp.tool(
    name="pulse_delete_card",
    description="Permanently delete one card.",
)
async def pulse_delete_card(
    ctx: Context, card_id: str
) -> dict[str, bool]:
    user, org_id = await authenticate_request(ctx)
    async with _open_member_session(org_id) as session:
        # Snapshot the title BEFORE deletion so the audit log can render
        # something more useful than a UUID once the row is gone.
        snapshot_title = await cards_repo.peek_title(session, card_id)
        deleted = await cards_repo.delete_card(session, card_id)
        if not deleted:
            raise ValueError("card not found")
        await record_audit(
            session,
            org_id=org_id,
            user_id=user.id,
            action="card.delete",
            target_type="card",
            target_id=card_id,
            metadata={"title": snapshot_title},
        )
        await session.commit()
        return {"ok": True}


# ── Attachments ──────────────────────────────────────────────────────────


@mcp.tool(
    name="pulse_upload_attachment",
    description=(
        "Upload a static reference file (HTML/PDF/image/SVG). Provide the "
        "filename plus the file bytes base64-encoded. Returns the "
        "attachment path you can pass to pulse_add_card/pulse_update_card "
        "as `attachment_path`. Files larger than the configured limit are "
        "rejected before any disk write."
    ),
)
async def pulse_upload_attachment(
    ctx: Context, filename: str, content_base64: str
) -> dict[str, str]:
    # Auth runs purely as the gate — attachments are not org-scoped on
    # disk (unguessable URL), so we don't need a member session here.
    await authenticate_request(ctx)
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("invalid base64 payload") from exc

    if len(content) == 0:
        raise ValueError("empty file")
    if len(content) > settings.max_upload_bytes:
        # Guard runs BEFORE build_attachment_path / write_upload — never
        # write the file to disk only to delete it after a size check.
        raise ValueError(
            f"file too large: {len(content)} bytes exceeds limit of "
            f"{settings.max_upload_bytes} bytes"
        )

    try:
        relative_path, mime_type = storage.build_attachment_path(filename)
    except storage.StoragePathError as exc:
        raise ValueError(str(exc)) from exc

    storage.write_upload(relative_path=relative_path, content=content)
    return {"path": relative_path, "mime_type": mime_type}
