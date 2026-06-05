"""Multi-tenant isolation tests (PR 1 — schema + RLS backstop).

Sibling to ``test_rls_isolation.py``. Where that suite proves the
client-token RLS backstop (a ``pulse_anon`` session bound to one token
cannot see another token's rows), this suite proves the organization
RLS backstop: a ``pulse_member`` session bound to one ``org_id`` cannot
read, insert, update, or delete rows belonging to another org.

Pattern (mirrors ``test_rls_isolation.py``):

1. Seed two orgs (Axiolo via fixture + Acme inserted here) with one row
   in every org-scoped table while still connected as the schema owner
   (RLS does not apply).
2. Call ``become_member(conn, org_id=...)`` to flip the open transaction
   onto ``pulse_member`` with the right ``pulse.org_id`` GUC.
3. Probe with raw SQL and assert what is visible / writable.

The role switch is bound to the outer test transaction; the rollback at
teardown reverts every seed row and the role switch.
"""
from __future__ import annotations

import secrets
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tests.conftest import become_member


# Tables protected by an org-scoped pulse_member RLS policy (migration 0004).
ORG_SCOPED_TABLES = [
    "organizations",
    "clients",
    "cards",
    "responses",
    "uploads",
    "api_keys",
    "audit_logs",
    "organization_memberships",
    "organization_invites",
]


# ─── seed helpers ─────────────────────────────────────────────────────────


async def _create_org(db: AsyncSession, *, name: str, slug: str) -> str:
    """Insert one org row and return its id as a string."""
    row = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug) "
                "values (:n, :s) returning id::text"
            ),
            {"n": name, "s": slug},
        )
    ).mappings().one()
    return row["id"]


async def _seed_one_set(
    db: AsyncSession, *, org_id: str, label: str
) -> dict[str, str]:
    """Insert one row in every org-scoped table for the given org.

    Returns a mapping from table-name → id (string). Every row is tagged
    with ``org_id``; FKs (cards→clients, responses→cards, etc.) are wired
    so a real production query would resolve them cleanly.

    Run as the schema owner (no RLS); the seeded rows become visible to
    the post-role-switch queries thanks to single-transaction semantics.
    """
    ids: dict[str, str] = {}

    # client
    client_token = secrets.token_hex(8)
    ids["clients"] = (
        await db.execute(
            text(
                "insert into public.clients (name, token, org_id) "
                "values (:n, :t, cast(:o as uuid)) returning id::text"
            ),
            {"n": f"{label}-client", "t": client_token, "o": org_id},
        )
    ).mappings().one()["id"]

    # card
    ids["cards"] = (
        await db.execute(
            text(
                "insert into public.cards "
                "(client_id, order_index, category, title, context, "
                " question, response_type, org_id) "
                "values (cast(:cid as uuid), 1, 'Test', :t, 'ctx', 'q?', "
                "        'short-text', cast(:o as uuid)) "
                "returning id::text"
            ),
            {"cid": ids["clients"], "t": f"{label}-card", "o": org_id},
        )
    ).mappings().one()["id"]

    # response
    ids["responses"] = (
        await db.execute(
            text(
                "insert into public.responses "
                "(card_id, client_id, state, org_id) "
                "values (cast(:card as uuid), cast(:cid as uuid), "
                "        'answered', cast(:o as uuid)) "
                "returning id::text"
            ),
            {"card": ids["cards"], "cid": ids["clients"], "o": org_id},
        )
    ).mappings().one()["id"]

    # upload
    ids["uploads"] = (
        await db.execute(
            text(
                "insert into public.uploads "
                "(card_id, client_id, file_name, file_size_bytes, "
                " storage_path, org_id) "
                "values (cast(:card as uuid), cast(:cid as uuid), :fn, "
                "        100, :sp, cast(:o as uuid)) "
                "returning id::text"
            ),
            {
                "card": ids["cards"],
                "cid": ids["clients"],
                "fn": f"{label}.pdf",
                "sp": f"{label}/x/y",
                "o": org_id,
            },
        )
    ).mappings().one()["id"]

    # user + organization_membership
    user_id = (
        await db.execute(
            text(
                "insert into public.users "
                "(email, password_hash, name, is_admin, email_verified_at) "
                "values (:e, 'x', :n, true, now()) returning id::text"
            ),
            {"e": f"{label}@example.com", "n": f"{label} User"},
        )
    ).mappings().one()["id"]
    ids["users"] = user_id
    ids["organization_memberships"] = (
        await db.execute(
            text(
                "insert into public.organization_memberships "
                "(org_id, user_id, role) "
                "values (cast(:o as uuid), cast(:u as uuid), 'owner') "
                "returning id::text"
            ),
            {"o": org_id, "u": user_id},
        )
    ).mappings().one()["id"]

    # organization_invite (pending; unique token_hash)
    ids["organization_invites"] = (
        await db.execute(
            text(
                "insert into public.organization_invites "
                "(org_id, email, role, token_hash, expires_at) "
                "values (cast(:o as uuid), :e, 'member', :h, "
                "        now() + interval '7 days') "
                "returning id::text"
            ),
            {
                "o": org_id,
                "e": f"invitee-{label}@example.com",
                "h": f"invite-hash-{label}-{secrets.token_hex(4)}",
            },
        )
    ).mappings().one()["id"]

    # audit_log
    ids["audit_logs"] = (
        await db.execute(
            text(
                "insert into public.audit_logs "
                "(org_id, user_id, action, target_type, target_id) "
                "values (cast(:o as uuid), cast(:u as uuid), 'test', "
                "        'client', :tid) "
                "returning id::text"
            ),
            {"o": org_id, "u": user_id, "tid": ids["clients"]},
        )
    ).mappings().one()["id"]

    # api_key (unique 8-char prefix)
    ids["api_keys"] = (
        await db.execute(
            text(
                "insert into public.api_keys "
                "(user_id, prefix, key_hash, label, org_id) "
                "values (cast(:u as uuid), :p, :h, :l, cast(:o as uuid)) "
                "returning id::text"
            ),
            {
                "u": user_id,
                "p": secrets.token_hex(4),  # 8 hex chars
                "h": f"hash-{label}-{secrets.token_hex(4)}",
                "l": f"{label} key",
                "o": org_id,
            },
        )
    ).mappings().one()["id"]

    return ids


async def _seed_two_orgs(
    db: AsyncSession, axiolo_org_id: str
) -> dict[str, dict[str, str]]:
    """Seed one row per org-scoped table for both Axiolo and Acme.

    The Axiolo org already exists (migration 0004); Acme is inserted
    here. Returns ``{"a": {table: id, ..., "org_id": ...}, "b": {...}}``
    where org "a" is Axiolo and org "b" is Acme.
    """
    acme_org_id = await _create_org(db, name="Acme", slug="acme-test")
    a = await _seed_one_set(db, org_id=axiolo_org_id, label="renee")
    b = await _seed_one_set(db, org_id=acme_org_id, label="josh")
    # The org's own id is what becomes visible through the organizations RLS
    # policy — record it so the parametrized read-isolation test covers
    # the most sensitive table in the hierarchy.
    a["organizations"] = axiolo_org_id
    b["organizations"] = acme_org_id
    a["org_id"] = axiolo_org_id
    b["org_id"] = acme_org_id
    return {"a": a, "b": b}


# ─── 1. Read isolation ────────────────────────────────────────────────────


@pytest.mark.parametrize("table", ORG_SCOPED_TABLES)
async def test_member_sees_only_own_org_rows(
    db: AsyncSession,
    db_conn: AsyncConnection,
    axiolo_org: dict[str, str],
    table: str,
) -> None:
    """A ``pulse_member`` bound to org A sees A's row and not B's.

    Parametrized across every org-scoped table; one function replaces
    eight near-identical hand-written cases.
    """
    seeded = await _seed_two_orgs(db, axiolo_org["id"])
    await db.flush()  # make sure seeds are visible after role flip

    await become_member(db_conn, org_id=seeded["a"]["org_id"])

    # Exactly one row visible — the seed for org A.
    count = (
        await db_conn.execute(text(f"select count(*) from public.{table}"))
    ).scalar()
    assert count == 1, (
        f"expected 1 visible row in {table} for org A; got {count}"
    )

    # And it's the org-A row, not org-B's.
    visible_ids = [
        str(r[0])
        for r in (
            await db_conn.execute(text(f"select id from public.{table}"))
        ).all()
    ]
    assert visible_ids == [seeded["a"][table]], (
        f"{table} leaked the wrong row: got {visible_ids}, "
        f"expected only {seeded['a'][table]}"
    )


# ─── 2. Empty GUC = no rows ───────────────────────────────────────────────


@pytest.mark.parametrize("table", ORG_SCOPED_TABLES)
async def test_member_with_empty_org_id_sees_nothing(
    db: AsyncSession,
    db_conn: AsyncConnection,
    axiolo_org: dict[str, str],
    table: str,
) -> None:
    """A ``pulse_member`` with ``pulse.org_id = ''`` (empty) sees zero rows.

    ``pulse_request_org_id()`` returns NULL for the empty string, and
    ``org_id = NULL`` is never true — RLS filters every row.
    """
    await _seed_two_orgs(db, axiolo_org["id"])
    await db.flush()

    await become_member(db_conn, org_id="")

    count = (
        await db_conn.execute(text(f"select count(*) from public.{table}"))
    ).scalar()
    assert count == 0, (
        f"pulse_member with empty pulse.org_id saw rows in {table}"
    )


# ─── 3. Write isolation (insert / update / delete) ───────────────────────


@pytest.mark.parametrize("op_name", ["insert", "update", "delete"])
async def test_member_cannot_write_to_other_org(
    db: AsyncSession,
    db_conn: AsyncConnection,
    axiolo_org: dict[str, str],
    op_name: str,
) -> None:
    """A ``pulse_member`` cannot write across the org boundary.

    Three cases, parametrized:
      - ``insert``: INSERT into ``audit_logs`` tagged with the other org's
        id. RLS WITH CHECK rejects → DBAPIError.
      - ``update``: UPDATE ``clients`` on the other org's row. RLS USING
        filter hides the row from the UPDATE → 0 rows affected.
      - ``delete``: DELETE ``clients`` on the other org's row. Same
        mechanism → 0 rows affected.
    """
    seeded = await _seed_two_orgs(db, axiolo_org["id"])
    await db.flush()

    org_a_id = seeded["a"]["org_id"]
    org_b_id = seeded["b"]["org_id"]
    other_client_id = seeded["b"]["clients"]
    user_id = seeded["a"]["users"]

    await become_member(db_conn, org_id=org_a_id)

    if op_name == "insert":
        # Cross-org INSERT must be rejected by the WITH CHECK clause.
        with pytest.raises(DBAPIError):
            await db_conn.execute(
                text(
                    "insert into public.audit_logs "
                    "(org_id, user_id, action) "
                    "values (cast(:o as uuid), cast(:u as uuid), 'leak')"
                ),
                {"o": org_b_id, "u": user_id},
            )
    elif op_name == "update":
        # Cross-org UPDATE: the row is invisible to the member, so the
        # UPDATE matches zero rows. No exception, just no-op.
        result = await db_conn.execute(
            text(
                "update public.clients set name = 'leak' "
                "where id = cast(:cid as uuid)"
            ),
            {"cid": other_client_id},
        )
        assert result.rowcount == 0, (
            "pulse_member updated a row belonging to another org "
            f"(rowcount={result.rowcount})"
        )

        # Confirm the row is unchanged (verify as owner after RESET ROLE).
        await db_conn.execute(text("reset role"))
        name = (
            await db_conn.execute(
                text(
                    "select name from public.clients "
                    "where id = cast(:cid as uuid)"
                ),
                {"cid": other_client_id},
            )
        ).scalar()
        assert name != "leak", "cross-org UPDATE silently succeeded"
    elif op_name == "delete":
        # Cross-org DELETE: same row-invisibility mechanism.
        result = await db_conn.execute(
            text(
                "delete from public.clients "
                "where id = cast(:cid as uuid)"
            ),
            {"cid": other_client_id},
        )
        assert result.rowcount == 0, (
            "pulse_member deleted a row belonging to another org "
            f"(rowcount={result.rowcount})"
        )

        # Confirm the row still exists (verify as owner).
        await db_conn.execute(text("reset role"))
        still_there = (
            await db_conn.execute(
                text(
                    "select count(*) from public.clients "
                    "where id = cast(:cid as uuid)"
                ),
                {"cid": other_client_id},
            )
        ).scalar()
        assert still_there == 1, "cross-org DELETE silently succeeded"


# ─── 4. INSERT default-from-GUC for responses + uploads ──────────────────


async def test_member_default_org_id_on_insert(
    db: AsyncSession,
    db_conn: AsyncConnection,
    axiolo_org: dict[str, str],
) -> None:
    """``responses`` and ``uploads`` get ``org_id`` from the GUC on INSERT.

    Migration 0004 installs a column DEFAULT on these two tables that
    reads ``nullif(current_setting('pulse.org_id', true), '')::uuid``.
    The client-facing INSERT path therefore does not need to know about
    the org id — the GUC the request middleware sets is enough.
    """
    # Seed a client + card as owner so the FKs resolve later. The
    # seeded card already has one response (unique constraint on
    # ``(card_id, client_id)``) so we add a fresh "extra" card here for
    # the response-default test to attach to.
    seeded = await _seed_two_orgs(db, axiolo_org["id"])
    org_a_id = seeded["a"]["org_id"]
    client_id = seeded["a"]["clients"]
    extra_card_id = (
        await db.execute(
            text(
                "insert into public.cards "
                "(client_id, order_index, category, title, context, "
                " question, response_type, org_id) "
                "values (cast(:cid as uuid), 99, 'Test', 'extra', 'ctx', "
                "        'q?', 'short-text', cast(:o as uuid)) "
                "returning id::text"
            ),
            {"cid": client_id, "o": org_a_id},
        )
    ).mappings().one()["id"]
    await db.flush()

    card_id = extra_card_id

    await become_member(db_conn, org_id=org_a_id)

    # INSERT a response WITHOUT specifying org_id.
    new_response_id = (
        await db_conn.execute(
            text(
                "insert into public.responses (card_id, client_id, state) "
                "values (cast(:card as uuid), cast(:cid as uuid), 'viewed') "
                "returning id::text"
            ),
            {"card": card_id, "cid": client_id},
        )
    ).scalar()
    assert new_response_id is not None

    inserted_org = (
        await db_conn.execute(
            text(
                "select org_id::text from public.responses "
                "where id = cast(:rid as uuid)"
            ),
            {"rid": new_response_id},
        )
    ).scalar()
    assert inserted_org == org_a_id, (
        f"responses.org_id default did not pick up the GUC: "
        f"got {inserted_org}, expected {org_a_id}"
    )

    # And the same for uploads.
    new_upload_id = (
        await db_conn.execute(
            text(
                "insert into public.uploads "
                "(card_id, client_id, file_name, file_size_bytes, storage_path) "
                "values (cast(:card as uuid), cast(:cid as uuid), "
                "        'default.pdf', 1, 'a/b/c') "
                "returning id::text"
            ),
            {"card": card_id, "cid": client_id},
        )
    ).scalar()
    assert new_upload_id is not None

    inserted_org = (
        await db_conn.execute(
            text(
                "select org_id::text from public.uploads "
                "where id = cast(:uid as uuid)"
            ),
            {"uid": new_upload_id},
        )
    ).scalar()
    assert inserted_org == org_a_id, (
        f"uploads.org_id default did not pick up the GUC: "
        f"got {inserted_org}, expected {org_a_id}"
    )


# ─── 5. pulse_request_org_id() helper contract ────────────────────────────


async def test_pulse_request_org_id_helper_returns_guc_value(
    db_conn: AsyncConnection,
) -> None:
    """``pulse_request_org_id()`` round-trips the ``pulse.org_id`` GUC.

    Locks the helper function as a contract — every RLS policy depends
    on it returning the same uuid the middleware set.
    """
    expected = str(uuid.uuid4())
    await db_conn.execute(
        text("select set_config('pulse.org_id', :o, true)"),
        {"o": expected},
    )
    got = (
        await db_conn.execute(
            text("select public.pulse_request_org_id()::text")
        )
    ).scalar()
    assert got == expected, (
        f"pulse_request_org_id() returned {got!r}, expected {expected!r}"
    )

    # And empty GUC → NULL.
    await db_conn.execute(
        text("select set_config('pulse.org_id', '', true)"),
    )
    got_null = (
        await db_conn.execute(text("select public.pulse_request_org_id()"))
    ).scalar()
    assert got_null is None, (
        f"pulse_request_org_id() with empty GUC returned {got_null!r}, "
        f"expected NULL"
    )
