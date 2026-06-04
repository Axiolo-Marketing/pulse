"""Superadmin routes — cross-tenant management of organizations.

All routes mounted under ``/api/superadmin/*`` and gated by
:func:`get_current_superadmin`, which checks ``users.is_superadmin``.

The session used is :func:`get_admin_session` (``pulse_admin`` /
BYPASSRLS) because superadmin work crosses tenant boundaries by
definition — listing every org, creating a new one, deleting an org
the caller isn't a member of. RLS is not the safety net here; the
``is_superadmin`` gate is.

Two concrete operator workflows shape this surface:

1. Onboarding a new company. Superadmin POSTs ``/api/superadmin/orgs``
   with ``{name, slug, owner_email}``. The endpoint atomically inserts
   the org, creates a pending invite row for ``owner_email`` with
   ``role="owner"``, and emails the recipient the same signed link the
   normal owner-invite path produces. The raw signed token never leaves
   the email — only its SHA-256 hash lives on disk.

2. Tearing down an empty test org. Superadmin DELETEs
   ``/api/superadmin/orgs/{id}``. The endpoint refuses with 409 if the
   org has any clients (cascade would wipe customer data) and if the
   org has more than one member (a soft sanity guard that lets us spot
   "is the right team really gone?" before destruction). When neither
   guard trips, memberships → invites → audit logs → org are removed
   in order.
"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import email as email_module
from pulse_api.auth.email_messages import org_invite_email
from pulse_api.auth.middleware import get_current_superadmin
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models import User
from pulse_api.models._helpers import utcnow_naive
from pulse_api.repos import invites as invites_repo
from pulse_api.repos import memberships as memberships_repo
from pulse_api.repos import orgs as orgs_repo

router = APIRouter(tags=["superadmin"])

# Slug rule: lower-case, hyphen-separated alphanumerics, 2-40 chars.
# Must start and end with an alphanumeric to avoid leading/trailing
# hyphens that read weirdly in URLs.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SLUG_MIN_LEN = 2
_SLUG_MAX_LEN = 40

# Listing pagination bounds.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


# ── Request / response models ─────────────────────────────────────────────


class OrgListRow(BaseModel):
    """Row in the ``GET /api/superadmin/orgs`` listing."""

    id: str
    name: str
    slug: str
    member_count: int
    pending_invite_count: int
    created_at: object  # datetime — Pydantic v2 handles it
    owner_emails: list[str]


class CreateOrgRequest(BaseModel):
    """Body for ``POST /api/superadmin/orgs``.

    Slug is intentionally caller-supplied. We don't auto-derive from
    ``name`` because the operator may want a different shape (e.g.
    "Acme Co." → ``acme``, not ``acme-co``); humans pick better than
    deterministic slugifiers in onboarding.
    """

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=_SLUG_MIN_LEN, max_length=_SLUG_MAX_LEN)
    owner_email: EmailStr
    owner_role: str = Field(default="owner", pattern=r"^owner$")


class CreatedInviteSummary(BaseModel):
    """Slim invite payload returned alongside a newly-created org.

    No raw token — the recipient receives that in their email body and
    nowhere else. The ``expires_at`` lets the operator confirm the
    invite has a real deadline without re-fetching the list.
    """

    id: str
    email: str
    expires_at: object  # datetime


class OrgRow(BaseModel):
    """Single org payload returned by the create endpoint."""

    id: str
    name: str
    slug: str
    created_at: object  # datetime


class CreateOrgResponse(BaseModel):
    """``POST /api/superadmin/orgs`` body."""

    org: OrgRow
    invite: CreatedInviteSummary


class SuperadminMemberRow(BaseModel):
    """Row in the superadmin "view members of any org" endpoint."""

    user_id: str
    email: str
    name: str | None
    role: str
    joined_at: object  # datetime


# ── Helpers ───────────────────────────────────────────────────────────────


def _validate_slug(slug: str) -> str:
    """Normalize + validate the slug shape.

    Returns the lower-cased slug if valid. Raises ``HTTPException(422)``
    on any of: wrong length, uppercase, special characters, leading or
    trailing hyphen. We accept input that's already lower-case-only and
    reject mixed case to keep the on-disk slug deterministic — case
    folding would also work, but is opaque to anyone reading the
    request log.
    """
    if not isinstance(slug, str):
        raise HTTPException(status_code=422, detail="slug must be a string")
    if not _SLUG_MIN_LEN <= len(slug) <= _SLUG_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=(
                f"slug must be between {_SLUG_MIN_LEN} and "
                f"{_SLUG_MAX_LEN} characters"
            ),
        )
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=422,
            detail=(
                "slug must contain only lower-case letters, digits, and "
                "single hyphens (no leading/trailing hyphen)"
            ),
        )
    return slug


def _max_age_delta():
    """Translate ``settings.invite_token_max_age_seconds`` to a timedelta."""
    from datetime import timedelta

    return timedelta(seconds=settings.invite_token_max_age_seconds)


# ── Routes ────────────────────────────────────────────────────────────────


@router.get("/api/superadmin/orgs", response_model=list[OrgListRow])
async def list_all_orgs(
    limit: int = _DEFAULT_LIMIT,
    _: User = Depends(get_current_superadmin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[OrgListRow]:
    """List every organization with member/invite counts + top owners.

    Cross-tenant by definition — BYPASSRLS session is what makes the
    list span every org in the database. Pagination is intentionally
    coarse (single ``limit`` query param; default 50, capped at 200);
    a richer filter/cursor surface will land alongside the activity
    feed in PR 6.

    Args:
        limit: Maximum number of rows to return. Clamped to the
            interval ``[1, 200]``.
    """
    bounded = max(1, min(int(limit), _MAX_LIMIT))
    rows = await orgs_repo.list_all_with_summary(session, limit=bounded)
    return [
        OrgListRow(
            id=str(r["id"]),
            name=str(r["name"]),
            slug=str(r["slug"]),
            member_count=int(r["member_count"]),
            pending_invite_count=int(r["pending_invite_count"]),
            created_at=r["created_at"],
            owner_emails=list(r.get("owner_emails") or []),
        )
        for r in rows
    ]


@router.post(
    "/api/superadmin/orgs", status_code=201, response_model=CreateOrgResponse
)
async def create_org(
    req: CreateOrgRequest,
    user: User = Depends(get_current_superadmin),
    session: AsyncSession = Depends(get_admin_session),
) -> CreateOrgResponse:
    """Create a new org and send an invite to the owner.

    Sequenced inserts in one transaction:

    1. Validate slug shape + uniqueness (409 on collision).
    2. Insert ``organizations`` row.
    3. Insert ``organization_invites`` row for ``owner_email`` with
       ``role='owner'`` and a 7-day expiry.
    4. Sign + email the invite link.

    Failure at any step rolls back the whole transaction — half-created
    orgs without an owner invite would strand the operator with an
    unjoinable tenant.

    Args:
        req: Validated request body.

    Returns:
        ``{org: {...}, invite: {id, email, expires_at}}``. The raw
        signed token is **only** delivered via email; the JSON response
        never contains it.
    """
    slug = _validate_slug(req.slug)

    # Slug uniqueness check — the unique index would 500 on conflict;
    # convert into a 409 here.
    existing = await orgs_repo.find_by_slug(session, slug)
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"slug '{slug}' is already in use"
        )

    org_row = await orgs_repo.create_org(
        session, name=req.name.strip(), slug=slug
    )
    org_id = uuid.UUID(str(org_row["id"]))

    # Create the owner invite. RLS WITH CHECK on ``organization_invites``
    # would normally enforce ``org_id = pulse.org_id``, but pulse_admin
    # is BYPASSRLS — the insert succeeds without setting the GUC. The
    # invites repo signs the token over the new invite id and returns
    # the raw token for the email body.
    owner_email = req.owner_email.lower().strip()
    expires_at = utcnow_naive() + _max_age_delta()
    invite_row, raw_token = await invites_repo.create_invite(
        session,
        org_id=org_id,
        email=owner_email,
        role="owner",
        invited_by_user_id=user.id,
        expires_at=expires_at,
    )

    subject, body = org_invite_email(
        raw_token,
        org_name=req.name.strip(),
        inviter_name=user.name,
        role="owner",
    )
    await email_module.send_email(owner_email, subject, body)
    await session.commit()

    return CreateOrgResponse(
        org=OrgRow(
            id=str(org_row["id"]),
            name=str(org_row["name"]),
            slug=str(org_row["slug"]),
            created_at=org_row["created_at"],
        ),
        invite=CreatedInviteSummary(
            id=str(invite_row["id"]),
            email=str(invite_row["email"]),
            expires_at=invite_row["expires_at"],
        ),
    )


@router.delete("/api/superadmin/orgs/{org_id}", status_code=204)
async def delete_org(
    org_id: str,
    _: User = Depends(get_current_superadmin),
    session: AsyncSession = Depends(get_admin_session),
) -> None:
    """Delete an empty organization.

    Refused with 409 when the org has clients (cascade would wipe
    customer data) OR when the org has more than one member (use the
    org's own owner-mediated remove-member flow to drain it first;
    forcing this from the cross-tenant view is too easy to misclick).

    The "active org" of the caller is irrelevant — a superadmin can
    delete the Axiolo org if it has no clients. The product safety is
    "no clients", not "not your active org".

    Status codes:

    * 204 — deleted.
    * 404 — unknown org id (or malformed UUID).
    * 409 — the org has clients, or more than one member.
    """
    # Malformed UUID → 404 (same shape as "no such org") so a probing
    # client can't tell which.
    try:
        as_uuid = uuid.UUID(org_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="organization not found") from exc

    row = await orgs_repo.get_by_id(session, as_uuid)
    if row is None:
        raise HTTPException(status_code=404, detail="organization not found")

    clients = await orgs_repo.client_count(session, as_uuid)
    if clients > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"organization has {clients} client(s); delete those first"
            ),
        )

    members = await orgs_repo.member_count(session, as_uuid)
    if members > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                f"organization has {members} members; remove all but one "
                "before deleting"
            ),
        )

    deleted = await orgs_repo.delete_org(session, as_uuid)
    if not deleted:  # pragma: no cover — get_by_id above guards
        raise HTTPException(status_code=404, detail="organization not found")
    await session.commit()


@router.get(
    "/api/superadmin/orgs/{org_id}/members",
    response_model=list[SuperadminMemberRow],
)
async def list_org_members(
    org_id: str,
    _: User = Depends(get_current_superadmin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[SuperadminMemberRow]:
    """List members of an arbitrary org, regardless of caller's active org.

    Convenience for support workflows ("who can I email when something
    breaks for Acme?"). Same row shape as ``GET /api/orgs/me/members``.
    BYPASSRLS, two-pass: membership rows + user-display fields are both
    read on the same admin session because there is no org-scoped role
    flip happening here.
    """
    try:
        as_uuid = uuid.UUID(org_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="organization not found") from exc

    org = await orgs_repo.get_by_id(session, as_uuid)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")

    rows = await memberships_repo.list_membership_rows(session, as_uuid)
    if not rows:
        return []
    user_ids = [str(r["user_id"]) for r in rows]
    user_map = await memberships_repo.list_user_display_fields(
        session, user_ids
    )
    out: list[SuperadminMemberRow] = []
    for r in rows:
        u = user_map.get(str(r["user_id"]))
        if u is None:
            continue
        name = u["name"]
        out.append(
            SuperadminMemberRow(
                user_id=str(r["user_id"]),
                email=str(u["email"]),
                name=(str(name) if name is not None else None),
                role=str(r["role"]),
                joined_at=r["joined_at"],
            )
        )
    return out
