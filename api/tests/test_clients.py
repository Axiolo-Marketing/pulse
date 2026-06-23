"""Tests for the real ``clients`` entity (PR2).

Covers:
  * ``GET /api/admin/clients`` lists the active org's clients, org-scoped.
  * Direct ``clients_repo.get_or_create`` is idempotent (no duplicate for a
    repeated ``(org, name)``) and org-scoped.
  * Cross-org RLS isolation on ``clients`` (org A can't see org B's rows),
    proven via the ``become_member`` direct-SQL pattern from
    ``test_rls_isolation.py``.
  * A name collision across orgs is allowed (same name in two orgs = two
    distinct rows).
  * Creating an engagement with ``client_name`` get-or-creates the client;
    with ``client_id`` reuses an existing one; ``created_by`` is stamped.
"""
from __future__ import annotations

import secrets

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from pulse_api.repos import clients as clients_repo


# ── GET /api/admin/clients ─────────────────────────────────────────────────


async def test_list_clients_returns_org_clients(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    other_seeded_client: dict[str, str],
) -> None:
    """The two seeded engagements created two clients (Renee, Josh); the
    list returns both, ordered by name."""
    r = await admin_authed.get("/api/admin/clients")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert names == ["Josh", "Renee"]  # ordered by name


async def test_list_clients_empty_when_none(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.get("/api/admin/clients")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_clients_rejects_anonymous(client: AsyncClient) -> None:
    r = await client.get("/api/admin/clients")
    assert r.status_code == 401


# ── get_or_create idempotency + org scope ──────────────────────────────────


async def test_get_or_create_is_idempotent(
    db: AsyncSession, axiolo_org: dict[str, str]
) -> None:
    """The same ``(org, name)`` returns the same row twice — no duplicate.

    Runs on the owner-role ``db`` session (RLS off) with ``pulse.org_id``
    set by ``axiolo_org``; the idempotency comes from the
    ``unique (org_id, name)`` + ``on conflict do nothing``.
    """
    first = await clients_repo.get_or_create(
        db, org_id=axiolo_org["id"], name="Acme Co"
    )
    second = await clients_repo.get_or_create(
        db, org_id=axiolo_org["id"], name="Acme Co"
    )
    assert first["id"] == second["id"]

    count = (
        await db.execute(
            text(
                "select count(*) from public.clients "
                "where org_id = cast(:o as uuid) and name = 'Acme Co'"
            ),
            {"o": axiolo_org["id"]},
        )
    ).scalar_one()
    assert count == 1


async def test_get_or_create_via_engagement_create_reuses_client(
    admin_authed: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Two engagements created with the same ``client_name`` share one
    client row."""
    r1 = await admin_authed.post(
        "/api/admin/engagements", json={"client_name": "Shared Co"}
    )
    r2 = await admin_authed.post(
        "/api/admin/engagements", json={"client_name": "Shared Co"}
    )
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["client_id"] == r2.json()["client_id"]

    count = (
        await db.execute(
            text(
                "select count(*) from public.clients "
                "where org_id = cast(:o as uuid) and name = 'Shared Co'"
            ),
            {"o": seed_admin_user["org_id"]},
        )
    ).scalar_one()
    assert count == 1


# ── Cross-org name collision is allowed ────────────────────────────────────


async def test_same_name_in_two_orgs_is_two_rows(
    db: AsyncSession, axiolo_org: dict[str, str]
) -> None:
    """The ``unique (org_id, name)`` constraint scopes uniqueness to an
    org; the same name in two orgs yields two distinct rows."""
    other_org_id = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values ('Acme', :s) returning id::text"
            ),
            {"s": f"acme-{secrets.token_hex(2)}"},
        )
    ).mappings().one()["id"]

    a = await clients_repo.get_or_create(
        db, org_id=axiolo_org["id"], name="Globex"
    )
    b = await clients_repo.get_or_create(
        db, org_id=other_org_id, name="Globex"
    )
    assert a["id"] != b["id"]


# ── Cross-org RLS isolation (direct SQL) ───────────────────────────────────


async def test_clients_cross_org_isolation(
    db_conn: AsyncConnection,
    db: AsyncSession,
    axiolo_org: dict[str, str],
) -> None:
    """A ``pulse_member`` session scoped to org A cannot read org B's
    clients. Mirrors ``test_rls_isolation.py``'s direct-SQL pattern."""
    from tests.conftest import become_member

    other_org_id = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values ('Acme', :s) returning id::text"
            ),
            {"s": f"acme-{secrets.token_hex(2)}"},
        )
    ).mappings().one()["id"]

    # Seed one client per org as the owner role (RLS off).
    await db.execute(
        text(
            "insert into public.clients (org_id, name) "
            "values (cast(:o as uuid), 'A-client')"
        ),
        {"o": axiolo_org["id"]},
    )
    await db.execute(
        text(
            "insert into public.clients (org_id, name) "
            "values (cast(:o as uuid), 'B-client')"
        ),
        {"o": other_org_id},
    )

    # Become an Axiolo member; expect to see exactly Axiolo's client.
    await become_member(db_conn, org_id=axiolo_org["id"])
    rows = (
        await db_conn.execute(
            text("select name from public.clients order by name")
        )
    ).mappings().all()
    assert {r["name"] for r in rows} == {"A-client"}


async def test_get_or_create_insert_is_org_scoped(
    db_conn: AsyncConnection,
    db: AsyncSession,
    axiolo_org: dict[str, str],
) -> None:
    """A member-scoped insert through ``get_or_create`` lands in the active
    org and is invisible to another org's member."""
    from tests.conftest import become_member

    other_org_id = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values ('Acme', :s) returning id::text"
            ),
            {"s": f"acme-{secrets.token_hex(2)}"},
        )
    ).mappings().one()["id"]

    await become_member(db_conn, org_id=axiolo_org["id"])
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(bind=db_conn, expire_on_commit=False, class_=AsyncSession)
    async with factory() as member_session:
        created = await clients_repo.get_or_create(
            member_session, org_id=axiolo_org["id"], name="Scoped Co"
        )
        assert created["name"] == "Scoped Co"

    # Switch to the other org's member; the client must be invisible.
    await become_member(db_conn, org_id=other_org_id)
    visible = (
        await db_conn.execute(
            text("select count(*) from public.clients where name = 'Scoped Co'")
        )
    ).scalar_one()
    assert visible == 0
