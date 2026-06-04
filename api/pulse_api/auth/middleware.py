"""FastAPI dependencies that resolve the current operator + active org.

Three layers, used by every authenticated route:

* ``get_current_user`` — resolves a ``User`` from the session cookie OR
  ``Authorization: Bearer pulse_<key>``. Returns the user only. Does NOT
  set any DB-side org GUC; callers that only need the user (the
  ``/api/auth/me`` family) stop here.

* ``get_current_org_member`` — resolves ``(user, membership)`` for the
  caller's currently-active org. The active org is sourced from:
    1. The API key's ``org_id`` if the request authenticated by Bearer.
    2. Else the session payload's ``active_org_id``.
    3. Else ``users.last_active_org_id``.
  If none of those resolve to an org the user is a current member of,
  the request is rejected with 403.

* ``get_org_scoped_session`` — yields an ``AsyncSession`` bound to the
  ``pulse_member`` engine with ``pulse.org_id`` set to the resolved
  active org. This is what ``/api/admin/*`` routes use: RLS narrows
  every query to the active org, so even a bug-prone handler that
  forgets to filter cannot leak cross-tenant rows.

Cookie wins over Bearer when both are present, EXCEPT for the org
attribution: a Bearer call always uses the key's ``org_id``, never the
cookie's. This is what lets a developer hold one shell session against
org A while running a script for org B via a Bearer key — the script's
org context is whatever its key was minted for.

``require_owner`` is a dependency that adds the ``owner``-role gate on
top of ``get_current_org_member``. Member-allowed routes don't need it.

``get_current_superadmin`` is the cross-org escape hatch — it gates on
``users.is_superadmin`` and uses ``get_admin_session`` (BYPASSRLS)
because superadmin work crosses every tenant by definition.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth import api_keys as api_keys_lib
from pulse_api.auth.session import (
    InvalidSessionError,
    decode_session_payload,
    encode_session,
)
from pulse_api.config import settings
from pulse_api.db import get_admin_session, member_engine
from pulse_api.models import ApiKey, OrganizationMembership, User
from pulse_api.repos import users as users_repo

# Request-state key under which we stash the (user, api_key) tuple
# resolved by ``_user_from_bearer`` so that ``get_current_org_member``
# can later read the api key's ``org_id`` without re-running the bearer
# validation. This is a request-scoped cache, not a global.
_REQUEST_API_KEY_ATTR = "pulse_resolved_api_key"

# Marker we attach to the request when the session payload was missing
# an ``active_org_id`` and we backfilled from ``users.last_active_org_id``.
# A middleware-level response post-processor would normally pick this up
# and re-issue the cookie. We instead handle the re-issue inline in
# ``get_current_org_member`` by setting it on the FastAPI response via
# the dependency's access to the response object. To keep that change
# small and not require every admin route to thread a response, we
# perform the persistence to ``users.last_active_org_id`` server-side
# whenever the session payload is missing an org — the next sign-in
# will then mint a session with the right shape.


async def _user_from_cookie(
    pulse_session: str, session: AsyncSession
) -> tuple[User, dict] | None:
    """Decode the cookie and load the ``User`` row + the decoded payload."""
    try:
        payload = decode_session_payload(
            pulse_session, settings.session_max_age_seconds
        )
    except InvalidSessionError:
        return None
    user = await users_repo.get_user_by_id(session, payload["user_id"])
    if user is None:
        return None
    return user, payload


async def _user_from_bearer(
    authorization: str, session: AsyncSession
) -> tuple[User, ApiKey] | None:
    """Resolve ``Authorization: Bearer pulse_<key>`` to (user, api_key)."""
    return await api_keys_lib.verify_bearer(authorization, session)


async def get_current_user(
    request: Request,
    pulse_session: str | None = Cookie(
        default=None, alias=settings.session_cookie_name
    ),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_admin_session),
) -> User:
    """Resolve the caller's ``User`` from cookie or Bearer.

    User-identity precedence: cookie wins if both are present.
    Org-attribution precedence (consumed in
    ``_resolve_active_org_id``): a valid Bearer key's ``org_id`` wins
    regardless of the cookie. We capture both on ``request.state`` so
    the downstream deps can apply the right rule:

      * ``pulse_session_payload`` is set when a cookie authenticated.
      * the ApiKey is stashed when a Bearer header was present and
        resolved cleanly — even alongside a cookie. That lets a script
        bring `--cookie session --header Bearer ...` to swap orgs
        without re-logging in.
    """
    user_from_cookie = None
    if pulse_session:
        from_cookie = await _user_from_cookie(pulse_session, session)
        if from_cookie is not None:
            user_from_cookie, payload = from_cookie
            request.state.pulse_session_payload = payload

    if authorization:
        from_bearer = await _user_from_bearer(authorization, session)
        if from_bearer is not None:
            bearer_user, api_key = from_bearer
            # Only cache the key for org attribution if the bearer
            # resolves to the same user as the cookie (or no cookie at
            # all). A mismatched user+bearer pair would let an
            # attacker who stole a cookie supply their own bearer to
            # ride the cookie's identity into another org — refuse.
            if (
                user_from_cookie is None
                or user_from_cookie.id == bearer_user.id
            ):
                setattr(request.state, _REQUEST_API_KEY_ATTR, api_key)
            if user_from_cookie is None:
                return bearer_user

    if user_from_cookie is not None:
        return user_from_cookie

    raise HTTPException(status_code=401, detail="not authenticated")


async def _resolve_active_org_id(
    request: Request, user: User
) -> uuid.UUID | None:
    """Pick the active org id from request context, in priority order.

    1. API key's org (Bearer auth wins for attribution).
    2. Session payload's ``active_org_id``.
    3. User's ``last_active_org_id``.

    Returns ``None`` if none of the above resolves — caller turns that
    into a 403.
    """
    api_key: ApiKey | None = getattr(request.state, _REQUEST_API_KEY_ATTR, None)
    if api_key is not None:
        return api_key.org_id

    payload: dict | None = getattr(request.state, "pulse_session_payload", None)
    if payload is not None:
        org_id_raw = payload.get("active_org_id")
        if org_id_raw:
            try:
                return uuid.UUID(str(org_id_raw))
            except (ValueError, TypeError):
                return None

    return user.last_active_org_id


async def _lookup_membership(
    session: AsyncSession, *, user_id: uuid.UUID, org_id: uuid.UUID
) -> OrganizationMembership | None:
    """Fetch the (user, org) membership row or None."""
    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.org_id == org_id,
        )
    )
    return result.scalar_one_or_none()


async def get_current_org_member(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_admin_session),
) -> tuple[User, OrganizationMembership]:
    """Resolve ``(user, membership)`` for the caller's active org.

    Raises 403 if no active org can be resolved, or if the resolved org
    is not one the caller is currently a member of.

    Side effect: if the active org came from ``users.last_active_org_id``
    rather than the session payload (historical cookie pre-multi-tenant),
    we update ``last_active_org_id`` to the resolved value so the next
    sign-in mints a cookie with the right shape. The current cookie is
    not re-issued — doing so would require threading a Response into
    every dependency. Acceptable because the next ``/api/auth/login``
    or OAuth callback rewrites the cookie anyway.
    """
    active_org_id = await _resolve_active_org_id(request, user)
    if active_org_id is None:
        raise HTTPException(
            status_code=403, detail="no active organization for this user"
        )

    membership = await _lookup_membership(
        session, user_id=user.id, org_id=active_org_id
    )
    if membership is None:
        raise HTTPException(
            status_code=403,
            detail="user is not a member of the active organization",
        )

    # Best-effort persistence: if the session payload was missing an
    # ``active_org_id`` we still know the right org now. Push it onto
    # ``users.last_active_org_id`` so the next login mints a cookie that
    # carries it. Bearer-auth requests skip this — the key's org_id is
    # already the authoritative source.
    payload: dict | None = getattr(request.state, "pulse_session_payload", None)
    via_bearer = (
        getattr(request.state, _REQUEST_API_KEY_ATTR, None) is not None
    )
    if (
        not via_bearer
        and payload is not None
        and "active_org_id" not in payload
        and user.last_active_org_id != active_org_id
    ):
        await session.execute(
            text(
                "update public.users set last_active_org_id = "
                "cast(:org as uuid) where id = cast(:uid as uuid)"
            ),
            {"org": str(active_org_id), "uid": str(user.id)},
        )
        await session.commit()

    return user, membership


def _membership_from_org_member(
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> OrganizationMembership:
    """Adapter — strips the user, returning only the membership."""
    return org_member[1]


def require_owner(
    membership: OrganizationMembership = Depends(_membership_from_org_member),
) -> OrganizationMembership:
    """Dependency that returns the membership iff its role is ``owner``.

    Raises 403 for member-role callers. Mount on owner-gated endpoints
    (org settings, member management, invites, etc.).

    The role column is plain text on disk (see the migration), so the
    instance attribute can be either a ``MemberRole`` enum or its
    string value depending on how the row was constructed. Compare via
    string-ifying so both shapes work.
    """
    role = membership.role.value if hasattr(membership.role, "value") else membership.role
    if role != "owner":
        raise HTTPException(status_code=403, detail="owner role required")
    return membership


async def get_current_superadmin(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Gate on ``users.is_superadmin``.

    Superadmin routes use ``get_admin_session`` (BYPASSRLS) directly
    because their job is cross-tenant by definition. The auth gate is
    purely at the application layer — there is no GUC to set.
    """
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="superadmin only")
    return user


async def get_org_scoped_session(
    org_member: tuple[User, OrganizationMembership] = Depends(
        get_current_org_member
    ),
) -> AsyncIterator[AsyncSession]:
    """Yield a ``pulse_member`` session with ``pulse.org_id`` set.

    This is what every ``/api/admin/*`` route depends on. The role has
    no ``BYPASSRLS``, so a forgotten ``where org_id = ...`` in a handler
    cannot leak — Postgres refuses the row.

    Note: in tests the ``client`` fixture overrides this dep to bind
    through the shared rollback connection. See ``api/tests/conftest.py``.
    """
    _, membership = org_member
    async with member_engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(
                text("select set_config('pulse.org_id', :org_id, true)"),
                {"org_id": str(membership.org_id)},
            )
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
                yield session
            await trans.commit()
        except Exception:
            await trans.rollback()
            raise


# ── Compatibility re-exports (intentionally narrow) ──────────────────────


# Re-export ``encode_session`` so legacy call sites (login + change-
# password routes) can use it without importing from ``auth.session``
# directly. Helps keep "if you import session helpers, import them
# from middleware" as a one-stop story.
__all__ = [
    "get_current_user",
    "get_current_org_member",
    "get_current_superadmin",
    "get_org_scoped_session",
    "require_owner",
    "encode_session",
]
