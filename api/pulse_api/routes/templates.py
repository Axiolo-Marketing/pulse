"""Deck templates: save an engagement's questions as a reusable,
org-scoped template and list / read / rename / re-snapshot / delete them.

Starting an engagement from a template is ``POST /api/admin/engagements``
with ``template_id`` (``routes/admin_api.py``); the shared logic lives in
``pulse_api/templates.py`` so the MCP tools run the same code.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import templates as templates_service
from pulse_api.auth.middleware import (
    get_current_org_member,
    get_org_scoped_session,
)
from pulse_api.models import OrganizationMembership, User
from pulse_api.repos import deck_templates as templates_repo

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_org_member)],
)


class CreateTemplateRequest(BaseModel):
    engagement_id: str
    # Name and description are trimmed and length-checked by the service,
    # which answers with a readable message (a pydantic 422 carries a list
    # the admin UI can't show).
    name: str
    description: str | None = None


class UpdateTemplateRequest(BaseModel):
    """Partial update. ``engagement_id`` re-snapshots the cards and settings
    from that engagement."""

    name: str | None = None
    description: str | None = None
    engagement_id: str | None = None


def _http(exc: templates_service.TemplateError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/templates")
async def list_templates(
    session: AsyncSession = Depends(get_org_scoped_session),
) -> list[dict[str, Any]]:
    return await templates_repo.list_for_org(session)


@router.get("/templates/{template_id}")
async def get_template(
    template_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
) -> dict[str, Any]:
    row = await templates_repo.get(session, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="template not found")
    return row


@router.post("/templates", status_code=201)
async def create_template(
    req: CreateTemplateRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    user, membership = org_member
    try:
        row = await templates_service.save_template(
            session,
            org_id=membership.org_id,
            user_id=user.id,
            engagement_id=req.engagement_id,
            name=req.name,
            description=req.description,
        )
    except templates_service.TemplateError as exc:
        raise _http(exc) from exc
    await session.commit()
    return row


@router.patch("/templates/{template_id}")
async def update_template(
    template_id: str,
    req: UpdateTemplateRequest,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> dict[str, Any]:
    user, membership = org_member
    try:
        row = await templates_service.update_template(
            session,
            org_id=membership.org_id,
            user_id=user.id,
            template_id=template_id,
            fields=req.model_dump(exclude_unset=True),
        )
    except templates_service.TemplateError as exc:
        raise _http(exc) from exc
    await session.commit()
    return row


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    session: AsyncSession = Depends(get_org_scoped_session),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> None:
    user, membership = org_member
    try:
        await templates_service.delete_template(
            session,
            org_id=membership.org_id,
            user_id=user.id,
            template_id=template_id,
        )
    except templates_service.TemplateError as exc:
        raise _http(exc) from exc
    await session.commit()
