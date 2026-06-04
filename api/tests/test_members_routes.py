"""Tests for ``/api/orgs/me/members``.

The "at least one owner" invariant is the load-bearing rule across this
surface. Parametrized over ``(actor_role, target_role, action,
expected_status)`` to keep the matrix obvious.
"""
from __future__ import annotations

import secrets
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth.password import hash_password
from pulse_api.auth.session import encode_session
from pulse_api.config import settings


async def _seed_user(
    db: AsyncSession,
    *,
    email: str,
    org_id: str,
    role: str,
    last_active: bool = True,
) -> str:
    """Insert a verified user with a membership in ``org_id``."""
    user_id = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, last_active_org_id, "
                " email_verified_at) "
                "values (:e, :h, :n, "
                "        case when :a then cast(:o as uuid) else null end, "
                "        now()) returning id::text"
            ),
            {
                "e": email,
                "h": hash_password("a-good-password-here"),
                "n": email.split("@")[0],
                "o": org_id,
                "a": last_active,
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


def _set_cookie(client: AsyncClient, *, user_id: str, org_id: str) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        encode_session(user_id, org_id),
    )


# ── GET /api/orgs/me/members ─────────────────────────────────────────────


async def test_list_members_visible_to_any_member(
    client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
) -> None:
    """A member-role caller can list members (read is unrestricted to
    anyone inside the org)."""
    owner_id = await _seed_user(
        db,
        email=f"owner-{secrets.token_hex(3)}@example.com",
        org_id=axiolo_org["id"],
        role="owner",
    )
    member_id = await _seed_user(
        db,
        email=f"member-{secrets.token_hex(3)}@example.com",
        org_id=axiolo_org["id"],
        role="member",
    )
    await db.flush()

    _set_cookie(client, user_id=member_id, org_id=axiolo_org["id"])
    r = await client.get("/api/orgs/me/members")
    assert r.status_code == 200, r.text
    user_ids = {row["user_id"] for row in r.json()}
    assert {owner_id, member_id}.issubset(user_ids)


# ── PATCH /api/orgs/me/members/{user_id} ─────────────────────────────────


@pytest.mark.parametrize(
    "actor_role, target_role, new_role, n_owners_before, expected_status",
    [
        # Owner promotes a member → 200.
        ("owner", "member", "owner", 1, 200),
        # Owner demotes another owner when 2 owners exist → 200.
        ("owner", "owner", "member", 2, 200),
        # Owner demotes themself while sole owner → 409.
        ("owner", "self_owner", "member", 1, 409),
        # Member tries to promote themselves → 403.
        ("member", "self_member", "owner", 1, 403),
    ],
    ids=[
        "owner-promotes-member",
        "owner-demotes-other-owner-when-two-owners",
        "owner-self-demotes-as-sole-owner-409",
        "member-self-promotion-403",
    ],
)
async def test_patch_member_role_matrix(
    client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
    actor_role: str,
    target_role: str,
    new_role: str,
    n_owners_before: int,
    expected_status: int,
) -> None:
    """``PATCH /api/orgs/me/members/{user_id}`` matrix."""
    org_id = axiolo_org["id"]
    actor_id = await _seed_user(
        db,
        email=f"actor-{secrets.token_hex(3)}@example.com",
        org_id=org_id,
        role=actor_role,
    )
    # Build target. "self_*" cases reuse the actor.
    if target_role.startswith("self_"):
        target_id = actor_id
    else:
        target_id = await _seed_user(
            db,
            email=f"target-{secrets.token_hex(3)}@example.com",
            org_id=org_id,
            role=target_role,
        )
    # Fill in extra owners if the case demands it.
    if n_owners_before > 1 and actor_role == "owner" and target_role == "owner":
        # Already have 2 owners (actor + target). No fill needed.
        pass
    elif n_owners_before > 1 and actor_role == "owner":
        for _ in range(n_owners_before - 1):
            await _seed_user(
                db,
                email=f"extra-owner-{secrets.token_hex(3)}@example.com",
                org_id=org_id,
                role="owner",
            )
    await db.flush()

    _set_cookie(client, user_id=actor_id, org_id=org_id)
    r = await client.patch(
        f"/api/orgs/me/members/{target_id}",
        json={"role": new_role},
    )
    assert r.status_code == expected_status, (
        f"actor={actor_role} target={target_role} new={new_role}: {r.text}"
    )

    if r.status_code == 200:
        # Verify the persisted role flipped.
        row = (
            await db.execute(
                text(
                    "select role from public.organization_memberships "
                    "where org_id = cast(:o as uuid) "
                    "  and user_id = cast(:u as uuid)"
                ),
                {"o": org_id, "u": target_id},
            )
        ).mappings().one()
        assert row["role"] == new_role


async def test_patch_member_role_unknown_user_404(
    client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
) -> None:
    """Unknown ``user_id`` → 404, not 403, since the caller IS an owner."""
    org_id = axiolo_org["id"]
    actor_id = await _seed_user(
        db,
        email=f"actor-{secrets.token_hex(3)}@example.com",
        org_id=org_id,
        role="owner",
    )
    await db.flush()
    _set_cookie(client, user_id=actor_id, org_id=org_id)

    r = await client.patch(
        f"/api/orgs/me/members/{uuid.uuid4()}", json={"role": "member"}
    )
    assert r.status_code == 404


# ── DELETE /api/orgs/me/members/{user_id} ────────────────────────────────


@pytest.mark.parametrize(
    "actor_role, target_role, n_owners_before, expected_status, target_is_self",
    [
        # Owner removes a member → 204.
        ("owner", "member", 1, 204, False),
        # Owner removes another owner when there are 2 owners → 204.
        ("owner", "owner", 2, 204, False),
        # Owner removes themself when they are the sole owner → 409.
        ("owner", "owner", 1, 409, True),
        # Member tries to remove anyone → 403.
        ("member", "member", 1, 403, False),
        # Owner removes themself when there are 2 owners → 204 (delegated).
        ("owner", "owner", 2, 204, True),
    ],
    ids=[
        "owner-removes-member",
        "owner-removes-other-owner-when-two-owners",
        "owner-self-remove-as-sole-owner-409",
        "member-cannot-remove-anyone-403",
        "owner-self-remove-when-two-owners-204",
    ],
)
async def test_delete_member_matrix(
    client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
    actor_role: str,
    target_role: str,
    n_owners_before: int,
    expected_status: int,
    target_is_self: bool,
) -> None:
    """``DELETE /api/orgs/me/members/{user_id}`` matrix."""
    org_id = axiolo_org["id"]
    actor_id = await _seed_user(
        db,
        email=f"actor-{secrets.token_hex(3)}@example.com",
        org_id=org_id,
        role=actor_role,
    )
    if target_is_self:
        target_id = actor_id
    else:
        target_id = await _seed_user(
            db,
            email=f"target-{secrets.token_hex(3)}@example.com",
            org_id=org_id,
            role=target_role,
        )
    # Top up extra owners if needed.
    current_owners = (1 if actor_role == "owner" else 0) + (
        1
        if (not target_is_self and target_role == "owner")
        else 0
    )
    while current_owners < n_owners_before:
        await _seed_user(
            db,
            email=f"extra-{secrets.token_hex(3)}@example.com",
            org_id=org_id,
            role="owner",
        )
        current_owners += 1
    await db.flush()

    _set_cookie(client, user_id=actor_id, org_id=org_id)
    r = await client.delete(f"/api/orgs/me/members/{target_id}")
    assert r.status_code == expected_status, (
        f"actor={actor_role} target={target_role} self={target_is_self}: {r.text}"
    )

    if r.status_code == 204:
        # Membership row gone.
        row = (
            await db.execute(
                text(
                    "select 1 from public.organization_memberships "
                    "where org_id = cast(:o as uuid) "
                    "  and user_id = cast(:u as uuid)"
                ),
                {"o": org_id, "u": target_id},
            )
        ).scalar()
        assert row is None


async def test_demote_concurrent_last_owners(
    engine,
    axiolo_org: dict[str, str],
) -> None:
    """Concurrent demotion of two owners against a 2-owner org leaves
    at least one owner standing.

    Before the ``lock_owners`` fix, two transactions targeting
    different owner rows could both pass the ``count_owners() == 2``
    check and demote both — leaving the org with zero owners. With
    ``SELECT … FOR UPDATE`` over the owner set, the second
    transaction blocks until the first commits, then re-reads the
    count and 409s.

    Drives the repo helpers directly across two independent
    connections so the lock actually engages. The shared-connection
    test fixture used elsewhere can't model this — FOR UPDATE within a
    single transaction is a no-op.
    """
    import asyncio
    import uuid as _uuid

    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from pulse_api.repos import memberships as memberships_repo

    org_id = axiolo_org["id"]

    # Seed two owners on a short-lived connection that commits — the
    # rows must be visible to both concurrent transactions below.
    factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=_AsyncSession
    )
    actor_id = str(_uuid.uuid4())
    other_id = str(_uuid.uuid4())
    actor_email = f"toctoc-actor-{secrets.token_hex(3)}@example.com"
    other_email = f"toctoc-other-{secrets.token_hex(3)}@example.com"
    async with factory() as setup:
        await setup.execute(
            text(
                "insert into public.users "
                "(id, email, password_hash, name, "
                " last_active_org_id, email_verified_at) "
                "values (cast(:i as uuid), :e, :h, :n, "
                "        cast(:o as uuid), now())"
            ),
            {
                "i": actor_id,
                "e": actor_email,
                "h": hash_password("a-good-password-here"),
                "n": "actor",
                "o": org_id,
            },
        )
        await setup.execute(
            text(
                "insert into public.users "
                "(id, email, password_hash, name, "
                " last_active_org_id, email_verified_at) "
                "values (cast(:i as uuid), :e, :h, :n, "
                "        cast(:o as uuid), now())"
            ),
            {
                "i": other_id,
                "e": other_email,
                "h": hash_password("a-good-password-here"),
                "n": "other",
                "o": org_id,
            },
        )
        await setup.execute(
            text(
                "insert into public.organization_memberships "
                "(org_id, user_id, role) "
                "values "
                "(cast(:o as uuid), cast(:a as uuid), 'owner'), "
                "(cast(:o as uuid), cast(:b as uuid), 'owner')"
            ),
            {"o": org_id, "a": actor_id, "b": other_id},
        )
        await setup.commit()

    async def _demote(target_id: str) -> bool:
        """Mimic the route's demote sequence on a private connection."""
        async with factory() as session:
            await memberships_repo.lock_owners(session, org_id)
            owners = await memberships_repo.count_owners(session, org_id)
            if owners <= 1:
                await session.rollback()
                return False
            await memberships_repo.update_role(
                session,
                org_id=org_id,
                user_id=target_id,
                role="member",
            )
            await session.commit()
            return True

    try:
        results = await asyncio.gather(
            _demote(actor_id), _demote(other_id)
        )
    finally:
        # Clean up — these rows live outside the test-isolation transaction.
        async with factory() as teardown:
            await teardown.execute(
                text(
                    "delete from public.organization_memberships "
                    "where user_id = any(cast(:ids as uuid[]))"
                ),
                {"ids": [actor_id, other_id]},
            )
            await teardown.execute(
                text(
                    "delete from public.users "
                    "where id = any(cast(:ids as uuid[]))"
                ),
                {"ids": [actor_id, other_id]},
            )
            await teardown.commit()

    # Exactly one succeeded, exactly one was blocked by the invariant.
    successes = sum(1 for r in results if r)
    assert successes == 1, (
        f"both demotions succeeded under FOR UPDATE: results={results}"
    )


async def test_delete_member_clears_last_active_org_if_match(
    client: AsyncClient,
    db: AsyncSession,
    axiolo_org: dict[str, str],
) -> None:
    """Removing a user clears their last_active_org_id pointer."""
    org_id = axiolo_org["id"]
    actor_id = await _seed_user(
        db,
        email=f"owner-{secrets.token_hex(3)}@example.com",
        org_id=org_id,
        role="owner",
    )
    target_id = await _seed_user(
        db,
        email=f"target-{secrets.token_hex(3)}@example.com",
        org_id=org_id,
        role="member",
        last_active=True,  # points at this org
    )
    await db.flush()
    _set_cookie(client, user_id=actor_id, org_id=org_id)

    r = await client.delete(f"/api/orgs/me/members/{target_id}")
    assert r.status_code == 204

    pointer = (
        await db.execute(
            text(
                "select last_active_org_id from public.users "
                "where id = cast(:u as uuid)"
            ),
            {"u": target_id},
        )
    ).scalar()
    assert pointer is None
