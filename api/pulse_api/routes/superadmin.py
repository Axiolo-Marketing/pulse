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

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import email as email_module
from pulse_api import storage
from pulse_api.audit import record_audit
from pulse_api.auth.email_messages import org_invite_email
from pulse_api.auth.middleware import get_current_superadmin
from pulse_api.config import settings
from pulse_api.db import get_admin_session
from pulse_api.models import User
from pulse_api.models._helpers import utcnow_naive
from pulse_api.repos import card_generations as card_generations_repo
from pulse_api.repos import invites as invites_repo
from pulse_api.repos import memberships as memberships_repo
from pulse_api.repos import orgs as orgs_repo

logger = logging.getLogger(__name__)

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

# Reactive-cards usage report window bounds (days).
_USAGE_DAYS_DEFAULT = 30
_USAGE_DAYS_MIN = 1
_USAGE_DAYS_MAX = 365

# Monthly usage table is always a fixed trailing window — not
# caller-configurable — so it stays independent of the days selector.
_USAGE_MONTHLY_MONTHS = 6


# ── Request / response models ─────────────────────────────────────────────


class OrgListRow(BaseModel):
    """Row in the ``GET /api/superadmin/orgs`` listing.

    ``reactive_cards_allowed`` is included so the superadmin org table's
    per-org toggle can render its current state
    directly from the listing — the UI mutates via ``PATCH
    /api/superadmin/orgs/{id}`` and then re-fetches this listing rather
    than holding separate state, so this field must round-trip here too.
    """

    id: str
    name: str
    slug: str
    member_count: int
    pending_invite_count: int
    created_at: object  # datetime — Pydantic v2 handles it
    owner_emails: list[str]
    reactive_cards_allowed: bool


class CreateOrgRequest(BaseModel):
    """Body for ``POST /api/superadmin/orgs``.

    Slug is intentionally caller-supplied. We don't auto-derive from
    ``name`` because the operator may want a different shape (e.g.
    "Acme Co." → ``acme``, not ``acme-co``); humans pick better than
    deterministic slugifiers in onboarding.
    """

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=_SLUG_MIN_LEN, max_length=_SLUG_MAX_LEN)
    # Omit to create an org the calling superadmin manages alone: they're
    # added as its owner directly and nobody is invited or emailed. Pass an
    # email to invite someone else as owner — that emails them a join link
    # and, once accepted, gives them their own admin-console login.
    owner_email: EmailStr | None = None
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
    # None when no owner was invited (the superadmin owns the org).
    invite: CreatedInviteSummary | None = None


class SuperadminMemberRow(BaseModel):
    """Row in the superadmin "view members of any org" endpoint."""

    user_id: str
    email: str
    name: str | None
    role: str
    joined_at: object  # datetime


class UpdateOrgFlagsRequest(BaseModel):
    """Body for ``PATCH /api/superadmin/orgs/{org_id}``.

    Currently a single admin-managed flag: the org-level gate for the
    reactive-cards feature (see ``pulse_api.reactive`` module docstring
    for the full three-gate chain — deployment, org, engagement). An
    org member can only turn on their own engagement's toggle while
    this is ``True``.
    """

    reactive_cards_allowed: bool


class OrgFlagsRow(BaseModel):
    """Returned by ``PATCH /api/superadmin/orgs/{org_id}``."""

    id: str
    name: str
    slug: str
    reactive_cards_allowed: bool


class ReactiveUsageOrgRow(BaseModel):
    """One org's row in the reactive-cards usage report."""

    org_id: str
    org_name: str
    generations: int
    completed: int
    skipped: int
    failed: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ReactiveUsageTotals(BaseModel):
    """All-orgs sums across the same window as ``orgs`` in
    :class:`ReactiveUsageResponse`."""

    generations: int
    completed: int
    skipped: int
    failed: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ReactiveUsageEngagementRow(BaseModel):
    """One engagement's row in the per-engagement usage/cost drill-down
    (same ``days`` window as ``orgs``). Only engagements with at least
    one generation in the window appear."""

    engagement_id: str
    engagement_label: str
    org_id: str
    org_name: str
    generations: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ReactiveUsageMonthlyRow(BaseModel):
    """One ``(month, org)`` row in the trailing-6-calendar-month cost
    table. Independent of the ``days`` window selector — always the
    last 6 calendar months. ``month`` is a ``"YYYY-MM"`` label."""

    month: str
    org_id: str
    org_name: str
    generations: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ReactiveUsageResponse(BaseModel):
    """``GET /api/superadmin/reactive-usage`` body.

    ``orgs``/``totals`` and ``engagements`` share the same ``days``
    window; ``monthly`` is always the trailing 6 calendar months
    regardless of ``days``."""

    days: int
    orgs: list[ReactiveUsageOrgRow]
    totals: ReactiveUsageTotals
    engagements: list[ReactiveUsageEngagementRow]
    monthly: list[ReactiveUsageMonthlyRow]


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
            reactive_cards_allowed=bool(r.get("reactive_cards_allowed")),
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
    """Create a new org, owned either by the caller or by an invited owner.

    Without ``owner_email`` the calling superadmin is added as the org's
    owner and nothing is emailed — for tenants the operator runs alone.
    With it, the sequence below invites that person as owner.

    Sequenced writes:

    1. Validate slug shape + uniqueness (409 on collision).
    2. Insert ``organizations`` row.
    3. Insert ``organization_invites`` row for ``owner_email`` with
       ``role='owner'`` and a 7-day expiry, plus the audit rows, then
       commit — half-created orgs without an owner invite would strand
       the operator with an unjoinable tenant, so a failure at any of
       these DB steps rolls back the whole transaction.
    4. Only once that's durably committed: sign + email the invite link.
       Sending happens after the commit (not inside the same
       transaction) so the network-bound send never holds the write
       open, and a send hiccup (``send_email`` is best-effort and never
       raises) can't roll back an org that was actually created.

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
    org_out = OrgRow(
        id=str(org_row["id"]),
        name=str(org_row["name"]),
        slug=str(org_row["slug"]),
        created_at=org_row["created_at"],
    )

    if req.owner_email is None:
        # Operator-managed org: the superadmin owns it; no invite, no email.
        await memberships_repo.add_membership(
            session, org_id=org_id, user_id=user.id, role="owner"
        )
        await record_audit(
            session,
            org_id=org_id,
            user_id=user.id,
            action="org.create",
            target_type="org",
            target_id=str(org_id),
            metadata={"name": req.name.strip(), "slug": slug, "owner": "self"},
        )
        await session.commit()
        return CreateOrgResponse(org=org_out, invite=None)

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

    # Audit on the BYPASSRLS session — the superadmin lives outside the
    # org, so we attribute the action under the new org's id so it
    # surfaces in *that* org's activity feed (the owner reading their
    # new tenant's audit log sees "Tom (Axiolo superadmin) created this
    # org" as the first row). The invite emission is also worth a row.
    await record_audit(
        session,
        org_id=org_id,
        user_id=user.id,
        action="org.create",
        target_type="org",
        target_id=str(org_id),
        metadata={"name": req.name.strip(), "slug": slug},
    )
    await record_audit(
        session,
        org_id=org_id,
        user_id=user.id,
        action="member.invite",
        target_type="invite",
        target_id=str(invite_row["id"]),
        metadata={"email": owner_email, "role": "owner"},
    )
    await session.commit()

    # Send the invite email AFTER the org + invite rows commit above so
    # the network-bound send never runs while that write's transaction is
    # still open (audit finding M7). Best-effort — never raises.
    subject, body = org_invite_email(
        raw_token,
        org_name=req.name.strip(),
        inviter_name=user.name,
        role="owner",
    )
    await email_module.send_email(owner_email, subject, body)

    return CreateOrgResponse(
        org=org_out,
        invite=CreatedInviteSummary(
            id=str(invite_row["id"]),
            email=str(invite_row["email"]),
            expires_at=invite_row["expires_at"],
        ),
    )


class OrgDeleteImpact(BaseModel):
    """What ``DELETE /api/superadmin/orgs/{id}`` would erase."""

    name: str
    clients: int
    engagements: int
    questions: int
    respondents: int
    answers: int
    files: int
    members: int
    pending_invites: int
    api_keys: int


def _has_data(impact: dict[str, int]) -> bool:
    """Anything beyond an empty shell (one or no member, nothing else)."""
    return (
        impact["clients"] > 0
        or impact["engagements"] > 0
        or impact["api_keys"] > 0
        or impact["members"] > 1
    )


async def _load_org(session: AsyncSession, org_id: str) -> tuple[uuid.UUID, dict]:
    # Malformed UUID → 404 (same shape as "no such org") so a probing
    # client can't tell which.
    try:
        as_uuid = uuid.UUID(org_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="organization not found") from exc
    row = await orgs_repo.get_by_id(session, as_uuid)
    if row is None:
        raise HTTPException(status_code=404, detail="organization not found")
    return as_uuid, row


@router.get(
    "/api/superadmin/orgs/{org_id}/delete-impact", response_model=OrgDeleteImpact
)
async def org_delete_impact(
    org_id: str,
    user: User = Depends(get_current_superadmin),
    session: AsyncSession = Depends(get_admin_session),
) -> OrgDeleteImpact:
    """Counts of everything deleting this org would erase, for the confirm
    dialog."""
    as_uuid, row = await _load_org(session, org_id)
    impact = await orgs_repo.delete_impact(session, as_uuid)
    return OrgDeleteImpact(name=str(row["name"]), **impact)


@router.delete("/api/superadmin/orgs/{org_id}", status_code=204)
async def delete_org(
    org_id: str,
    confirm: str | None = None,
    user: User = Depends(get_current_superadmin),
    session: AsyncSession = Depends(get_admin_session),
) -> None:
    """Delete an organization and everything in it.

    An empty org (no clients, engagements or API keys; at most one member)
    deletes outright. Anything more requires ``?confirm=<org name>``
    (case-insensitive, surrounding whitespace ignored) — the UI makes the
    superadmin type it after showing ``/delete-impact``. Then engagements,
    questions, respondents, answers, uploaded files, clients, contacts,
    memberships, invites, API keys and the org's activity log are all
    erased; user accounts are kept. Irreversible (backups only).

    The action is audited in one of the caller's own orgs — their last
    active org, else their oldest other membership — since the deleted
    org's log goes with it. A superadmin with no other membership leaves
    only the warning log line.

    Status codes:

    * 204 — deleted.
    * 404 — unknown org id (or malformed UUID).
    * 409 — the org has data and ``confirm`` is missing or doesn't match.
    """
    as_uuid, row = await _load_org(session, org_id)
    name = str(row.get("name"))
    impact = await orgs_repo.delete_impact(session, as_uuid)

    if _has_data(impact):
        if (confirm or "").strip().casefold() != name.strip().casefold():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"organization has {impact['clients']} client(s), "
                    f"{impact['engagements']} engagement(s) and "
                    f"{impact['members']} member(s); confirm with the "
                    "organization's name to delete everything"
                ),
            )

    # Audit where it will survive: the caller's last active org, else any
    # other org they belong to — never the org being deleted.
    audit_org = user.last_active_org_id
    if audit_org is None or audit_org == as_uuid:
        audit_org = await memberships_repo.first_other_org_for_user(
            session, user_id=user.id, exclude_org_id=as_uuid
        )
    if audit_org is not None:
        await record_audit(
            session,
            org_id=audit_org,
            user_id=user.id,
            action="org.delete",
            target_type="org",
            target_id=str(as_uuid),
            metadata={
                "name": name,
                "slug": str(row.get("slug")),
                "erased": impact,
            },
        )

    deleted, paths = await orgs_repo.delete_org_and_data(session, as_uuid)
    if not deleted:  # pragma: no cover — _load_org above guards
        raise HTTPException(status_code=404, detail="organization not found")
    await session.commit()
    logger.warning(
        "superadmin %s deleted org %s (%s): %s%s",
        user.id, as_uuid, name, impact,
        "" if audit_org is not None else " (no org to audit in)",
    )
    # Files go after the commit: a failed unlink leaves an orphan file
    # (cheap to clean up, and logged), never a dangling row.
    for path in paths:
        storage.delete_upload(path)


@router.patch(
    "/api/superadmin/orgs/{org_id}", response_model=OrgFlagsRow
)
async def update_org_flags(
    org_id: str,
    req: UpdateOrgFlagsRequest,
    user: User = Depends(get_current_superadmin),
    session: AsyncSession = Depends(get_admin_session),
) -> OrgFlagsRow:
    """Flip an org's reactive-cards allow gate.

    The only admin-managed org flag today. Superadmin-only, BYPASSRLS —
    the target org need not be one the caller belongs to. Audited under
    the existing ``org.update`` action (the same one ``PATCH
    /api/orgs/me`` uses for a name change) with ``changed_fields`` +
    before/after values in the metadata, so the affected org's own
    Activity feed shows exactly what changed and by whom.

    Status codes:

    * 200 — updated; returns the refreshed row.
    * 404 — unknown org id (or malformed UUID).
    """
    try:
        as_uuid = uuid.UUID(org_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="organization not found") from exc

    previous = await orgs_repo.get_by_id(session, as_uuid)
    if previous is None:
        raise HTTPException(status_code=404, detail="organization not found")

    row = await orgs_repo.set_reactive_cards_allowed(
        session, as_uuid, allowed=req.reactive_cards_allowed
    )
    if row is None:  # pragma: no cover — previous fetch above guards
        raise HTTPException(status_code=404, detail="organization not found")

    await record_audit(
        session,
        org_id=as_uuid,
        user_id=user.id,
        action="org.update",
        target_type="org",
        target_id=str(as_uuid),
        metadata={
            "changed_fields": ["reactive_cards_allowed"],
            "old_reactive_cards_allowed": bool(
                previous.get("reactive_cards_allowed")
            ),
            "new_reactive_cards_allowed": bool(row.get("reactive_cards_allowed")),
        },
    )
    await session.commit()

    return OrgFlagsRow(
        id=str(row["id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        reactive_cards_allowed=bool(row.get("reactive_cards_allowed")),
    )


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


@router.get(
    "/api/superadmin/reactive-usage", response_model=ReactiveUsageResponse
)
async def reactive_usage(
    days: int = _USAGE_DAYS_DEFAULT,
    _: User = Depends(get_current_superadmin),
    session: AsyncSession = Depends(get_admin_session),
) -> ReactiveUsageResponse:
    """Reactive-cards usage + cost report across every org.

    Operating-cost monitoring for the reactive-cards LLM feature — NOT a
    billing surface (no per-customer invoicing hooks off this; see the
    plan's "monitoring/reporting only" note). Three complementary views,
    all served by the ``(org_id, created_at)`` index from migration 0017:

    - ``orgs`` / ``totals`` — per-org aggregates over the trailing
      ``days``-day window (clamped to ``[1, 365]``; default 30). The
      ``totals`` row sums the per-org rows in Python rather than a
      second query.
    - ``engagements`` — per-engagement drill-down over the SAME ``days``
      window (``card_generations_repo.usage_report_by_engagement``).
      Only engagements with at least one generation in the window
      appear.
    - ``monthly`` — per-``(month, org)`` cost trend across the trailing
      6 CALENDAR months (``card_generations_repo.usage_report_monthly``),
      independent of ``days``.

    Superadmin-only — 403 for any other caller via
    ``get_current_superadmin``.

    Args:
        days: Trailing window size in days, applied to ``orgs``/
            ``totals``/``engagements``. Clamped to ``[1, 365]``.
    """
    bounded_days = max(_USAGE_DAYS_MIN, min(int(days), _USAGE_DAYS_MAX))
    rows = await card_generations_repo.usage_report(session, days=bounded_days)

    org_rows = [
        ReactiveUsageOrgRow(
            org_id=str(r["org_id"]),
            org_name=str(r["org_name"]),
            generations=int(r["generations"]),
            completed=int(r["completed"]),
            skipped=int(r["skipped"]),
            failed=int(r["failed"]),
            input_tokens=int(r["input_tokens"]),
            output_tokens=int(r["output_tokens"]),
            cost_usd=float(r["cost_usd"]),
        )
        for r in rows
    ]
    totals = ReactiveUsageTotals(
        generations=sum(r.generations for r in org_rows),
        completed=sum(r.completed for r in org_rows),
        skipped=sum(r.skipped for r in org_rows),
        failed=sum(r.failed for r in org_rows),
        input_tokens=sum(r.input_tokens for r in org_rows),
        output_tokens=sum(r.output_tokens for r in org_rows),
        cost_usd=sum(r.cost_usd for r in org_rows),
    )

    engagement_rows_raw = await card_generations_repo.usage_report_by_engagement(
        session, days=bounded_days
    )
    engagement_rows = [
        ReactiveUsageEngagementRow(
            engagement_id=str(r["engagement_id"]),
            engagement_label=str(r["engagement_label"]),
            org_id=str(r["org_id"]),
            org_name=str(r["org_name"]),
            generations=int(r["generations"]),
            input_tokens=int(r["input_tokens"]),
            output_tokens=int(r["output_tokens"]),
            cost_usd=float(r["cost_usd"]),
        )
        for r in engagement_rows_raw
    ]

    monthly_rows_raw = await card_generations_repo.usage_report_monthly(
        session, months=_USAGE_MONTHLY_MONTHS
    )
    monthly_rows = [
        ReactiveUsageMonthlyRow(
            month=str(r["month"]),
            org_id=str(r["org_id"]),
            org_name=str(r["org_name"]),
            generations=int(r["generations"]),
            input_tokens=int(r["input_tokens"]),
            output_tokens=int(r["output_tokens"]),
            cost_usd=float(r["cost_usd"]),
        )
        for r in monthly_rows_raw
    ]

    return ReactiveUsageResponse(
        days=bounded_days,
        orgs=org_rows,
        totals=totals,
        engagements=engagement_rows,
        monthly=monthly_rows,
    )
