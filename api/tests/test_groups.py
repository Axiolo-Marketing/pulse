"""Engagement-folder (``engagement_groups``) tests.

Three layers, mirroring the rest of the suite:

* **HTTP route layer** — drive the ``/api/admin/groups`` CRUD + the
  ``PATCH /api/admin/clients/{id}`` move through the real ASGI surface
  as the seeded admin (``admin_authed``). Assert the response shapes,
  the ``client_count`` projection, the ``group_id`` round-trip on the
  engagement, the ungroup-on-delete behaviour, and the cross-org 404 on
  a foreign folder id.
* **Audit** — every group mutation writes the right ``audit_logs`` row;
  the action enum is locked by ``test_audit_log.py`` (the static scan
  picks up ``group.create`` / ``group.update`` / ``group.delete`` from
  the route source).
* **RLS isolation** — direct ``pulse_member`` probes (``become_member``)
  prove org A cannot see / rename / delete org B's folder, same pattern
  as ``test_multi_tenant_isolation.py``.
"""
from __future__ import annotations

import secrets
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from pulse_api.auth.session import encode_session
from pulse_api.config import settings
from tests.conftest import become_member


# ── helpers ────────────────────────────────────────────────────────────────


async def _make_org(db: AsyncSession, name: str) -> str:
    """Insert one org row, return its UUID as a string."""
    row = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values (:n, :s) returning id::text"
            ),
            {"n": name, "s": f"{name.lower()}-{secrets.token_hex(4)}"},
        )
    ).mappings().one()
    return row["id"]


async def _make_group(db: AsyncSession, *, org_id: str, name: str) -> str:
    """Insert one folder row (schema-owner — no RLS), return its id."""
    row = (
        await db.execute(
            text(
                "insert into public.engagement_groups (name, org_id) "
                "values (:n, cast(:o as uuid)) returning id::text"
            ),
            {"n": name, "o": org_id},
        )
    ).mappings().one()
    return row["id"]


async def _fetch_audit_actions(
    db: AsyncSession, *, org_id: str
) -> list[str]:
    result = await db.execute(
        text(
            "select action from public.audit_logs "
            "where org_id = cast(:o as uuid) order by created_at desc, id desc"
        ),
        {"o": org_id},
    )
    return [r[0] for r in result.all()]


# ── HTTP: group CRUD ───────────────────────────────────────────────────────


async def test_create_group_returns_row(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.post("/api/admin/groups", json={"name": "Q3 deals"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Q3 deals"
    assert body["id"]
    assert "created_at" in body


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({}, 422),
        ({"name": ""}, 422),
        ({"name": "x" * 201}, 422),
    ],
)
async def test_create_group_validation(
    admin_authed: AsyncClient, payload: dict, expected: int
) -> None:
    r = await admin_authed.post("/api/admin/groups", json=payload)
    assert r.status_code == expected


async def test_list_groups_includes_client_count(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    created = (
        await admin_authed.post("/api/admin/groups", json={"name": "Folder A"})
    ).json()
    # Empty folder shows up with count 0.
    listed = (await admin_authed.get("/api/admin/groups")).json()
    assert any(g["id"] == created["id"] and g["client_count"] == 0 for g in listed)

    # Move the seeded engagement in → count becomes 1.
    await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}",
        json={"group_id": created["id"]},
    )
    listed = (await admin_authed.get("/api/admin/groups")).json()
    assert any(g["id"] == created["id"] and g["client_count"] == 1 for g in listed)


async def test_list_groups_ordered_by_name(
    admin_authed: AsyncClient,
) -> None:
    for name in ("Zebra", "Alpha", "Mango"):
        await admin_authed.post("/api/admin/groups", json={"name": name})
    names = [g["name"] for g in (await admin_authed.get("/api/admin/groups")).json()]
    assert names == sorted(names)


async def test_rename_group(admin_authed: AsyncClient) -> None:
    created = (
        await admin_authed.post("/api/admin/groups", json={"name": "Old"})
    ).json()
    r = await admin_authed.patch(
        f"/api/admin/groups/{created['id']}", json={"name": "New"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "New"


async def test_rename_group_unknown_id_returns_404(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.patch(
        f"/api/admin/groups/{uuid.uuid4()}", json={"name": "x"}
    )
    assert r.status_code == 404


async def test_delete_group(admin_authed: AsyncClient) -> None:
    created = (
        await admin_authed.post("/api/admin/groups", json={"name": "Trash"})
    ).json()
    r = await admin_authed.delete(f"/api/admin/groups/{created['id']}")
    assert r.status_code == 204
    listed = (await admin_authed.get("/api/admin/groups")).json()
    assert all(g["id"] != created["id"] for g in listed)


async def test_delete_group_unknown_id_returns_404(
    admin_authed: AsyncClient,
) -> None:
    r = await admin_authed.delete(f"/api/admin/groups/{uuid.uuid4()}")
    assert r.status_code == 404


# ── HTTP: deleting a folder ungroups its engagements (never deletes) ───────


async def test_delete_group_ungroups_engagements(
    admin_authed: AsyncClient, seed_client: dict[str, str], db: AsyncSession
) -> None:
    created = (
        await admin_authed.post("/api/admin/groups", json={"name": "Bucket"})
    ).json()
    await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}",
        json={"group_id": created["id"]},
    )
    # Sanity: the engagement is in the folder.
    moved = (
        await admin_authed.get(f"/api/admin/clients/{seed_client['id']}")
    ).json()["client"]
    assert moved["group_id"] == created["id"]

    r = await admin_authed.delete(f"/api/admin/groups/{created['id']}")
    assert r.status_code == 204

    # The engagement still exists and is now ungrouped (group_id null).
    after = (
        await admin_authed.get(f"/api/admin/clients/{seed_client['id']}")
    ).json()["client"]
    assert after["group_id"] is None
    # Row still present in the DB — only the folder was removed.
    still_there = (
        await db.execute(
            text(
                "select group_id from public.clients "
                "where id = cast(:c as uuid)"
            ),
            {"c": seed_client["id"]},
        )
    ).mappings().one_or_none()
    assert still_there is not None
    assert still_there["group_id"] is None


# ── HTTP: PATCH client {group_id} moves + ungroups ─────────────────────────


async def test_patch_client_moves_into_folder(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    g = (
        await admin_authed.post("/api/admin/groups", json={"name": "Dest"})
    ).json()
    r = await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}", json={"group_id": g["id"]}
    )
    assert r.status_code == 200
    assert r.json()["group_id"] == g["id"]
    # The list projection carries the folder name too.
    listed = (await admin_authed.get("/api/admin/clients")).json()
    row = next(c for c in listed if c["id"] == seed_client["id"])
    assert row["group_id"] == g["id"]
    assert row["group_name"] == "Dest"


async def test_patch_client_ungroup_with_null(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    g = (
        await admin_authed.post("/api/admin/groups", json={"name": "Dest"})
    ).json()
    await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}", json={"group_id": g["id"]}
    )
    r = await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}", json={"group_id": None}
    )
    assert r.status_code == 200
    assert r.json()["group_id"] is None


async def test_patch_client_unknown_folder_returns_404(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    r = await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}",
        json={"group_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404


async def test_patch_client_omitting_group_id_leaves_it_untouched(
    admin_authed: AsyncClient, seed_client: dict[str, str]
) -> None:
    g = (
        await admin_authed.post("/api/admin/groups", json={"name": "Keep"})
    ).json()
    await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}", json={"group_id": g["id"]}
    )
    # A patch that omits group_id must not null the column.
    r = await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}", json={"brief": "hi"}
    )
    assert r.status_code == 200
    assert r.json()["group_id"] == g["id"]


# ── HTTP: cross-org move is rejected (foreign folder id → 404) ─────────────


async def test_patch_client_into_foreign_org_folder_rejected(
    admin_authed: AsyncClient,
    seed_client: dict[str, str],
    db: AsyncSession,
) -> None:
    """A folder belonging to another org is invisible (RLS) → 404, and the
    engagement is NOT silently ungrouped."""
    acme_id = await _make_org(db, "Acme")
    foreign_group = await _make_group(db, org_id=acme_id, name="Acme folder")

    r = await admin_authed.patch(
        f"/api/admin/clients/{seed_client['id']}",
        json={"group_id": foreign_group},
    )
    assert r.status_code == 404
    # Untouched.
    after = (
        await admin_authed.get(f"/api/admin/clients/{seed_client['id']}")
    ).json()["client"]
    assert after["group_id"] is None


# ── HTTP: audit rows ───────────────────────────────────────────────────────


async def test_group_mutations_write_audit_rows(
    admin_authed: AsyncClient,
    seed_admin_user: dict[str, str],
    db: AsyncSession,
) -> None:
    created = (
        await admin_authed.post("/api/admin/groups", json={"name": "Auditme"})
    ).json()
    await admin_authed.patch(
        f"/api/admin/groups/{created['id']}", json={"name": "Renamed"}
    )
    await admin_authed.delete(f"/api/admin/groups/{created['id']}")

    actions = await _fetch_audit_actions(db, org_id=seed_admin_user["org_id"])
    assert "group.create" in actions
    assert "group.update" in actions
    assert "group.delete" in actions


# ── RLS isolation: org A can't see / rename / delete org B's folder ────────


async def test_member_cannot_see_other_orgs_group(
    db: AsyncSession,
    db_conn: AsyncConnection,
    axiolo_org: dict[str, str],
) -> None:
    acme_id = await _make_org(db, "Acme")
    axiolo_group = await _make_group(
        db, org_id=axiolo_org["id"], name="Axiolo folder"
    )
    acme_group = await _make_group(db, org_id=acme_id, name="Acme folder")
    await db.flush()

    # Become a member of Axiolo — only Axiolo's folder is visible.
    await become_member(db_conn, org_id=axiolo_org["id"])
    visible = (
        await db_conn.execute(
            text("select id::text from public.engagement_groups")
        )
    ).scalars().all()
    assert axiolo_group in visible
    assert acme_group not in visible


async def test_member_cannot_rename_other_orgs_group(
    db: AsyncSession,
    db_conn: AsyncConnection,
    axiolo_org: dict[str, str],
) -> None:
    acme_id = await _make_org(db, "Acme")
    acme_group = await _make_group(db, org_id=acme_id, name="Acme folder")
    await db.flush()

    await become_member(db_conn, org_id=axiolo_org["id"])
    # RLS hides the row, so the UPDATE matches nothing.
    result = await db_conn.execute(
        text(
            "update public.engagement_groups set name = 'hacked' "
            "where id = cast(:g as uuid)"
        ),
        {"g": acme_group},
    )
    assert result.rowcount == 0


async def test_member_cannot_delete_other_orgs_group(
    db: AsyncSession,
    db_conn: AsyncConnection,
    axiolo_org: dict[str, str],
) -> None:
    acme_id = await _make_org(db, "Acme")
    acme_group = await _make_group(db, org_id=acme_id, name="Acme folder")
    await db.flush()

    await become_member(db_conn, org_id=axiolo_org["id"])
    result = await db_conn.execute(
        text(
            "delete from public.engagement_groups "
            "where id = cast(:g as uuid)"
        ),
        {"g": acme_group},
    )
    assert result.rowcount == 0


async def test_member_cannot_insert_group_for_other_org(
    db: AsyncSession,
    db_conn: AsyncConnection,
    axiolo_org: dict[str, str],
) -> None:
    acme_id = await _make_org(db, "Acme")
    await db.flush()

    await become_member(db_conn, org_id=axiolo_org["id"])
    # WITH CHECK refuses an org_id that isn't the active one.
    with pytest.raises(DBAPIError):
        await db_conn.execute(
            text(
                "insert into public.engagement_groups (name, org_id) "
                "values ('sneaky', cast(:o as uuid))"
            ),
            {"o": acme_id},
        )
