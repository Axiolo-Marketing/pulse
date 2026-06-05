"""MCP tool implementations.

Every tool here is a thin wrapper over an existing
``pulse_api.repos.*`` helper or ``pulse_api.storage`` function. The MCP
layer holds zero business logic — if you find yourself reaching for a
new behaviour, fix it in the REST endpoint first and re-export it here.

Mappings to the REST routes (see ``routes/admin_api.py`` and
``routes/attachments.py``):

    pulse_list_engagements      ←  GET    /api/admin/clients
    pulse_get_engagement        ←  GET    /api/admin/clients/{id}
    pulse_create_engagement     ←  POST   /api/admin/clients
    pulse_update_engagement     ←  PATCH  /api/admin/clients/{id}
    pulse_delete_engagement     ←  DELETE /api/admin/clients/{id}
    pulse_rotate_token          ←  POST   /api/admin/clients/{id}/rotate-token
    pulse_import_deck           ←  POST   /api/admin/clients/{id}/cards/import-markdown
    pulse_add_card              ←  POST   /api/admin/clients/{id}/cards
    pulse_update_card           ←  PATCH  /api/admin/cards/{id}
    pulse_delete_card           ←  DELETE /api/admin/cards/{id}
    pulse_upload_attachment     ←  POST   /api/admin/attachments

Each tool authenticates first via ``authenticate_request``, which
returns ``(user, api_key)``. The tool then opens a member-scoped
session against ``api_key.org_id`` so every query is RLS-narrowed to
the right tenant. A missing / invalid / revoked key, or a key whose
user lost their membership, raises ``MCPAuthError`` before any work
happens — same outcome as the REST middleware's 403.
"""
from __future__ import annotations

import base64
from typing import Any

from mcp.server.fastmcp.server import Context

from pulse_api import storage
from pulse_api.card_import import CardImportError, parse_markdown
from pulse_api.config import settings
from pulse_api.mcp.server import (
    _open_member_session,
    authenticate_request,
    mcp,
)
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import clients as clients_repo
from pulse_api.repos import responses as responses_repo
from pulse_api.repos import uploads as uploads_repo


# ── Engagements ──────────────────────────────────────────────────────────


@mcp.tool(
    name="pulse_list_engagements",
    description=(
        "List every engagement with progress counts (answered, skipped, "
        "total cards). No arguments."
    ),
)
async def pulse_list_engagements(ctx: Context) -> list[dict[str, Any]]:
    _, api_key = await authenticate_request(ctx)
    async with _open_member_session(api_key.org_id) as session:
        return await clients_repo.list_all_with_counts(session)


@mcp.tool(
    name="pulse_get_engagement",
    description=(
        "Fetch one engagement with its cards, responses, and uploads. "
        "Returns 'not found' if no engagement matches the id."
    ),
)
async def pulse_get_engagement(
    ctx: Context, client_id: str
) -> dict[str, Any]:
    _, api_key = await authenticate_request(ctx)
    async with _open_member_session(api_key.org_id) as session:
        client = await clients_repo.get_by_id(session, client_id)
        if client is None:
            raise ValueError("engagement not found")
        return {
            "client": client,
            "cards": await cards_repo.list_for_client(session, client_id),
            "responses": await responses_repo.list_for_client(session, client_id),
            "uploads": await uploads_repo.list_for_client(session, client_id),
        }


@mcp.tool(
    name="pulse_create_engagement",
    description=(
        "Create a new engagement. Returns the new row including the "
        "freshly minted access token."
    ),
)
async def pulse_create_engagement(
    ctx: Context,
    name: str,
    org_name: str | None = None,
    engagement_name: str | None = None,
) -> dict[str, Any]:
    _, api_key = await authenticate_request(ctx)
    async with _open_member_session(api_key.org_id) as session:
        row = await clients_repo.create_engagement(
            session,
            name=name,
            org_name=org_name,
            engagement_name=engagement_name,
            org_id=str(api_key.org_id),
        )
        await session.commit()
        return row


@mcp.tool(
    name="pulse_update_engagement",
    description=(
        "Patch an engagement. Only the provided fields change; unspecified "
        "fields stay as-is. `token` cannot be set here — use "
        "pulse_rotate_token."
    ),
)
async def pulse_update_engagement(
    ctx: Context,
    client_id: str,
    name: str | None = None,
    org_name: str | None = None,
    engagement_name: str | None = None,
    brief: str | None = None,
) -> dict[str, Any]:
    _, api_key = await authenticate_request(ctx)
    fields: dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if org_name is not None:
        fields["org_name"] = org_name
    if engagement_name is not None:
        fields["engagement_name"] = engagement_name
    if brief is not None:
        fields["brief"] = brief

    async with _open_member_session(api_key.org_id) as session:
        row = await clients_repo.update_engagement(session, client_id, fields)
        if row is None:
            raise ValueError("engagement not found")
        await session.commit()
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
    ctx: Context, client_id: str
) -> dict[str, bool]:
    _, api_key = await authenticate_request(ctx)
    async with _open_member_session(api_key.org_id) as session:
        upload_paths = await clients_repo.list_upload_paths_for_client(
            session, client_id
        )
        deleted = await clients_repo.delete_engagement(session, client_id)
        if not deleted:
            raise ValueError("engagement not found")
        await session.commit()

    # Best-effort disk cleanup after the DB-side commit, identical to the
    # REST DELETE flow.
    for path in upload_paths:
        storage.delete_upload(path)
    return {"ok": True}


@mcp.tool(
    name="pulse_rotate_token",
    description=(
        "Generate a fresh access token for an engagement. The old URL "
        "stops working immediately."
    ),
)
async def pulse_rotate_token(
    ctx: Context, client_id: str
) -> dict[str, Any]:
    _, api_key = await authenticate_request(ctx)
    async with _open_member_session(api_key.org_id) as session:
        row = await clients_repo.rotate_token(session, client_id)
        if row is None:
            raise ValueError("engagement not found")
        await session.commit()
        return row


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
    ctx: Context, client_id: str, markdown: str
) -> dict[str, Any]:
    _, api_key = await authenticate_request(ctx)
    async with _open_member_session(api_key.org_id) as session:
        if (await clients_repo.get_by_id(session, client_id)) is None:
            raise ValueError("engagement not found")
        try:
            parsed = parse_markdown(markdown)
        except CardImportError as exc:
            detail = "\n".join(exc.errors) if exc.errors else str(exc)
            raise ValueError(detail) from exc

        org_id = str(api_key.org_id)
        created: list[dict[str, Any]] = []
        for card in parsed:
            row = await cards_repo.create_card(
                session,
                client_id=client_id,
                org_id=org_id,
                **card.to_create_kwargs(),
            )
            if row is None:
                raise ValueError(f"failed to insert card {card.title!r}")
            created.append(row)
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
    client_id: str,
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
    _, api_key = await authenticate_request(ctx)
    async with _open_member_session(api_key.org_id) as session:
        if (await clients_repo.get_by_id(session, client_id)) is None:
            raise ValueError("engagement not found")
        row = await cards_repo.create_card(
            session,
            client_id=client_id,
            category=category,
            title=title,
            context=context,
            question=question,
            response_type=response_type,
            options=options,
            default_value=default_value,
            skip_allowed=skip_allowed,
            attachment_path=attachment_path,
            org_id=str(api_key.org_id),
        )
        if row is None:
            raise ValueError("card creation failed")
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
    _, api_key = await authenticate_request(ctx)
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

    async with _open_member_session(api_key.org_id) as session:
        row = await cards_repo.update_card(session, card_id, fields)
        if row is None:
            raise ValueError("card not found")
        await session.commit()
        return row


@mcp.tool(
    name="pulse_delete_card",
    description="Permanently delete one card.",
)
async def pulse_delete_card(
    ctx: Context, card_id: str
) -> dict[str, bool]:
    _, api_key = await authenticate_request(ctx)
    async with _open_member_session(api_key.org_id) as session:
        deleted = await cards_repo.delete_card(session, card_id)
        if not deleted:
            raise ValueError("card not found")
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
