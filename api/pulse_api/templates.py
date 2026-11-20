"""Deck-template operations shared by the REST routes and the MCP tools.

Each function runs on the caller's ``pulse_member`` session, writes the
audit row, and leaves the commit to the caller (so the audit row and the
change are atomic). Failures raise a ``TemplateError`` subclass that each
surface maps to its own error shape (HTTP status / ``ValueError``).
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import reactive, transcription
from pulse_api.audit import record_audit
from pulse_api.repos import cards as cards_repo
from pulse_api.repos import deck_templates as templates_repo
from pulse_api.repos import engagements as engagements_repo


class TemplateError(Exception):
    """Base class; ``status`` is the HTTP status the REST layer uses."""

    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TemplateNotFound(TemplateError):
    status = 404

    def __init__(self) -> None:
        super().__init__("template not found")


class EngagementNotFound(TemplateError):
    status = 404

    def __init__(self) -> None:
        super().__init__("engagement not found")


class EmptySnapshot(TemplateError):
    status = 400

    def __init__(self) -> None:
        super().__init__("This engagement has no questions to save as a template.")


class NameTaken(TemplateError):
    status = 409

    def __init__(self) -> None:
        super().__init__("A template with that name already exists.")


class InvalidName(TemplateError):
    status = 422

    def __init__(self) -> None:
        super().__init__("Template name must be 1 to 120 characters.")


NAME_MAX = 120
DESCRIPTION_MAX = 500


def clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > NAME_MAX:
        raise InvalidName
    return name


def clean_description(description: str | None) -> str | None:
    if description is None:
        return None
    description = description.strip()
    if len(description) > DESCRIPTION_MAX:
        raise TemplateError(
            f"Description must be at most {DESCRIPTION_MAX} characters."
        )
    return description or None


async def _snapshot(
    session: AsyncSession, engagement_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if await engagements_repo.get_by_id(session, engagement_id) is None:
        raise EngagementNotFound
    cards, settings = await templates_repo.snapshot_engagement(
        session, engagement_id
    )
    if not cards:
        raise EmptySnapshot
    return cards, settings


async def save_template(
    session: AsyncSession,
    *,
    org_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    engagement_id: str,
    name: str,
    description: str | None,
) -> dict[str, Any]:
    """Snapshot ``engagement_id`` into a new template (audit
    ``template.create``)."""
    name = clean_name(name)
    description = clean_description(description)
    cards, settings = await _snapshot(session, engagement_id)
    if await templates_repo.name_taken(session, name):
        raise NameTaken
    try:
        async with session.begin_nested():
            row = await templates_repo.create(
                session,
                org_id=str(org_id),
                created_by=str(user_id),
                name=name,
                description=description,
                cards=cards,
                settings=settings,
            )
    except IntegrityError as exc:  # lost a race on the unique name index
        raise NameTaken from exc
    await record_audit(
        session,
        org_id=org_id,
        user_id=user_id,
        action="template.create",
        target_type="template",
        target_id=row["id"],
        metadata={
            "name": name,
            "cards": len(cards),
            "engagement_id": engagement_id,
        },
    )
    return row


async def update_template(
    session: AsyncSession,
    *,
    org_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    template_id: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Rename / re-describe a template, and/or re-snapshot it from
    ``fields["engagement_id"]`` (audit ``template.update``)."""
    current = await templates_repo.get(session, template_id)
    if current is None:
        raise TemplateNotFound
    writes: dict[str, Any] = {}
    changed: list[str] = []
    meta: dict[str, Any] = {}
    if "name" in fields and fields["name"] is not None:
        name = clean_name(fields["name"])
        if await templates_repo.name_taken(session, name, exclude_id=template_id):
            raise NameTaken
        writes["name"] = name
        changed.append("name")
    if "description" in fields:
        writes["description"] = clean_description(fields["description"])
        changed.append("description")
    if fields.get("engagement_id"):
        cards, settings = await _snapshot(session, fields["engagement_id"])
        writes["cards"] = cards
        writes["settings"] = settings
        changed.extend(["cards", "settings"])
        meta["cards"] = len(cards)
        meta["engagement_id"] = fields["engagement_id"]
    try:
        async with session.begin_nested():
            row = await templates_repo.update(session, template_id, writes)
    except IntegrityError as exc:
        raise NameTaken from exc
    if row is None:  # pragma: no cover - deleted between the get and the update
        raise TemplateNotFound
    await record_audit(
        session,
        org_id=org_id,
        user_id=user_id,
        action="template.update",
        target_type="template",
        target_id=template_id,
        metadata={"name": row["name"], "changed_fields": changed, **meta},
    )
    return row


async def delete_template(
    session: AsyncSession,
    *,
    org_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    template_id: str,
) -> None:
    deleted = await templates_repo.delete(session, template_id)
    if deleted is None:
        raise TemplateNotFound
    await record_audit(
        session,
        org_id=org_id,
        user_id=user_id,
        action="template.delete",
        target_type="template",
        target_id=template_id,
        metadata={"name": deleted["name"]},
    )


async def create_engagement_from_template(
    session: AsyncSession,
    *,
    org_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    client_id: str,
    engagement_name: str | None,
    template_id: str,
) -> tuple[dict[str, Any], int]:
    """Create an engagement carrying the template's settings and cards, in
    the caller's transaction. Reactive cards / transcription are applied
    only when the org / deployment allow them (otherwise left off, no
    error). Returns ``(engagement_row, cards_copied)``; the caller records
    the ``engagement.create`` audit row and commits."""
    template = await templates_repo.get(session, template_id)
    if template is None:
        raise TemplateNotFound
    wanted = template.get("settings") or {}
    settings = {k: bool(wanted[k]) for k in templates_repo.SETTING_KEYS if k in wanted}
    if settings.get("reactive_cards_enabled") and not await reactive.ensure_org_allowed(
        session, org_id
    ):
        settings["reactive_cards_enabled"] = False
    if settings.get("transcription_enabled") and not transcription.available():
        settings["transcription_enabled"] = False
    row = await engagements_repo.create_engagement(
        session,
        client_id=client_id,
        engagement_name=engagement_name,
        org_id=str(org_id),
        created_by=str(user_id),
        template_id=template["id"],
        settings=settings,
    )
    cards = template["cards"] or []
    for card in cards:
        await cards_repo.create_card(
            session,
            engagement_id=row["id"],
            org_id=str(org_id),
            **{k: card.get(k) for k in templates_repo.CARD_KEYS},
        )
    return row, len(cards)
