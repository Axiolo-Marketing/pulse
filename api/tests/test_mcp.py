"""HTTPS MCP server tests.

Eight cases from the plan's Phase 2 "Tests" section:

  1. tools/list returns the 11 tools (with a valid token — RS mode).
  2. Each tool round-trips with a valid admin key and the DB state
     matches expectations (we exercise create + delete here; the rest are
     covered structurally by tools/list shape + auth gating below).
  3. Missing Authorization header → HTTP 401 (RequireAuthMiddleware).
  4. Revoked key → HTTP 401.
  5. Non-member valid key → HTTP 401.
  6. pulse_create_engagement + pulse_import_deck chain creates the
     expected card rows in order.
  7. pulse_upload_attachment then pulse_add_card with the returned path
     links the attachment correctly.
  8. Oversize base64 payload rejected with a too-large error before any
     disk write happens.

Wire protocol: stateless HTTP, no session-id continuation. Each test
sends a single JSON-RPC POST per tool call.

Test infra notes:
  • FastMCP's streamable-HTTP transport needs `mcp.session_manager.run()`
    active while the test's HTTP client is alive. We enter that context
    manager once per test via the `mcp_runtime` fixture.
  • Tools open admin DB sessions via `_open_admin_session`. We monkeypatch
    it to bind through the test's rolled-back connection so mutations are
    visible to the test's `db` session AND wiped at teardown.
  • The streamable HTTP route lives at `/api/mcp/` (trailing slash — the
    mount point + the empty-path inner route; httpx's
    `follow_redirects=True` would also work for `/api/mcp`).
"""
from __future__ import annotations

import asyncio
import base64
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
from pulse_api.config import settings
from pulse_api.mcp import server as mcp_server


MCP_PATH = "/api/mcp/"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
async def mcp_runtime(
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    mcp_session_manager: None,  # shared session-scoped fixture in conftest
) -> None:
    """Patch tool session factories to bind through the test's connection.

    The session manager itself comes from the session-scoped fixture
    above; per-test we only need to redirect DB writes through the
    rolled-back connection so they're visible to the test's `db` session
    and wiped at teardown.
    """
    @asynccontextmanager
    async def _override_session() -> AsyncIterator[AsyncSession]:
        # A previous request in the same test may have set role pulse_anon
        # via a different code path; reset before issuing admin queries.
        await db_conn.execute(text("reset role"))
        factory = async_sessionmaker(
            bind=db_conn, expire_on_commit=False, class_=AsyncSession
        )
        async with factory() as session:
            yield session

    @asynccontextmanager
    async def _override_member_session(org_id: str) -> AsyncIterator[AsyncSession]:
        # After PR 2 the tools open a ``pulse_member`` session per call;
        # in tests we route those through the same db_conn (the rollback
        # transaction). Flip the role + set the GUC, then yield. The
        # outer test transaction wipes everything at teardown.
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

    monkeypatch.setattr(mcp_server, "_open_admin_session", _override_session)
    monkeypatch.setattr(mcp_server, "_open_member_session", _override_member_session)
    # `tools.py` captured these at import time, so patch the name there
    # too — both modules use them.
    from pulse_api.mcp import tools as mcp_tools

    monkeypatch.setattr(mcp_tools, "_open_member_session", _override_member_session)

    # RS mode: the bearer token is validated at the HTTP layer by the
    # `verifier` singleton, which opens its own admin session. Route that
    # through db_conn too so key lookups + membership checks see the
    # test's seeded rows.
    from pulse_api.mcp.oauth import verifier as verifier_mod

    monkeypatch.setattr(verifier_mod, "_admin_session", _override_session)

    # Patch _touch_last_used through the same conn (mirrors the override
    # in conftest's `client` fixture). Lives on `auth.api_keys` after the
    # bearer-validation extraction — both REST and MCP go through there.
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

    monkeypatch.setattr(_api_keys_lib, "_touch_last_used", _patched_touch_last_used)


@pytest.fixture
async def mcp_client(
    client: AsyncClient, mcp_runtime: None
) -> AsyncClient:
    """The httpx client from conftest, with the MCP runtime active.

    Reuses `client` because that fixture already wires up the DB-session
    dependency overrides for the rest of the FastAPI app (auth/me, etc.),
    which the MCP layer doesn't need but doesn't hurt either.
    """
    return client


async def _insert_admin_key(
    db: AsyncSession,
    *,
    user_id: str,
    org_id: str,
    revoked: bool = False,
) -> str:
    """Insert an MCP-test API key for ``(user, org)`` and return its raw value."""
    raw = generate_key()
    await db.execute(
        text(
            "insert into public.api_keys "
            "(user_id, org_id, prefix, key_hash, label, revoked_at) "
            "values (cast(:u as uuid), cast(:o as uuid), :p, :h, :l, "
            "  case when :r then now() else null end)"
        ),
        {
            "u": user_id,
            "o": org_id,
            "p": prefix_of(raw),
            "h": hash_key(raw),
            "l": "mcp test",
            "r": revoked,
        },
    )
    return raw


async def _mcp_post(
    mcp_client: AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
):
    """POST a JSON-RPC call and return the raw httpx response.

    Used by auth-failure tests that assert on the HTTP status code:
    after RS-mode, a missing/invalid/non-member credential is rejected
    by ``RequireAuthMiddleware`` with a 401 before any tool body runs.
    """
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
    return await mcp_client.post(MCP_PATH, json=body, headers=headers)


async def _mcp_call(
    mcp_client: AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    r = await _mcp_post(mcp_client, method, params, api_key=api_key)
    assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text}"
    return r.json()


def _tool_call_payload(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "arguments": arguments}


def _structured(result: dict[str, Any]) -> Any:
    """Pull the structured content out of a tools/call result.

    FastMCP returns both `content` (text-rendered) and `structuredContent`
    (the original Python return value, JSON-serialized). We assert against
    the structured form because it's what the SDK exposes to model
    clients.
    """
    sc = result["result"].get("structuredContent")
    assert sc is not None, f"no structuredContent in result: {result}"
    # FastMCP wraps non-dict returns under {"result": ...}; dicts are
    # passed through as-is. Detect by checking for the wrapper key.
    return sc.get("result", sc)


# ── Tests ────────────────────────────────────────────────────────────────


# 1. tools/list returns the 11 expected tools (RS mode requires a token).
async def test_tools_list_returns_eleven_tools(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw = await _insert_admin_key(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )
    resp = await _mcp_call(mcp_client, "tools/list", api_key=raw)
    tools = resp["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    assert names == sorted(
        [
            "pulse_list_engagements",
            "pulse_get_engagement",
            "pulse_create_engagement",
            "pulse_update_engagement",
            "pulse_delete_engagement",
            "pulse_import_deck",
            "pulse_add_card",
            "pulse_update_card",
            "pulse_delete_card",
            "pulse_upload_attachment",
        ]
    )


# 2. Round-trip a couple of tools (create + delete) with a valid admin
#    key and verify DB state changes match the REST endpoint's behaviour.
async def test_create_then_delete_engagement_roundtrip(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw = await _insert_admin_key(
        db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"]
    )

    create_resp = await _mcp_call(
        mcp_client,
        "tools/call",
        _tool_call_payload(
            "pulse_create_engagement",
            {"client_name": "MCP roundtrip", "org_name": "Acme"},
        ),
        api_key=raw,
    )
    assert create_resp["result"].get("isError") is not True, create_resp
    created = _structured(create_resp)
    client_id = created["id"]
    assert created["name"] == "MCP roundtrip"
    assert created["client_name"] == "MCP roundtrip"
    assert created["org_name"] == "Acme"
    # The engagement + its client row exist in the DB now.
    row = (
        await db.execute(
            text(
                "select cl.name from public.engagements e "
                "join public.clients cl on cl.id = e.client_id "
                "where e.id = cast(:i as uuid)"
            ),
            {"i": client_id},
        )
    ).scalar_one_or_none()
    assert row == "MCP roundtrip"

    # Delete it.
    del_resp = await _mcp_call(
        mcp_client,
        "tools/call",
        _tool_call_payload("pulse_delete_engagement", {"engagement_id": client_id}),
        api_key=raw,
    )
    assert del_resp["result"].get("isError") is not True, del_resp
    assert _structured(del_resp) == {"ok": True}

    gone = (
        await db.execute(
            text("select 1 from public.engagements where id = cast(:i as uuid)"),
            {"i": client_id},
        )
    ).scalar_one_or_none()
    assert gone is None


# 3. Missing Authorization header → HTTP 401 (RequireAuthMiddleware).
#    RS-mode validates the bearer at the HTTP layer, so an unauthenticated
#    call never reaches a tool body — it's rejected with 401 +
#    WWW-Authenticate, the discovery trigger Claude Desktop relies on.
async def test_missing_authorization_returns_401(
    mcp_client: AsyncClient,
) -> None:
    resp = await _mcp_post(
        mcp_client,
        "tools/call",
        _tool_call_payload("pulse_list_engagements", {}),
        api_key=None,
    )
    assert resp.status_code == 401
    www = resp.headers.get("www-authenticate", "")
    assert "resource_metadata=" in www


# 4. Revoked key → HTTP 401 (the verifier returns None for a revoked key).
async def test_revoked_key_returns_401(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw = await _insert_admin_key(
        db,
        user_id=seed_admin_user["id"],
        org_id=seed_admin_user["org_id"],
        revoked=True,
    )
    resp = await _mcp_post(
        mcp_client,
        "tools/call",
        _tool_call_payload("pulse_list_engagements", {}),
        api_key=raw,
    )
    assert resp.status_code == 401


# 5. Valid key on a user who is NOT a member of the key's org → HTTP 401.
#    Post-PR-2 "admin only" is gone — what matters is membership in the
#    org the key was minted against. The verifier re-checks membership
#    and returns None when it's missing, so the middleware 401s.
async def test_non_member_key_returns_401(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_user: dict[str, str],
    axiolo_org: dict[str, str],
) -> None:
    raw = await _insert_admin_key(
        db, user_id=seed_user["id"], org_id=axiolo_org["id"]
    )
    resp = await _mcp_post(
        mcp_client,
        "tools/call",
        _tool_call_payload("pulse_list_engagements", {}),
        api_key=raw,
    )
    assert resp.status_code == 401


# 6. create_engagement + import_deck chain.
async def test_create_engagement_then_import_deck_orders_cards(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw = await _insert_admin_key(db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"])

    create_resp = await _mcp_call(
        mcp_client,
        "tools/call",
        _tool_call_payload(
            "pulse_create_engagement", {"client_name": "Deck import"}
        ),
        api_key=raw,
    )
    client_id = _structured(create_resp)["id"]

    deck = (
        "## Card 1: First\n\n"
        "**Category:** Onboarding\n"
        "**Type:** confirm-edit\n"
        "**Skip:** optional\n\n"
        "**Context:**\n"
        "hi\n\n"
        "**Question:**\n"
        "ready?\n\n"
        "---\n\n"
        "## Card 2: Second\n\n"
        "**Category:** Onboarding\n"
        "**Type:** single-select\n"
        "**Skip:** required\n\n"
        "**Context:**\n"
        "pick\n\n"
        "**Question:**\n"
        "which?\n\n"
        "**Options:**\n"
        "- A\n"
        "- B\n"
    )
    import_resp = await _mcp_call(
        mcp_client,
        "tools/call",
        _tool_call_payload(
            "pulse_import_deck", {"engagement_id": client_id, "markdown": deck}
        ),
        api_key=raw,
    )
    assert import_resp["result"].get("isError") is not True, import_resp
    created_cards = _structured(import_resp)["created"]
    assert [c["title"] for c in created_cards] == ["First", "Second"]
    # Order indices should be 1 and 2.
    assert [c["order_index"] for c in created_cards] == [1, 2]

    # DB cross-check: the same cards are present and ordered.
    rows = (
        await db.execute(
            text(
                "select title, order_index from public.cards "
                "where engagement_id = cast(:i as uuid) order by order_index"
            ),
            {"i": client_id},
        )
    ).all()
    assert [(r[0], r[1]) for r in rows] == [("First", 1), ("Second", 2)]


# 7. upload_attachment → add_card chain links the attachment.
async def test_upload_attachment_then_add_card_links_path(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw = await _insert_admin_key(db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"])

    # 1x1 transparent PNG (smallest valid PNG)
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6300010000000500010d0a2db40000000049454e44ae42"
        "6082"
    )
    encoded = base64.b64encode(png_bytes).decode()

    upload_resp = await _mcp_call(
        mcp_client,
        "tools/call",
        _tool_call_payload(
            "pulse_upload_attachment",
            {"filename": "tiny.png", "content_base64": encoded},
        ),
        api_key=raw,
    )
    assert upload_resp["result"].get("isError") is not True, upload_resp
    attachment = _structured(upload_resp)
    assert attachment["path"].startswith("attachments/")
    assert attachment["path"].endswith(".png")
    assert attachment["mime_type"] == "image/png"

    # File written to the test's tmp upload dir.
    from pulse_api import storage as _storage

    on_disk = _storage.resolve_within_upload_dir(attachment["path"])
    assert on_disk.exists()
    assert on_disk.read_bytes() == png_bytes

    # Create an engagement + card linking that attachment.
    eng = _structured(
        await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload(
                "pulse_create_engagement", {"client_name": "Attachment link"}
            ),
            api_key=raw,
        )
    )
    card_resp = await _mcp_call(
        mcp_client,
        "tools/call",
        _tool_call_payload(
            "pulse_add_card",
            {
                "engagement_id": eng["id"],
                "category": "ref",
                "title": "PNG ref",
                "context": "look",
                "question": "see?",
                "response_type": "document-link",
                "attachment_path": attachment["path"],
            },
        ),
        api_key=raw,
    )
    assert card_resp["result"].get("isError") is not True, card_resp
    card = _structured(card_resp)
    assert card["attachment_path"] == attachment["path"]


# Bonus coverage: exercise the remaining tools in one flow so the broader
# tool surface stays under test. Not one of the eight required cases, but
# a regression net for the other ~half of `tools.py`.
async def test_remaining_tool_surface_roundtrip(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
) -> None:
    raw = await _insert_admin_key(db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"])

    # list_engagements (empty path)
    listed = _structured(
        await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload("pulse_list_engagements", {}),
            api_key=raw,
        )
    )
    assert isinstance(listed, list)

    # create
    eng = _structured(
        await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload(
                "pulse_create_engagement", {"client_name": "Surface"}
            ),
            api_key=raw,
        )
    )
    cid = eng["id"]

    # get_engagement
    got = _structured(
        await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload("pulse_get_engagement", {"engagement_id": cid}),
            api_key=raw,
        )
    )
    assert got["engagement"]["id"] == cid
    assert got["cards"] == []

    # update_engagement
    updated = _structured(
        await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload(
                "pulse_update_engagement",
                {"engagement_id": cid, "org_name": "Org", "brief": "Hello"},
            ),
            api_key=raw,
        )
    )
    assert updated["org_name"] == "Org"
    assert updated["brief"] == "Hello"

    # add_card
    card = _structured(
        await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload(
                "pulse_add_card",
                {
                    "engagement_id": cid,
                    "category": "Intro",
                    "title": "Welcome",
                    "context": "ctx",
                    "question": "ready?",
                    "response_type": "short-text",
                },
            ),
            api_key=raw,
        )
    )
    card_id = card["id"]

    # update_card
    updated_card = _structured(
        await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload(
                "pulse_update_card",
                {"card_id": card_id, "title": "Welcome v2"},
            ),
            api_key=raw,
        )
    )
    assert updated_card["title"] == "Welcome v2"

    # delete_card
    del_card = _structured(
        await _mcp_call(
            mcp_client,
            "tools/call",
            _tool_call_payload("pulse_delete_card", {"card_id": card_id}),
            api_key=raw,
        )
    )
    assert del_card == {"ok": True}


# 8. Oversize base64 payload rejected before any disk write.
async def test_oversize_attachment_rejected_before_disk_write(
    mcp_client: AsyncClient,
    db: AsyncSession,
    seed_admin_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = await _insert_admin_key(db, user_id=seed_admin_user["id"], org_id=seed_admin_user["org_id"])

    # Shrink the limit way down so the test payload doesn't need to be
    # megabytes long.
    monkeypatch.setattr(settings, "max_upload_bytes", 64)

    # 128 bytes of zeroes — twice the limit.
    encoded = base64.b64encode(b"\x00" * 128).decode()

    # Trip-wire: assert no write_upload is called on the rejection path.
    from pulse_api import storage as _storage

    write_calls: list[Any] = []
    real_write = _storage.write_upload

    def _spy_write(**kwargs: Any) -> None:
        write_calls.append(kwargs)
        return real_write(**kwargs)

    monkeypatch.setattr(_storage, "write_upload", _spy_write)
    # tools.py imported `storage` directly as a module reference; the
    # monkeypatch above hits the same object via attribute access.

    resp = await _mcp_call(
        mcp_client,
        "tools/call",
        _tool_call_payload(
            "pulse_upload_attachment",
            {"filename": "big.png", "content_base64": encoded},
        ),
        api_key=raw,
    )
    assert resp["result"]["isError"] is True
    txt = resp["result"]["content"][0]["text"].lower()
    assert "too large" in txt
    # No disk write happened. This is the load-bearing assertion the plan
    # called out specifically: the size guard runs BEFORE storage.
    assert write_calls == []
