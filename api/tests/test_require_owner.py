"""Tests for the ``require_owner`` dep.

PR 2 doesn't ship any owner-gated production routes — those land in
PR 3 (``/api/orgs/me/*``). To exercise ``require_owner`` end-to-end
without prematurely shipping a real endpoint, we mount a tiny dummy
router into the FastAPI app via a fixture, hit it, and tear it down.

Parametrized scenarios over ``(role, route_kind, expected_status)``:

| role   | route               | expected |
|--------|---------------------|----------|
| owner  | owner-gated         | 200      |
| member | owner-gated         | 403      |
| owner  | member-allowed      | 200      |
| member | member-allowed      | 200      |
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

from fastapi import Header, HTTPException

from pulse_api.auth.middleware import (
    get_current_org_member,
    get_org_scoped_session,
    require_owner,
)
from pulse_api.auth.session import encode_session
from pulse_api.config import settings
from pulse_api.db import get_admin_session, get_anon_session, get_session
from pulse_api.observability import limiter

# ── Dummy router that exercises both dep gates ───────────────────────────


def _build_dummy_router() -> APIRouter:
    router = APIRouter(prefix="/__test", tags=["test-only"])

    @router.get("/owner-only")
    async def owner_only(
        _: Any = Depends(require_owner),
    ) -> dict[str, str]:
        return {"ok": "owner"}

    @router.get("/member-allowed")
    async def member_allowed(
        _: Any = Depends(get_current_org_member),
    ) -> dict[str, str]:
        return {"ok": "member"}

    return router


# ── Fixture: standalone app + client with the dummy router mounted ───────


@pytest.fixture
async def test_app_client(
    db_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """Build a fresh FastAPI app with just the dummy router.

    We do NOT mutate the production app because mounting test-only
    routes there would leak across tests. The dep overrides mirror the
    main ``client`` fixture's pattern so the test runs against
    ``db_conn``'s rolled-back transaction.
    """
    limiter.reset()

    app = FastAPI()
    app.include_router(_build_dummy_router())

    async def _override_session() -> AsyncIterator[AsyncSession]:
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    async def _override_anon_session(
        x_pulse_token: str | None = Header(default=None, alias="X-Pulse-Token"),
    ) -> AsyncIterator[AsyncSession]:
        if not x_pulse_token:
            raise HTTPException(status_code=401, detail="missing token")
        await db_conn.execute(text("set local role pulse_anon"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    async def _override_org_scoped_session(
        org_member=Depends(get_current_org_member),
    ) -> AsyncIterator[AsyncSession]:
        _, membership = org_member
        await db_conn.execute(text("reset role"))
        await db_conn.execute(text("set local role pulse_member"))
        await db_conn.execute(
            text("select set_config('pulse.org_id', :o, true)"),
            {"o": str(membership.org_id)},
        )
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_admin_session] = _override_session
    app.dependency_overrides[get_anon_session] = _override_anon_session
    app.dependency_overrides[get_org_scoped_session] = _override_org_scoped_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Helpers ──────────────────────────────────────────────────────────────


async def _seed_user_with_role(
    db: AsyncSession, *, role: str, org_id: str
) -> str:
    """Insert a verified user attached to ``org_id`` with the given role.

    Returns the user id (string).
    """
    from pulse_api.auth.password import hash_password

    user_id = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, last_active_org_id, "
                " email_verified_at) "
                "values (:e, :h, :n, cast(:o as uuid), now()) "
                "returning id::text"
            ),
            {
                "e": f"{role}-{org_id[:8]}@example.com",
                "h": hash_password("test-pass-12345678"),
                "n": role.title(),
                "o": org_id,
            },
        )
    ).mappings().one()["id"]
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), :r)"
        ),
        {"o": org_id, "u": user_id, "r": role},
    )
    return user_id


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "role, route, expected_status",
    [
        ("owner",  "/__test/owner-only",     200),
        ("member", "/__test/owner-only",     403),
        ("owner",  "/__test/member-allowed", 200),
        ("member", "/__test/member-allowed", 200),
    ],
)
async def test_require_owner_gate(
    test_app_client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
    role: str,
    route: str,
    expected_status: int,
) -> None:
    """``require_owner`` admits owners only; members can reach
    ``get_current_org_member``-only routes regardless of role."""
    user_id = await _seed_user_with_role(
        db, role=role, org_id=axiolo_org["id"]
    )
    await db.flush()
    test_app_client.cookies.set(
        settings.session_cookie_name,
        encode_session(user_id, axiolo_org["id"]),
    )

    r = await test_app_client.get(route)
    assert r.status_code == expected_status, (
        f"role={role!r} route={route!r} expected={expected_status} "
        f"got={r.status_code} body={r.text!r}"
    )
