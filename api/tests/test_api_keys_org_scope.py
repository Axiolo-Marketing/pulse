"""API key org-scope tests.

After PR 2 each API key carries an ``org_id``. The auth gate flips the
session into a ``pulse_member`` connection with ``pulse.org_id`` set to
the key's org, so the key's data visibility matches what an owner of
that org would see in a browser session.

Parametrized matrix over ``(key_org, request_org_data, expected)``
plus the create/revoke lifecycle checks the plan calls out.
"""
from __future__ import annotations

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.api_keys import generate_key, hash_key, prefix_of
from pulse_api.auth.session import encode_session
from pulse_api.config import settings


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


async def _insert_key(
    db: AsyncSession,
    *,
    user_id: str,
    org_id: str,
    revoked: bool = False,
) -> tuple[str, str]:
    raw = generate_key()
    row = (
        await db.execute(
            text(
                "insert into public.api_keys "
                "(user_id, org_id, prefix, key_hash, label, revoked_at) "
                "values (cast(:u as uuid), cast(:o as uuid), :p, :h, :l, "
                "        case when :r then now() else null end) "
                "returning id::text"
            ),
            {
                "u": user_id,
                "o": org_id,
                "p": prefix_of(raw),
                "h": hash_key(raw),
                "l": "org-scope test",
                "r": revoked,
            },
        )
    ).mappings().one()
    return raw, row["id"]


async def _seed_client(db: AsyncSession, *, org_id: str, name: str) -> str:
    row = (
        await db.execute(
            text(
                "insert into public.engagements (name, token, org_id) "
                "values (:n, :t, cast(:o as uuid)) returning id::text"
            ),
            {"n": name, "t": secrets.token_hex(8), "o": org_id},
        )
    ).mappings().one()
    return row["id"]


# ── Cross-org isolation: a key for org A reads only org A's data ─────────


@pytest.mark.parametrize(
    "key_org_label, request_status, visible_client_count",
    [
        # Key for org A → endpoint scoped to A → returns only A's clients.
        ("axiolo", 200, "axiolo_only"),
    ],
    ids=["bearer-key-sees-only-own-org-clients"],
)
async def test_bearer_key_scoped_to_key_org(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    key_org_label: str,
    request_status: int,
    visible_client_count: str,
) -> None:
    """A bearer key for org A reads only org A's clients, never B's."""
    acme_id = await _make_org(db, "Acme")
    # The seed_admin_user is an owner of Axiolo. Add them as an owner of
    # Acme too so the auth gate doesn't 403 on the cross-org probe.
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), 'owner')"
        ),
        {"o": acme_id, "u": seed_admin_user["id"]},
    )
    # Seed a client in each org.
    axiolo_client_id = await _seed_client(
        db, org_id=seed_admin_user["org_id"], name="Axiolo-Client"
    )
    acme_client_id = await _seed_client(
        db, org_id=acme_id, name="Acme-Client"
    )
    await db.flush()

    # Key scoped to Axiolo.
    raw, _ = await _insert_key(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    r = await client.get(
        "/api/admin/engagements", headers={"Authorization": f"Bearer {raw}"}
    )
    assert r.status_code == request_status
    ids = {c["id"] for c in r.json()}
    assert axiolo_client_id in ids
    assert acme_client_id not in ids, (
        "Axiolo-scoped key leaked an Acme client row"
    )


# ── Revoke lifecycle ─────────────────────────────────────────────────────


async def test_revoking_key_immediately_blocks_subsequent_auth(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """Hard-revoke: the key stops authenticating on the next request."""
    raw, key_id = await _insert_key(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    ok = await client.get(
        "/api/admin/engagements", headers={"Authorization": f"Bearer {raw}"}
    )
    assert ok.status_code == 200

    # Revoke via the management endpoint (session cookie path).
    client.cookies.set(
        settings.session_cookie_name,
        encode_session(seed_admin_user["id"], seed_admin_user["org_id"]),
    )
    revoked = await client.delete(f"/api/auth/me/api-keys/{key_id}")
    assert revoked.status_code == 204
    # Drop the cookie so the next call is bearer-only.
    client.cookies.delete(settings.session_cookie_name)

    blocked = await client.get(
        "/api/admin/engagements", headers={"Authorization": f"Bearer {raw}"}
    )
    assert blocked.status_code == 401


# ── Create-key-for-foreign-org gate ──────────────────────────────────────


async def test_create_key_for_foreign_org_returns_403(
    client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """A user who isn't a member of the target org can't mint a key for it."""
    # Seed an unrelated org. The admin user is NOT a member of it.
    acme_id = await _make_org(db, "Acme")
    await db.flush()

    client.cookies.set(
        settings.session_cookie_name,
        encode_session(seed_admin_user["id"], seed_admin_user["org_id"]),
    )
    r = await client.post(
        "/api/auth/me/api-keys",
        json={"label": "rogue", "org_id": acme_id},
    )
    assert r.status_code == 403
    assert "member" in r.json()["detail"].lower()
