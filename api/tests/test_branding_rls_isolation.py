"""Direct-SQL RLS isolation for the ``organizations_anon_read`` policy.

Migration 0007 added an anon SELECT path on ``organizations`` so the
client deck (``pulse_anon``) can read its OWN org's ``branding`` /
``logo_path``. This test proves the policy is org-scoped: with the
request's ``pulse.org_id`` GUC pinned to org A, a ``pulse_anon``
``select ... from organizations`` returns ONLY org A's row — never org
B's — and an unset GUC returns nothing.

Like ``test_rls_isolation.py`` / ``test_multi_tenant_isolation.py``, this
runs raw SQL with no FastAPI in the middle, so it proves the
database-layer backstop independent of the application layer. Seed as the
owner, then flip the effective role to ``pulse_anon`` with the GUCs set
the way ``get_anon_session`` would in production.
"""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tests.conftest import become_anon


async def _seed_org_with_client(
    db: AsyncSession, *, label: str, branding_json: str | None
) -> dict[str, str]:
    """Insert an org (with optional branding) + a client bound to it.

    Returns ``{org_id, token, name}``.
    """
    org_id = (
        await db.execute(
            text(
                "insert into public.organizations (name, slug, branding) "
                "values (:n, :s, cast(:b as jsonb)) returning id::text"
            ),
            {
                "n": f"{label} Inc",
                "s": f"{label.lower()}-{secrets.token_hex(4)}",
                "b": branding_json,
            },
        )
    ).mappings().one()["id"]
    token = secrets.token_hex(8)
    await db.execute(
        text(
            "with c as ("
            "  insert into public.clients (org_id, name) "
            "  values (cast(:o as uuid), :n) "
            "  on conflict (org_id, name) do update set name = excluded.name "
            "  returning id"
            ") "
            "insert into public.engagements (client_id, token, org_id) "
            "select c.id, :t, cast(:o as uuid) from c"
        ),
        {"n": f"{label} Client", "t": token, "o": org_id},
    )
    return {"org_id": org_id, "token": token, "name": f"{label} Inc"}


async def _become_anon_for_org(
    conn: AsyncConnection, *, token: str, org_id: str
) -> None:
    """Flip to ``pulse_anon`` and set BOTH GUCs the org policy reads.

    ``become_anon`` only sets ``pulse.token``; the
    ``organizations_anon_read`` policy keys off ``pulse.org_id`` (via
    ``pulse_request_org_id()``), so set it the way ``get_anon_session``
    does — from the token's client row.
    """
    await become_anon(conn, token=token)
    await conn.execute(
        text("select set_config('pulse.org_id', :o, true)"),
        {"o": org_id},
    )


async def test_anon_reads_only_own_org_row(
    db: AsyncSession,
    db_conn: AsyncConnection,
) -> None:
    """A ``pulse_anon`` session pinned to org A sees only org A's
    ``organizations`` row, with org A's branding — never org B's."""
    org_a = await _seed_org_with_client(
        db, label="Alpha", branding_json='{"brand_color": "#aaaaaa"}'
    )
    org_b = await _seed_org_with_client(
        db, label="Bravo", branding_json='{"brand_color": "#bbbbbb"}'
    )

    await _become_anon_for_org(
        db_conn, token=org_a["token"], org_id=org_a["org_id"]
    )

    rows = (
        await db_conn.execute(
            text("select id::text, branding from public.organizations")
        )
    ).mappings().all()
    assert len(rows) == 1, f"anon saw {len(rows)} org rows; expected exactly 1"
    assert rows[0]["id"] == org_a["org_id"]
    assert rows[0]["branding"] == {"brand_color": "#aaaaaa"}
    # Org B never appears.
    assert org_b["org_id"] not in {r["id"] for r in rows}


async def test_anon_branding_query_returns_only_own_value(
    db: AsyncSession,
    db_conn: AsyncConnection,
) -> None:
    """``select branding from organizations`` under org A's anon session
    yields org A's branding only — the direct-SQL analogue of the task's
    isolation requirement."""
    org_a = await _seed_org_with_client(
        db, label="Alpha", branding_json='{"font": "inter"}'
    )
    await _seed_org_with_client(
        db, label="Bravo", branding_json='{"font": "lora"}'
    )

    await _become_anon_for_org(
        db_conn, token=org_a["token"], org_id=org_a["org_id"]
    )

    brandings = [
        r[0]
        for r in (
            await db_conn.execute(
                text("select branding from public.organizations")
            )
        ).all()
    ]
    assert brandings == [{"font": "inter"}]


async def test_anon_with_no_org_id_sees_zero_org_rows(
    db: AsyncSession,
    db_conn: AsyncConnection,
) -> None:
    """With no ``pulse.org_id`` set, the anon policy admits no org rows."""
    await _seed_org_with_client(
        db, label="Alpha", branding_json='{"brand_color": "#aaaaaa"}'
    )

    # become_anon WITHOUT setting pulse.org_id → the policy's
    # pulse_request_org_id() is NULL, matching no row.
    await become_anon(db_conn)

    count = (
        await db_conn.execute(
            text("select count(*) from public.organizations")
        )
    ).scalar()
    assert count == 0, "anon with no org_id leaked organization rows"


async def test_anon_with_foreign_org_id_sees_zero_rows(
    db: AsyncSession,
    db_conn: AsyncConnection,
) -> None:
    """Pinning ``pulse.org_id`` to org B while holding org A's token still
    returns only the row matching the GUC (org B), and nothing when that
    GUC points at an org whose row the seed didn't create for this view.

    Concretely: set the GUC to org A but query — only org A returns; org
    B's row stays invisible. This locks the policy to the GUC, not the
    token alone.
    """
    org_a = await _seed_org_with_client(
        db, label="Alpha", branding_json=None
    )
    org_b = await _seed_org_with_client(
        db, label="Bravo", branding_json='{"brand_color": "#bbbbbb"}'
    )

    # GUC pinned to org A; org B must never be visible.
    await _become_anon_for_org(
        db_conn, token=org_a["token"], org_id=org_a["org_id"]
    )
    visible = {
        r[0]
        for r in (
            await db_conn.execute(
                text("select id::text from public.organizations")
            )
        ).all()
    }
    assert visible == {org_a["org_id"]}
    assert org_b["org_id"] not in visible
