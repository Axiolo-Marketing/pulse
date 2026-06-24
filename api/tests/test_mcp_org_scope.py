"""MCP-layer org isolation tests.

After PR 2 every MCP tool opens a ``pulse_member`` session against the
API key's ``org_id``. We prove:

  1. ``pulse_list_engagements`` returns ONLY the key's org's clients.
  2. ``pulse_create_engagement`` lands its row in the key's org.
  3. ``tools/list`` requires a token — under RS mode ``RequireAuthMiddleware``
     gates the whole endpoint, so unauthenticated enumeration 401s (the
     OAuth flow authenticates before listing tools).

The MCP fixtures (session manager, runtime, ``mcp_client``) are
re-defined locally — pytest does not auto-discover fixtures across
test modules, only via ``conftest.py``. Keeping a small copy here is
preferable to pulling the runtime up into the shared ``conftest`` (it
would slow every non-MCP test).
"""
from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

from pulse_api.auth.api_keys import generate_key, hash_key, prefix_of
from pulse_api.mcp import server as mcp_server


MCP_PATH = "/api/mcp/"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


# ── Fixtures (copy of test_mcp.py's MCP runtime) ─────────────────────────


@pytest.fixture
async def mcp_runtime(
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    mcp_session_manager: None,  # shared session-scoped fixture in conftest
) -> None:
    """Bind MCP tool session factories to the test's connection."""
    @asynccontextmanager
    async def _override_admin_session() -> AsyncIterator[AsyncSession]:
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    @asynccontextmanager
    async def _override_member_session(org_id: str) -> AsyncIterator[AsyncSession]:
        await db_conn.execute(text("reset role"))
        await db_conn.execute(text("set local role pulse_member"))
        await db_conn.execute(
            text("select set_config('pulse.org_id', :o, true)"),
            {"o": str(org_id)},
        )
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    monkeypatch.setattr(mcp_server, "_open_admin_session", _override_admin_session)
    monkeypatch.setattr(mcp_server, "_open_member_session", _override_member_session)
    from pulse_api.mcp import tools as mcp_tools

    monkeypatch.setattr(mcp_tools, "_open_member_session", _override_member_session)

    # RS mode validates the bearer token via the `verifier` singleton,
    # which opens its own admin session — bind it through db_conn too.
    from pulse_api.mcp.oauth import verifier as verifier_mod

    monkeypatch.setattr(
        verifier_mod, "_admin_session", _override_admin_session
    )

    from pulse_api.auth import api_keys as _api_keys_lib
    from pulse_api.repos import api_keys as _api_keys_repo

    async def _patched_touch_last_used(api_key_id) -> None:
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as touch_session:
            await _api_keys_repo.mark_used(touch_session, api_key_id)
            await touch_session.commit()

    monkeypatch.setattr(
        _api_keys_lib, "_touch_last_used", _patched_touch_last_used
    )


@pytest.fixture
async def mcp_client(client: AsyncClient, mcp_runtime: None) -> AsyncClient:
    return client


# ── Helpers ──────────────────────────────────────────────────────────────


async def _mcp_call(
    mcp_client: AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    headers = dict(MCP_HEADERS)
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
    }
    if params is not None:
        body["params"] = params
    r = await mcp_client.post(MCP_PATH, json=body, headers=headers)
    assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text}"
    return r.json()


def _tool_call_payload(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "arguments": arguments}


def _structured(result: dict[str, Any]) -> Any:
    sc = result["result"].get("structuredContent")
    assert sc is not None, f"no structuredContent in result: {result}"
    return sc.get("result", sc)


async def _insert_admin_key(
    db: AsyncSession,
    *,
    user_id: str,
    org_id: str,
    revoked: bool = False,
) -> str:
    raw = generate_key()
    await db.execute(
        text(
            "insert into public.api_keys "
            "(user_id, org_id, prefix, key_hash, label, revoked_at) "
            "values (cast(:u as uuid), cast(:o as uuid), :p, :h, :l, "
            "        case when :r then now() else null end)"
        ),
        {
            "u": user_id,
            "o": org_id,
            "p": prefix_of(raw),
            "h": hash_key(raw),
            "l": "mcp scope test",
            "r": revoked,
        },
    )
    return raw


async def _make_org(db: AsyncSession, name: str) -> str:
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


async def _seed_client(db: AsyncSession, *, org_id: str, name: str) -> str:
    client = (
        await db.execute(
            text(
                "insert into public.clients (org_id, name) "
                "values (cast(:o as uuid), :n) "
                "on conflict (org_id, name) do update set name = excluded.name "
                "returning id::text"
            ),
            {"o": org_id, "n": name},
        )
    ).mappings().one()
    row = (
        await db.execute(
            text(
                "insert into public.engagements (client_id, token, org_id) "
                "values (cast(:c as uuid), :t, cast(:o as uuid)) returning id::text"
            ),
            {"c": client["id"], "t": secrets.token_hex(8), "o": org_id},
        )
    ).mappings().one()
    return row["id"]


# ── Parametrized matrix ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool_name, scenario",
    [
        ("pulse_list_engagements", "lists_only_key_org_clients"),
        ("pulse_create_engagement", "creates_in_key_org"),
    ],
)
async def test_mcp_tool_scoped_to_key_org(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    tool_name: str,
    scenario: str,
) -> None:
    """Drive the org-isolation matrix across the two load-bearing tools."""
    axiolo_id = seed_admin_user["org_id"]
    acme_id = await _make_org(db, "Acme")
    # Add admin to Acme too — the auth gate would otherwise 403 on the
    # cross-org probe, which is not what we're testing here.
    await db.execute(
        text(
            "insert into public.organization_memberships "
            "(org_id, user_id, role) "
            "values (cast(:o as uuid), cast(:u as uuid), 'owner')"
        ),
        {"o": acme_id, "u": seed_admin_user["id"]},
    )
    axiolo_client_id = await _seed_client(
        db, org_id=axiolo_id, name="Axiolo-Engagement"
    )
    acme_client_id = await _seed_client(
        db, org_id=acme_id, name="Acme-Engagement"
    )
    await db.flush()

    raw = await _insert_admin_key(
        db, user_id=seed_admin_user["id"], org_id=axiolo_id
    )

    if scenario == "lists_only_key_org_clients":
        resp = await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload(tool_name, {}),
            api_key=raw,
        )
        listed = _structured(resp)
        listed_ids = {c["id"] for c in listed}
        assert axiolo_client_id in listed_ids
        assert acme_client_id not in listed_ids, (
            "Axiolo-scoped key leaked Acme client through MCP"
        )

    elif scenario == "creates_in_key_org":
        resp = await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload(
                tool_name, {"client_name": "MCP scope test"}
            ),
            api_key=raw,
        )
        new_row = _structured(resp)
        new_id = new_row["id"]
        stored_org_id = (
            await db.execute(
                text(
                    "select org_id::text from public.engagements "
                    "where id = cast(:i as uuid)"
                ),
                {"i": new_id},
            )
        ).scalar()
        assert stored_org_id == axiolo_id, (
            f"MCP create landed in org {stored_org_id}, expected {axiolo_id}"
        )

    else:  # pragma: no cover
        raise AssertionError(f"unknown scenario {scenario!r}")


# ── tools/list under RS-mode auth ────────────────────────────────────────


async def test_tools_list_requires_auth_and_returns_tools(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    """RS mode gates the whole endpoint: ``tools/list`` 401s without a
    credential and returns the tool catalog with a valid key."""
    # Without a credential, RequireAuthMiddleware 401s before discovery.
    r = await mcp_client.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/list"},
        headers=MCP_HEADERS,
    )
    assert r.status_code == 401

    raw = await _insert_admin_key(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    resp = await _mcp_call(mcp_client, "tools/list", api_key=raw)
    tools = resp["result"]["tools"]
    assert isinstance(tools, list)
    assert len(tools) >= 1
