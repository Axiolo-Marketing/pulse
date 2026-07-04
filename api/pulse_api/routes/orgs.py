"""Organization, membership, and invite admin routes.

Three concerns share this module because the route surface is one
operator's view of "their" org — switching, settings, members, invites
all live under ``/api/orgs/me/*`` or ``/api/me/*``.

Three auth gates are used:

* ``get_current_user`` — for the multi-org switching surface. The
  switch endpoint is the only place we touch orgs the user might not
  be currently active in, so the gate is just "is this a signed-in
  user" and the verification is per-call.
* ``get_current_org_member`` — read-only org context (list members,
  list invites, GET org details).
* ``require_owner`` — owner-gated mutations (PATCH org, manage members,
  create/revoke invites, manage logo).

Logo uploads live under ``settings.upload_dir/org-logos/{org_id}/`` so
the disk layout mirrors the existing client-upload pattern. The path
prefix is reconstructed from authenticated state (the active org's
``id``) — never from the wire body.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import email as email_module
from pulse_api import storage
from pulse_api.audit import AUDIT_ACTIONS, record_audit
from pulse_api.auth.email_messages import org_invite_email
from pulse_api.auth.middleware import (
    get_current_org_member,
    get_current_user,
    get_org_scoped_session,
    require_owner,
)
from pulse_api.auth.session import write_session
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models import OrganizationMembership, User
from pulse_api.models._helpers import utcnow_naive
from pulse_api.repos import audit_logs as audit_logs_repo
from pulse_api.repos import invites as invites_repo
from pulse_api.repos import memberships as memberships_repo
from pulse_api.repos import orgs as orgs_repo

router = APIRouter(tags=["orgs"])


async def _reset_role_on_admin_session(session: AsyncSession) -> None:
    """Reset the effective role on the admin session.

    In production this is a no-op (the ``pulse_admin`` engine opens its
    own connection that's already at the right role). In tests the
    admin session shares the test transaction's connection, so the
    org-scoped session's ``set local role pulse_member`` would
    otherwise leak across — calling ``reset role`` brings the
    connection back to its session_user (the owner role in dev) so
    queries against ``users`` succeed.
    """
    await session.execute(text("reset role"))

# ── Request/response models ───────────────────────────────────────────────


class OrgSummary(BaseModel):
    """Slim org payload used in the ``/api/me/orgs`` list."""

    id: str
    name: str
    slug: str
    role: str
    logo_path: str | None = None


class SwitchOrgRequest(BaseModel):
    """Body for ``POST /api/me/switch-org``."""

    org_id: str


# ── Branding ──────────────────────────────────────────────────────────────

# Hex color, exactly ``#RRGGBB``. Shared by all three color fields so the
# frontend and backend validate the same shape.
_HEX_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"

# Font slugs the client deck knows how to render. Keep this set in sync
# with the frontend's font map — an org can only pick from these.
ALLOWED_FONTS: frozenset[str] = frozenset(
    {
        "plus-jakarta-sans",
        "inter",
        "roboto",
        "lora",
        "source-serif",
        "system-ui",
    }
)


class BrandingSettings(BaseModel):
    """Per-org brand/theme overrides for the client deck.

    Every field is optional; a missing/``None`` value means "use the
    built-in default". The same model is reused for the
    ``PATCH /api/orgs/me/branding`` request body and the ``branding``
    field of :class:`OrgDetails`.

    Attributes:
        brand_color: Primary accent color, ``#RRGGBB``.
        background_color: Deck background color, ``#RRGGBB``.
        text_color: Body text color, ``#RRGGBB``.
        font: One of :data:`ALLOWED_FONTS`.
    """

    brand_color: str | None = Field(default=None, pattern=_HEX_COLOR_PATTERN)
    background_color: str | None = Field(
        default=None, pattern=_HEX_COLOR_PATTERN
    )
    text_color: str | None = Field(default=None, pattern=_HEX_COLOR_PATTERN)
    font: str | None = None

    @field_validator("font")
    @classmethod
    def _validate_font(cls, value: str | None) -> str | None:
        """Reject any font slug outside :data:`ALLOWED_FONTS`."""
        if value is not None and value not in ALLOWED_FONTS:
            raise ValueError(
                f"font must be one of {sorted(ALLOWED_FONTS)}"
            )
        return value

    def is_empty(self) -> bool:
        """Return True when every field is unset.

        Used by the PATCH route to decide whether to store SQL NULL
        (reset the deck to its built-in defaults) instead of an empty
        JSON object.
        """
        return all(
            getattr(self, name) is None for name in type(self).model_fields
        )


class OrgDetails(BaseModel):
    """Returned by ``GET /api/orgs/me`` — the Settings page header."""

    id: str
    name: str
    slug: str
    logo_path: str | None = None
    branding: BrandingSettings | None = None
    role: str
    member_count: int
    pending_invite_count: int


class UpdateOrgRequest(BaseModel):
    """Body for ``PATCH /api/orgs/me``. Slug is intentionally immutable."""

    name: str | None = Field(default=None, min_length=1, max_length=200)


class UpdateMemberRequest(BaseModel):
    """Body for ``PATCH /api/orgs/me/members/{user_id}``."""

    role: str = Field(pattern=r"^(owner|member)$")


class MemberRow(BaseModel):
    """Row in the ``/api/orgs/me/members`` listing."""

    user_id: str
    email: str
    name: str | None
    role: str
    joined_at: object  # datetime — Pydantic v2 handles it


class CreateInviteRequest(BaseModel):
    """Body for ``POST /api/orgs/me/invites``."""

    email: EmailStr
    role: str = Field(pattern=r"^(owner|member)$")


class InviteSummary(BaseModel):
    """Returned by the invite list + create endpoints (no token)."""

    id: str
    email: str
    role: str
    created_at: object  # datetime
    expires_at: object  # datetime
    invited_by_email: str | None = None


class ActivityActor(BaseModel):
    """Actor sub-payload of an activity entry."""

    user_id: str | None
    email: str | None
    name: str | None


class ActivityEntry(BaseModel):
    """One row in the activity feed."""

    id: str
    created_at: object  # datetime — Pydantic v2 handles it
    actor: ActivityActor
    action: str
    target_type: str | None
    target_id: str | None
    metadata: dict[str, Any] | None = None


class ActivityPage(BaseModel):
    """Returned by ``GET /api/orgs/me/activity``."""

    entries: list[ActivityEntry]
    next_cursor: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────


def _role_str(membership: OrganizationMembership) -> str:
    """Return ``membership.role`` as a plain string regardless of enum shape.

    The role column is plain text on disk; SQLModel instances can
    surface either the enum or the raw value depending on how the row
    was loaded.
    """
    return (
        membership.role.value
        if hasattr(membership.role, "value")
        else str(membership.role)
    )


def _branding_from_row(row: dict[str, object]) -> BrandingSettings | None:
    """Build a :class:`BrandingSettings` from a stored ``branding`` dict.

    asyncpg decodes the JSONB column to a plain ``dict`` (or ``None``).
    Returns ``None`` when the column is unset so the response omits the
    branding object entirely.

    Args:
        row: An org row dict from ``orgs_repo`` carrying a ``branding`` key.

    Returns:
        Parsed ``BrandingSettings`` or ``None``.
    """
    raw = row.get("branding")
    if not raw:
        return None
    return BrandingSettings.model_validate(raw)


# ── Multi-org switching ───────────────────────────────────────────────────


@router.get("/api/me/orgs", response_model=list[OrgSummary])
async def list_my_orgs(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_admin_session),
) -> list[OrgSummary]:
    """List every org the user is a member of.

    Uses a BYPASSRLS session because the list spans every org — RLS
    would filter to only the currently-active org, which is the
    opposite of what this endpoint is for.
    """
    rows = await orgs_repo.list_orgs_for_user(session, user.id)
    return [
        OrgSummary(
            id=str(r["id"]),
            name=str(r["name"]),
            slug=str(r["slug"]),
            role=str(r["role"]),
            logo_path=r.get("logo_path"),
        )
        for r in rows
    ]


@router.post("/api/me/switch-org", response_model=OrgSummary)
async def switch_org(
    req: SwitchOrgRequest,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_admin_session),
) -> OrgSummary:
    """Set the active org for the caller's session.

    Verifies the user holds a current membership in the target org,
    updates ``users.last_active_org_id``, and re-issues the session
    cookie with the new ``active_org_id``. Returns the resolved org so
    the UI can update its header without an extra round-trip.

    Returns 422 for a malformed UUID, 403 for an org the user isn't a
    member of (or doesn't exist).
    """
    try:
        target_org_id = uuid.UUID(req.org_id)
    except (TypeError, ValueError) as exc:
        # Match FastAPI's malformed-uuid error shape.
        raise HTTPException(status_code=422, detail="invalid org_id") from exc

    is_member = await orgs_repo.is_member_of(
        session, user_id=user.id, org_id=target_org_id
    )
    if not is_member:
        # Same shape regardless of "doesn't exist" vs "not a member" so
        # an attacker can't probe org existence.
        raise HTTPException(
            status_code=403, detail="not a member of the target organization"
        )

    rows = await orgs_repo.list_orgs_for_user(session, user.id)
    org_row = next((r for r in rows if str(r["id"]) == str(target_org_id)), None)
    if org_row is None:
        # Race: the membership disappeared between the two queries.
        raise HTTPException(
            status_code=403, detail="not a member of the target organization"
        )

    await orgs_repo.set_last_active_org(
        session, user_id=user.id, org_id=target_org_id
    )
    await session.commit()

    write_session(response, user_id=user.id, active_org_id=target_org_id)

    return OrgSummary(
        id=str(org_row["id"]),
        name=str(org_row["name"]),
        slug=str(org_row["slug"]),
        role=str(org_row["role"]),
        logo_path=org_row.get("logo_path"),
    )


# ── Org details + branding ────────────────────────────────────────────────


@router.get("/api/orgs/me", response_model=OrgDetails)
async def get_my_org(
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    session: AsyncSession = Depends(get_org_scoped_session),
) -> OrgDetails:
    """Return the active org's name + slug + logo + the caller's role
    plus member/invite counts. Single endpoint for the Settings header.
    """
    _, membership = org_member
    row = await orgs_repo.get_for_member(session, membership.org_id)
    if row is None:
        # RLS narrowed the org row away — would imply the GUC and the
        # membership disagree, which shouldn't be possible. Treat as 404.
        raise HTTPException(status_code=404, detail="organization not found")
    member_count = await orgs_repo.member_count(session, membership.org_id)
    invite_count = await orgs_repo.pending_invite_count(session, membership.org_id)
    return OrgDetails(
        id=str(row["id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        logo_path=row.get("logo_path"),
        branding=_branding_from_row(row),
        role=_role_str(membership),
        member_count=member_count,
        pending_invite_count=invite_count,
    )


@router.patch("/api/orgs/me", response_model=OrgDetails)
async def update_my_org(
    req: UpdateOrgRequest,
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    _: OrganizationMembership = Depends(require_owner),
    session: AsyncSession = Depends(get_org_scoped_session),
) -> OrgDetails:
    """Owner-only. Update the org's display name.

    Slug is immutable — it appears in URLs and the audit log, and a
    rename would invalidate links. Logo updates go through the
    dedicated ``POST /api/orgs/me/logo`` endpoint.
    """
    user, membership = org_member
    fields = req.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        previous = await orgs_repo.get_for_member(session, membership.org_id)
        row = await orgs_repo.update_name(
            session, org_id=membership.org_id, name=fields["name"].strip()
        )
        if row is None:
            raise HTTPException(
                status_code=404, detail="organization not found"
            )
        await record_audit(
            session,
            org_id=membership.org_id,
            user_id=user.id,
            action="org.update",
            target_type="org",
            target_id=str(membership.org_id),
            metadata={
                "old_name": (previous or {}).get("name") if previous else None,
                "new_name": row.get("name"),
            },
        )
    else:
        row = await orgs_repo.get_for_member(session, membership.org_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail="organization not found"
            )

    await session.commit()
    member_count = await orgs_repo.member_count(session, membership.org_id)
    invite_count = await orgs_repo.pending_invite_count(
        session, membership.org_id
    )
    return OrgDetails(
        id=str(row["id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        logo_path=row.get("logo_path"),
        branding=_branding_from_row(row),
        role=_role_str(membership),
        member_count=member_count,
        pending_invite_count=invite_count,
    )


@router.patch("/api/orgs/me/branding", response_model=OrgDetails)
async def update_my_org_branding(
    req: BrandingSettings,
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    _owner_guard: OrganizationMembership = Depends(require_owner),
    session: AsyncSession = Depends(get_org_scoped_session),
) -> OrgDetails:
    """Owner-only. Replace the active org's branding/theme overrides.

    The body fully replaces the stored ``branding`` object — there is no
    per-field merge, so the client always sends the complete desired
    state. When every field is ``None`` (an "empty" body) the column is
    set to SQL NULL, resetting the client deck to its built-in defaults.

    Returns the refreshed :class:`OrgDetails` so the Settings UI can
    re-render without a follow-up GET.
    """
    user, membership = org_member

    previous = await orgs_repo.get_for_member(session, membership.org_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="organization not found")

    # Empty body resets to defaults (SQL NULL); otherwise persist the
    # non-null fields only so the JSON object stays compact.
    new_branding = (
        None
        if req.is_empty()
        else req.model_dump(exclude_none=True)
    )

    row = await orgs_repo.set_branding(
        session, org_id=membership.org_id, branding=new_branding
    )
    if row is None:  # pragma: no cover — previous fetch above guards
        raise HTTPException(status_code=404, detail="organization not found")

    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="org.branding",
        target_type="org",
        target_id=str(membership.org_id),
        metadata={
            "old": previous.get("branding"),
            "new": new_branding,
        },
    )
    await session.commit()

    member_count = await orgs_repo.member_count(session, membership.org_id)
    invite_count = await orgs_repo.pending_invite_count(
        session, membership.org_id
    )
    return OrgDetails(
        id=str(row["id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        logo_path=row.get("logo_path"),
        branding=_branding_from_row(row),
        role=_role_str(membership),
        member_count=member_count,
        pending_invite_count=invite_count,
    )


# Defense-in-depth: each allowed mime maps to the extension we'll
# write. We accept the lookup from BOTH sides — the wire-supplied
# content-type AND the file's extension must both point at the same
# entry. Reject if either is missing or they disagree.
_LOGO_MIME_TO_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}
_LOGO_EXT_TO_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def serve_logo_file(logo_path: str) -> FileResponse:
    """Build a :class:`FileResponse` for a stored org ``logo_path``.

    Shared by the member-authed ``GET /api/orgs/me/logo/{filename}`` route
    and the token-authed ``GET /api/me/logo`` client route so the MIME
    lookup and the SVG ``Content-Security-Policy`` hardening live in one
    place.

    The caller is responsible for the authorization decision (which org's
    logo this is) and for confirming ``logo_path`` is the org's *current*
    logo. This helper only resolves the path safely and serves the bytes.

    Args:
        logo_path: Relative path under ``settings.upload_dir`` taken from
            the authenticated org row — never from the request body.

    Returns:
        A ``FileResponse`` with the right media type and, for SVGs, a
        strict CSP header so direct-tab opens can't execute embedded
        scripts.

    Raises:
        HTTPException: 400 if the path escapes the upload root, 404 if the
            file is missing on disk.
    """
    try:
        path = storage.resolve_within_upload_dir(logo_path)
    except storage.StoragePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="logo not found")

    mime = _LOGO_EXT_TO_MIME.get(
        path.suffix.lower(), "application/octet-stream"
    )
    headers: dict[str, str] = {}
    if path.suffix.lower() == ".svg":
        # SVG can embed scripts — strict CSP so direct-tab opens are safe.
        headers["Content-Security-Policy"] = (
            "script-src 'none'; default-src 'self' data:;"
        )
    return FileResponse(path=path, media_type=mime, headers=headers)


def _validate_logo_upload(
    file: UploadFile, content: bytes
) -> tuple[str, str]:
    """Validate logo content type, extension, and size.

    Returns ``(safe_extension, mime_type)``. Raises HTTPException with
    the right status code on rejection:

    * 413 — size > ``settings.max_org_logo_bytes``.
    * 400 — empty file.
    * 415 — MIME and extension don't match an allow-listed pair.
    """
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > settings.max_org_logo_bytes:
        raise HTTPException(status_code=413, detail="file too large")

    content_type = (file.content_type or "").lower().strip()
    if content_type not in settings.allowed_logo_mime_types:
        raise HTTPException(
            status_code=415, detail=f"unsupported content-type: {content_type!r}"
        )

    ext = Path(file.filename or "").suffix.lower()
    expected_ext = _LOGO_MIME_TO_EXT.get(content_type)
    # Both sides must agree. .jpg ↔ image/jpeg is the only alias.
    if expected_ext is None or _LOGO_EXT_TO_MIME.get(ext) != content_type:
        raise HTTPException(
            status_code=415,
            detail="content-type and filename extension do not match",
        )
    return expected_ext, content_type


@router.post("/api/orgs/me/logo")
async def upload_org_logo(
    file: UploadFile = File(...),
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    _owner_guard: OrganizationMembership = Depends(require_owner),
    session: AsyncSession = Depends(get_org_scoped_session),
) -> dict[str, str]:
    """Owner-only multipart upload. Replaces any existing logo.

    Constraints:

    * ≤ ``settings.max_org_logo_bytes`` (default 500KB).
    * Content-Type ∈ ``settings.allowed_logo_mime_types``.
    * Filename extension must match the supplied content type.

    On success, writes the file under
    ``settings.upload_dir/org-logos/{org_id}/{uuid}.{ext}`` and updates
    ``organizations.logo_path``. Returns ``{logo_path}``.
    """
    user, membership = org_member
    content = await file.read()
    ext, mime = _validate_logo_upload(file, content)

    org_id_str = str(membership.org_id)
    # Trust boundary: the prefix segment is the active org's id taken
    # from the authenticated membership — never the wire body.
    relative_path = f"org-logos/{org_id_str}/{uuid.uuid4()}{ext}"
    try:
        storage.resolve_within_upload_dir(relative_path)
    except storage.StoragePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    storage.write_upload(relative_path=relative_path, content=content)

    # Snapshot the previous logo path before we overwrite it, but don't
    # delete the file yet — if the commit below fails, the row must still
    # point at a file that exists on disk. Mirrors delete_engagement's
    # DB-first/disk-second order.
    previous = await orgs_repo.get_for_member(session, membership.org_id)
    previous_logo_path = previous.get("logo_path") if previous else None

    await orgs_repo.set_logo_path(
        session, org_id=membership.org_id, logo_path=relative_path
    )
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="org.logo_set",
        target_type="org",
        target_id=str(membership.org_id),
        metadata={"mime_type": mime, "size_bytes": len(content)},
    )
    await session.commit()

    # Best-effort cleanup, now that the row durably points at the new file.
    # Old files not accumulating is a nice-to-have; a row pointing at a
    # deleted file (had the commit failed after an early delete) would not be.
    if previous_logo_path:
        storage.delete_upload(str(previous_logo_path))

    return {"logo_path": relative_path}


@router.delete("/api/orgs/me/logo", status_code=204)
async def delete_org_logo(
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    _owner_guard: OrganizationMembership = Depends(require_owner),
    session: AsyncSession = Depends(get_org_scoped_session),
) -> None:
    """Owner-only. Clear the org's logo and best-effort delete the file."""
    user, membership = org_member
    previous = await orgs_repo.get_for_member(session, membership.org_id)
    if previous and previous.get("logo_path"):
        storage.delete_upload(str(previous["logo_path"]))
    await orgs_repo.set_logo_path(
        session, org_id=membership.org_id, logo_path=None
    )
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="org.logo_remove",
        target_type="org",
        target_id=str(membership.org_id),
        metadata=None,
    )
    await session.commit()


@router.get("/api/orgs/me/logo/{filename}")
async def serve_org_logo(
    filename: str,
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    session: AsyncSession = Depends(get_org_scoped_session),
) -> FileResponse:
    """Serve the logo bytes for the active org.

    Auth: any member of the active org. The filename in the URL is the
    UUID-based name we minted at upload, so unguessable as a discovery
    vector; the auth gate still narrows access to people who can already
    reach the org.

    Path traversal defense lives in
    ``storage.resolve_within_upload_dir`` — refuses absolute paths,
    ``..`` traversal, and anything that resolves outside the upload root.
    """
    _, membership = org_member
    base_name = Path(filename).name
    if base_name != filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    relative_path = f"org-logos/{membership.org_id}/{base_name}"

    # Confirm this is in fact the active logo (avoids leaking old logos
    # after they were replaced — the previous-file delete is best-effort).
    row = await orgs_repo.get_for_member(session, membership.org_id)
    if row is None or row.get("logo_path") != relative_path:
        raise HTTPException(status_code=404, detail="logo not found")

    return serve_logo_file(relative_path)


# ── Members ───────────────────────────────────────────────────────────────


async def _list_members_two_pass(
    *,
    member_session: AsyncSession,
    admin_session: AsyncSession,
    org_id: object,
) -> list[dict[str, object]]:
    """Two-pass membership listing.

    Step 1 (``member_session``, RLS-scoped) — fetch bare
    ``organization_memberships`` rows for the active org.

    Step 2 (``admin_session``, BYPASSRLS) — resolve user_id → email/name.

    The ``reset role`` between steps is a no-op in production
    (different engines / connections) and a deliberate clean-up in
    tests (shared connection — the org-scoped dep set role to
    ``pulse_member``, which would otherwise block the users SELECT).
    """
    rows = await memberships_repo.list_membership_rows(member_session, org_id)
    if not rows:
        return []
    await _reset_role_on_admin_session(admin_session)
    user_ids = [str(r["user_id"]) for r in rows]
    user_map = await memberships_repo.list_user_display_fields(
        admin_session, user_ids
    )
    out: list[dict[str, object]] = []
    for r in rows:
        u = user_map.get(str(r["user_id"]))
        if u is None:
            continue
        out.append(
            {
                "user_id": str(r["user_id"]),
                "email": u["email"],
                "name": u["name"],
                "role": r["role"],
                "joined_at": r["joined_at"],
            }
        )
    return out


@router.get("/api/orgs/me/members", response_model=list[MemberRow])
async def list_org_members(
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    member_session: AsyncSession = Depends(get_org_scoped_session),
    admin_session: AsyncSession = Depends(get_admin_session),
) -> list[MemberRow]:
    """List members of the active org. Visible to any member of the org.

    Two-pass to avoid a join the ``pulse_member`` role can't make
    (no SELECT on ``users``): membership rows via the RLS-scoped
    ``member_session``; display fields via the BYPASSRLS
    ``admin_session``. The route gate plus the org_id-RLS on the
    membership half are the tenant boundary.
    """
    _, membership = org_member
    rows = await _list_members_two_pass(
        member_session=member_session,
        admin_session=admin_session,
        org_id=membership.org_id,
    )
    return [
        MemberRow(
            user_id=str(r["user_id"]),
            email=str(r["email"]),
            name=(str(r["name"]) if r.get("name") is not None else None),
            role=str(r["role"]),
            joined_at=r["joined_at"],
        )
        for r in rows
    ]


@router.patch("/api/orgs/me/members/{user_id}", response_model=MemberRow)
async def update_member_role(
    user_id: str,
    req: UpdateMemberRequest,
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    _owner_guard: OrganizationMembership = Depends(require_owner),
    session: AsyncSession = Depends(get_org_scoped_session),
    admin_session: AsyncSession = Depends(get_admin_session),
) -> MemberRow:
    """Owner-only. Change a member's role.

    The "at least one owner" invariant is enforced application-side:
    demoting the last owner returns 409. Demoting yourself when you
    are the only owner also returns 409 — same invariant, separate
    error message for the UI.

    Uses both sessions: ``session`` (``pulse_member``) for the membership
    mutation so RLS scopes it; ``admin_session`` for the join-to-users
    re-fetch of the display row (``users`` is not granted to
    ``pulse_member`` — see ``memberships.list_members``).
    """
    caller_user, membership = org_member

    try:
        target_user_id = uuid.UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="member not found") from exc

    existing = await memberships_repo.get_membership(
        session, org_id=membership.org_id, user_id=target_user_id
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="member not found")

    current_role = str(existing["role"])
    new_role = req.role

    if current_role != new_role:
        # Demoting an owner — enforce the "at least one owner" invariant.
        if current_role == "owner" and new_role != "owner":
            # Lock all owners to serialize concurrent demote/remove;
            # prevents the last-owner TOCTOU.
            await memberships_repo.lock_owners(session, membership.org_id)
            owners = await memberships_repo.count_owners(
                session, membership.org_id
            )
            if owners <= 1:
                raise HTTPException(
                    status_code=409, detail="at least one owner required"
                )

        updated = await memberships_repo.update_role(
            session,
            org_id=membership.org_id,
            user_id=target_user_id,
            role=new_role,
        )
        if updated is None:  # pragma: no cover — get_membership above guards
            raise HTTPException(status_code=404, detail="member not found")
        await record_audit(
            session,
            org_id=membership.org_id,
            user_id=caller_user.id,
            action="member.role_change",
            target_type="member",
            target_id=str(target_user_id),
            metadata={"from": current_role, "to": new_role},
        )
        await session.commit()

    # Re-fetch the joined row so we return display fields. Uses the
    # two-pass helper because the join needs SELECT on users.
    rows = await _list_members_two_pass(
        member_session=session,
        admin_session=admin_session,
        org_id=membership.org_id,
    )
    row = next(
        (r for r in rows if str(r["user_id"]) == str(target_user_id)),
        None,
    )
    if row is None:  # pragma: no cover — we just updated it
        raise HTTPException(status_code=404, detail="member not found")
    return MemberRow(
        user_id=str(row["user_id"]),
        email=str(row["email"]),
        name=(str(row["name"]) if row.get("name") is not None else None),
        role=str(row["role"]),
        joined_at=row["joined_at"],
    )


@router.delete("/api/orgs/me/members/{user_id}", status_code=204)
async def remove_org_member(
    user_id: str,
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    _owner_guard: OrganizationMembership = Depends(require_owner),
    session: AsyncSession = Depends(get_org_scoped_session),
    admin_session: AsyncSession = Depends(get_admin_session),
) -> None:
    """Owner-only. Remove a member from the active org.

    Refuses to remove the last owner (whether that's the caller or
    anyone else). On success, clears
    ``users.last_active_org_id`` for the removed user iff it was
    pointing at this org — otherwise their next sign-in would 403
    until they explicitly switched.

    The user row itself is preserved; they may belong to other orgs.
    """
    caller_user, membership = org_member

    try:
        target_user_id = uuid.UUID(user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="member not found") from exc

    existing = await memberships_repo.get_membership(
        session, org_id=membership.org_id, user_id=target_user_id
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="member not found")

    if str(existing["role"]) == "owner":
        # Lock all owners to serialize concurrent demote/remove;
        # prevents the last-owner TOCTOU.
        await memberships_repo.lock_owners(session, membership.org_id)
        owners = await memberships_repo.count_owners(session, membership.org_id)
        if owners <= 1:
            raise HTTPException(
                status_code=409, detail="at least one owner required"
            )

    ok = await memberships_repo.remove_member(
        session, org_id=membership.org_id, user_id=target_user_id
    )
    if not ok:  # pragma: no cover — get_membership above guards
        raise HTTPException(status_code=404, detail="member not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=caller_user.id,
        action="member.remove",
        target_type="member",
        target_id=str(target_user_id),
        metadata={"former_role": str(existing["role"])},
    )
    await session.commit()

    # Clear the user's last_active_org_id if it was pointing at this
    # org. Uses the admin session because ``users`` has no grant to
    # ``pulse_member``; the route gate is the auth boundary.
    await _reset_role_on_admin_session(admin_session)
    await orgs_repo.clear_last_active_org_if_match(
        admin_session,
        user_id=target_user_id,
        org_id=membership.org_id,
    )
    await admin_session.commit()


# ── Invites ───────────────────────────────────────────────────────────────


@router.get("/api/orgs/me/invites", response_model=list[InviteSummary])
async def list_org_invites(
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    member_session: AsyncSession = Depends(get_org_scoped_session),
    admin_session: AsyncSession = Depends(get_admin_session),
) -> list[InviteSummary]:
    """List pending (non-expired, unaccepted) invites for the active org.

    Two-pass like the member listing: invite rows via the RLS-scoped
    ``member_session``, inviter display email via the BYPASSRLS
    ``admin_session`` (``pulse_member`` has no SELECT on ``users``).
    """
    _, membership = org_member
    rows = await invites_repo.list_pending_invite_rows(
        member_session, membership.org_id
    )
    if not rows:
        return []
    inviter_ids = [
        str(r["invited_by_user_id"])
        for r in rows
        if r.get("invited_by_user_id") is not None
    ]
    await _reset_role_on_admin_session(admin_session)
    user_map = await memberships_repo.list_user_display_fields(
        admin_session, inviter_ids
    )
    out: list[InviteSummary] = []
    for r in rows:
        inviter_id = r.get("invited_by_user_id")
        inviter_email = None
        if inviter_id is not None:
            u = user_map.get(str(inviter_id))
            if u is not None:
                inviter_email = str(u["email"])
        out.append(
            InviteSummary(
                id=str(r["id"]),
                email=str(r["email"]),
                role=str(r["role"]),
                created_at=r["created_at"],
                expires_at=r["expires_at"],
                invited_by_email=inviter_email,
            )
        )
    return out


@router.post(
    "/api/orgs/me/invites", status_code=201, response_model=InviteSummary
)
async def create_invite(
    req: CreateInviteRequest,
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    _owner_guard: OrganizationMembership = Depends(require_owner),
    session: AsyncSession = Depends(get_org_scoped_session),
    admin_session: AsyncSession = Depends(get_admin_session),
) -> InviteSummary:
    """Owner-only. Create a pending invite + send the email.

    Validation:

    * 409 if the email is already a member of this org.
    * 409 if a pending non-expired invite already exists for
      ``(org_id, email)`` — re-sending the link would just confuse the
      recipient.

    Storage: only the SHA-256 hash of the signed token lives in the DB.
    The raw token is embedded in the outbound email and is never
    persisted nor returned in the response.

    Uses both sessions: ``session`` (``pulse_member``) writes the
    invite under org-RLS; ``admin_session`` does the
    ``has_membership_in_org`` check that joins through ``users``.
    """
    user, membership = org_member
    target_email = req.email.lower().strip()

    await _reset_role_on_admin_session(admin_session)
    already_member = await memberships_repo.is_existing_user_membership(
        member_session=session,
        admin_session=admin_session,
        org_id=membership.org_id,
        email=target_email,
    )
    if already_member:
        raise HTTPException(
            status_code=409, detail="user is already a member of this organization"
        )

    existing_invite = await invites_repo.find_pending_invite_for_email(
        session, org_id=membership.org_id, email=target_email
    )
    if existing_invite is not None:
        raise HTTPException(
            status_code=409, detail="invite already pending for this email"
        )

    # Expiry — use the configured max-age so the link the email contains
    # and the DB row agree on when it stops working.
    expires_at = utcnow_naive() + _max_age_delta()

    row, raw_token = await invites_repo.create_invite(
        session,
        org_id=membership.org_id,
        email=target_email,
        role=req.role,
        invited_by_user_id=user.id,
        expires_at=expires_at,
    )
    # Look up the org name so the email body can be human-readable.
    org_row = await orgs_repo.get_for_member(session, membership.org_id)
    org_name = str(org_row["name"]) if org_row else "your organization"

    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="member.invite",
        target_type="invite",
        target_id=str(row["id"]),
        metadata={"email": target_email, "role": req.role},
    )
    await session.commit()

    # Send the email AFTER the invite row + audit commit above so the
    # network-bound send never runs while that write's transaction is
    # still open (audit finding M7). Best-effort — never raises.
    subject, body = org_invite_email(
        raw_token,
        org_name=org_name,
        inviter_name=user.name,
        role=req.role,
    )
    await email_module.send_email(target_email, subject, body)

    return InviteSummary(
        id=str(row["id"]),
        email=str(row["email"]),
        role=str(row["role"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        invited_by_email=user.email,
    )


@router.delete("/api/orgs/me/invites/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: str,
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    _owner_guard: OrganizationMembership = Depends(require_owner),
    session: AsyncSession = Depends(get_org_scoped_session),
) -> None:
    """Owner-only. Revoke a pending invite.

    Stamps a dedicated ``revoked_at`` column (added in 0006) so the
    public token-resolve endpoint can return ``status = "revoked"`` —
    distinct from ``"accepted"``, so the acceptance UI can render an
    actionable "this invite was revoked, ask the owner for a new
    link" message instead of the misleading "already used" copy.
    """
    user, membership = org_member
    try:
        as_uuid = uuid.UUID(invite_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="invite not found") from exc

    ok = await invites_repo.revoke_pending(
        session, invite_id=as_uuid, org_id=membership.org_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="invite not found")
    await record_audit(
        session,
        org_id=membership.org_id,
        user_id=user.id,
        action="member.invite_revoke",
        target_type="invite",
        target_id=str(as_uuid),
        metadata=None,
    )
    await session.commit()


def _max_age_delta():
    """Translate ``settings.invite_token_max_age_seconds`` to a timedelta."""
    from datetime import timedelta

    return timedelta(seconds=settings.invite_token_max_age_seconds)


# ── Activity feed ─────────────────────────────────────────────────────────


# Bounds for the activity-list endpoint. Default keeps the payload small
# enough to render on a phone; the max is generous for power users
# triaging a noisy day.
_ACTIVITY_DEFAULT_LIMIT = 50
_ACTIVITY_MAX_LIMIT = 200


def _parse_activity_cursor(
    cursor: str | None,
) -> tuple[datetime, uuid.UUID] | None:
    """Parse the opaque cursor into ``(created_at, id)``.

    Wire format is ``"<iso8601>|<uuid>"``. Both halves are required so
    the SQL can do a stable composite comparison and never drop a row
    with a duplicate ``created_at``. We avoid signing it because the
    surface is already authed.
    """
    if cursor is None or cursor == "":
        return None
    try:
        ts_part, _, id_part = cursor.partition("|")
        if not ts_part or not id_part:
            raise ValueError("cursor missing ts or id half")
        return datetime.fromisoformat(ts_part), uuid.UUID(id_part)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail="invalid cursor"
        ) from exc


@router.get("/api/orgs/me/activity", response_model=ActivityPage)
async def list_activity(
    limit: int = _ACTIVITY_DEFAULT_LIMIT,
    cursor: str | None = None,
    actor_user_id: str | None = None,
    action: str | None = None,
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
    member_session: AsyncSession = Depends(get_org_scoped_session),
    admin_session: AsyncSession = Depends(get_admin_session),
) -> ActivityPage:
    """Paginated activity feed for the active org.

    Visible to any member of the org (read-only surface — every write
    that lands here was already gated on the originating route).

    Args:
        limit: Page size; clamped to ``[1, _ACTIVITY_MAX_LIMIT]``.
        cursor: Opaque ``created_at`` of the previous page's last row.
            Pass the value of ``next_cursor`` from the previous response.
        actor_user_id: Optional filter — only entries by this user.
        action: Optional exact-match filter on the action enum.
        org_member: Caller's resolved ``(user, membership)``.
        member_session: ``pulse_member`` session; RLS-scoped to the
            active org so a forgotten predicate cannot leak.
        admin_session: BYPASSRLS session used purely for the
            actor-display join (``users`` is not granted to
            ``pulse_member``).

    Returns:
        ``ActivityPage`` with up to ``limit`` entries in reverse-
        chronological order plus an opaque ``next_cursor`` (``None``
        when this is the last page).
    """
    _, membership = org_member
    bounded_limit = max(1, min(int(limit), _ACTIVITY_MAX_LIMIT))
    cursor_pair = _parse_activity_cursor(cursor)

    parsed_actor: uuid.UUID | None = None
    if actor_user_id:
        try:
            parsed_actor = uuid.UUID(actor_user_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="invalid actor_user_id"
            ) from exc

    if action is not None and action not in AUDIT_ACTIONS:
        raise HTTPException(status_code=422, detail="invalid action")

    rows = await audit_logs_repo.list_for_org(
        member_session,
        org_id=membership.org_id,
        limit=bounded_limit,
        cursor=cursor_pair,
        actor_user_id=parsed_actor,
        action=action,
    )

    # Two-pass actor enrichment. The `users` table is not granted to
    # `pulse_member`; we resolve display fields against `pulse_admin`.
    actor_ids = sorted(
        {str(r["user_id"]) for r in rows if r.get("user_id") is not None}
    )
    if actor_ids:
        await _reset_role_on_admin_session(admin_session)
        actor_map = await memberships_repo.list_user_display_fields(
            admin_session, actor_ids
        )
    else:
        actor_map = {}

    entries: list[ActivityEntry] = []
    for r in rows:
        actor_uid = r.get("user_id")
        actor_uid_str = str(actor_uid) if actor_uid is not None else None
        actor_row = actor_map.get(actor_uid_str) if actor_uid_str else None
        entries.append(
            ActivityEntry(
                id=str(r["id"]),
                created_at=r["created_at"],
                actor=ActivityActor(
                    user_id=actor_uid_str,
                    email=(
                        str(actor_row["email"]) if actor_row else None
                    ),
                    name=(
                        (str(actor_row["name"]) if actor_row.get("name") else None)
                        if actor_row
                        else None
                    ),
                ),
                action=str(r["action"]),
                target_type=(
                    str(r["target_type"])
                    if r.get("target_type") is not None
                    else None
                ),
                target_id=(
                    str(r["target_id"])
                    if r.get("target_id") is not None
                    else None
                ),
                metadata=r.get("metadata"),
            )
        )

    next_cursor: str | None = None
    if len(entries) == bounded_limit and rows:
        last_row = rows[-1]
        last_created = last_row["created_at"]
        last_id = last_row["id"]
        if isinstance(last_created, datetime):
            next_cursor = f"{last_created.isoformat()}|{last_id}"

    return ActivityPage(entries=entries, next_cursor=next_cursor)
