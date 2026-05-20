"""Endpoint tests for the ClickUp OAuth flow + webhook receiver.

The crypto helper needs a key for these tests — set one via monkeypatched
settings + reset the lru_cache. Other secrets (HMAC computation, etc.)
are exercised through the routes, never directly.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api import crypto
from pulse_api.clickup import CLICKUP_AUTHORIZE, CLICKUP_BASE
from pulse_api.config import settings


@pytest.fixture(autouse=True)
def with_encryption_key(monkeypatch: pytest.MonkeyPatch):
    """Provide a Fernet key + the ClickUp OAuth client config for every
    test in this module. Resets the crypto key cache before AND after so
    cross-test state doesn't bleed."""
    monkeypatch.setattr(settings, "encryption_keys", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "clickup_client_id", "test-cu-client-id")
    monkeypatch.setattr(settings, "clickup_client_secret", "test-cu-secret")
    monkeypatch.setattr(
        settings, "clickup_redirect_uri",
        "http://localhost:58000/api/auth/clickup/callback",
    )
    crypto.reset_keys_cache()
    yield
    crypto.reset_keys_cache()


# ── authorize ────────────────────────────────────────────────────────────


async def test_authorize_redirects_to_clickup_with_state(admin_authed: AsyncClient) -> None:
    r = await admin_authed.get("/api/auth/clickup/authorize", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(CLICKUP_AUTHORIZE)
    q = parse_qs(urlparse(loc).query)
    assert q["client_id"][0] == "test-cu-client-id"
    assert q["state"][0]
    assert r.cookies.get("oauth_state_clickup")


async def test_authorize_rejects_anonymous(client: AsyncClient) -> None:
    r = await client.get("/api/auth/clickup/authorize", follow_redirects=False)
    assert r.status_code == 401


async def test_authorize_rejects_non_admin(client: AsyncClient, seed_user: dict) -> None:
    from pulse_api.auth.session import encode_session
    client.cookies.set(settings.session_cookie_name, encode_session(seed_user["id"]))
    r = await client.get("/api/auth/clickup/authorize", follow_redirects=False)
    assert r.status_code == 403


async def test_authorize_503_when_not_configured(
    admin_authed: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "clickup_client_id", "")
    r = await admin_authed.get("/api/auth/clickup/authorize", follow_redirects=False)
    assert r.status_code == 503


# ── callback ─────────────────────────────────────────────────────────────


async def _do_authorize(client: AsyncClient) -> tuple[str, str]:
    r = await client.get("/api/auth/clickup/authorize", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    cookie = r.cookies.get("oauth_state_clickup")
    return state, cookie


async def test_callback_stores_encrypted_token_and_creates_webhook(
    admin_authed: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    seed_admin_user: dict,
) -> None:
    state, state_cookie = await _do_authorize(admin_authed)

    # Mock ClickUp's OAuth token exchange + user info + teams + webhook create
    respx_mock.post(f"{CLICKUP_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok_AAA"})
    )
    respx_mock.get(f"{CLICKUP_BASE}/user").mock(
        return_value=httpx.Response(200, json={"user": {"id": 99, "username": "tom"}})
    )
    respx_mock.get(f"{CLICKUP_BASE}/team").mock(
        return_value=httpx.Response(200, json={"teams": [
            {"id": "ws1", "name": "Axiolo Marketing"},
        ]})
    )
    respx_mock.post(f"{CLICKUP_BASE}/team/ws1/webhook").mock(
        return_value=httpx.Response(200, json={"webhook": {
            "id": "wh1", "secret": "shhh-webhook-secret",
        }})
    )

    admin_authed.cookies.set("oauth_state_clickup", state_cookie)
    r = await admin_authed.get(
        f"/api/auth/clickup/callback?code=fake&state={state}",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "clickup=connected" in r.headers["location"]

    # Token stored encrypted (NOT plaintext)
    row = (
        await db.execute(
            text("select clickup_access_token_enc, clickup_user_id from public.users where id = cast(:i as uuid)"),
            {"i": seed_admin_user["id"]},
        )
    ).mappings().one()
    assert row["clickup_access_token_enc"] is not None
    assert row["clickup_access_token_enc"] != "tok_AAA"   # encrypted, not raw
    assert "tok_AAA" not in row["clickup_access_token_enc"]
    assert row["clickup_user_id"] == "99"

    # Webhook secret stored encrypted
    ws_row = (
        await db.execute(
            text("select workspace_id, workspace_name, webhook_id, webhook_secret_enc "
                 "from public.clickup_workspaces where user_id = cast(:i as uuid)"),
            {"i": seed_admin_user["id"]},
        )
    ).mappings().one()
    assert ws_row["workspace_id"] == "ws1"
    assert ws_row["workspace_name"] == "Axiolo Marketing"
    assert ws_row["webhook_id"] == "wh1"
    assert ws_row["webhook_secret_enc"] is not None
    assert "shhh-webhook-secret" not in ws_row["webhook_secret_enc"]  # encrypted

    # Decrypts back to the original
    assert crypto.decrypt(ws_row["webhook_secret_enc"]) == "shhh-webhook-secret"


async def test_callback_missing_state_cookie_400(admin_authed: AsyncClient) -> None:
    r = await admin_authed.get(
        "/api/auth/clickup/callback?code=x&state=y", follow_redirects=False
    )
    assert r.status_code == 400


async def test_callback_state_mismatch_400(admin_authed: AsyncClient) -> None:
    _, state_cookie = await _do_authorize(admin_authed)
    admin_authed.cookies.set("oauth_state_clickup", state_cookie)
    r = await admin_authed.get(
        "/api/auth/clickup/callback?code=x&state=ATTACKER-CHOSEN",
        follow_redirects=False,
    )
    assert r.status_code == 400


async def test_callback_provider_error_maps_to_502(
    admin_authed: AsyncClient, respx_mock: respx.Router
) -> None:
    state, state_cookie = await _do_authorize(admin_authed)
    respx_mock.post(f"{CLICKUP_BASE}/oauth/token").mock(
        return_value=httpx.Response(400, json={"err": "invalid_grant"})
    )
    admin_authed.cookies.set("oauth_state_clickup", state_cookie)
    r = await admin_authed.get(
        f"/api/auth/clickup/callback?code=bad&state={state}",
        follow_redirects=False,
    )
    assert r.status_code == 502


# ── disconnect ────────────────────────────────────────────────────────────


async def test_disconnect_clears_token_and_drops_webhooks(
    admin_authed: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    seed_admin_user: dict,
) -> None:
    # Seed a connected state directly
    enc_token = crypto.encrypt("tok_AAA")
    enc_secret = crypto.encrypt("shhh")
    await db.execute(
        text(
            "update public.users set clickup_access_token_enc = :t, clickup_user_id = '99' "
            "where id = cast(:i as uuid)"
        ),
        {"t": enc_token, "i": seed_admin_user["id"]},
    )
    await db.execute(
        text(
            "insert into public.clickup_workspaces "
            "(user_id, workspace_id, workspace_name, webhook_id, webhook_secret_enc) "
            "values (cast(:i as uuid), 'ws1', 'WS1', 'wh1', :s)"
        ),
        {"i": seed_admin_user["id"], "s": enc_secret},
    )

    respx_mock.delete(f"{CLICKUP_BASE}/webhook/wh1").mock(
        return_value=httpx.Response(200, json={})
    )

    r = await admin_authed.post("/api/auth/clickup/disconnect")
    assert r.status_code == 200
    assert r.json()["workspaces_removed"] == 1

    # Both rows gone, token cleared
    row = (
        await db.execute(
            text("select clickup_access_token_enc from public.users where id = cast(:i as uuid)"),
            {"i": seed_admin_user["id"]},
        )
    ).mappings().one()
    assert row["clickup_access_token_enc"] is None

    ws_count = (
        await db.execute(text("select count(*) from public.clickup_workspaces"))
    ).scalar()
    assert ws_count == 0


async def test_disconnect_tolerates_clickup_failure(
    admin_authed: AsyncClient,
    db: AsyncSession,
    respx_mock: respx.Router,
    seed_admin_user: dict,
) -> None:
    """If ClickUp's API call fails (expired token, network), the local
    state should still get wiped — disconnect must never leave the
    operator unable to reconnect."""
    enc_token = crypto.encrypt("tok_AAA")
    enc_secret = crypto.encrypt("shhh")
    await db.execute(
        text(
            "update public.users set clickup_access_token_enc = :t where id = cast(:i as uuid)"
        ),
        {"t": enc_token, "i": seed_admin_user["id"]},
    )
    await db.execute(
        text(
            "insert into public.clickup_workspaces "
            "(user_id, workspace_id, workspace_name, webhook_id, webhook_secret_enc) "
            "values (cast(:i as uuid), 'ws1', 'WS1', 'wh1', :s)"
        ),
        {"i": seed_admin_user["id"], "s": enc_secret},
    )

    respx_mock.delete(f"{CLICKUP_BASE}/webhook/wh1").mock(
        return_value=httpx.Response(401, json={"err": "OAUTH_017"})
    )

    r = await admin_authed.post("/api/auth/clickup/disconnect")
    assert r.status_code == 200

    row = (
        await db.execute(
            text("select clickup_access_token_enc from public.users where id = cast(:i as uuid)"),
            {"i": seed_admin_user["id"]},
        )
    ).mappings().one()
    assert row["clickup_access_token_enc"] is None


# ── webhook receiver ─────────────────────────────────────────────────────


async def test_webhook_updates_response_clickup_status(
    client: AsyncClient,
    db: AsyncSession,
    seed_client: dict,
    seed_admin_user: dict,
) -> None:
    # Seed: a card with clickup_task_id + a response row + a workspace row
    # whose secret we'll use to sign the payload.
    enc_secret = crypto.encrypt("shared-secret")
    await db.execute(
        text(
            "insert into public.clickup_workspaces "
            "(workspace_id, user_id, webhook_secret_enc) "
            "select 'ws1', cast(:u as uuid), :s"
        ),
        {"s": enc_secret, "u": seed_admin_user["id"]},
    )

    card_row = (
        await db.execute(
            text(
                "insert into public.cards "
                "(client_id, order_index, category, title, context, question, response_type, clickup_task_id) "
                "values (cast(:c as uuid), 1, 'C', 't', 'x', 'q?', 'short-text', 'task42') "
                "returning id::text"
            ),
            {"c": seed_client["id"]},
        )
    ).mappings().one()
    await db.execute(
        text(
            "insert into public.responses (card_id, client_id, state) "
            "values (cast(:k as uuid), cast(:c as uuid), 'answered')"
        ),
        {"k": card_row["id"], "c": seed_client["id"]},
    )

    # Important: db.commit() so a separate (pulse_admin) connection from
    # the route override can see these rows. The conftest's connection
    # joining means session.commit() releases a savepoint without
    # committing the outer tx.
    await db.flush()

    payload = {
        "team_id": "ws1",
        "event": "taskStatusUpdated",
        "task_id": "task42",
        "history_items": [
            {"after": {"status": "Approved"}}
        ],
    }
    body = json.dumps(payload).encode()
    sig = hmac.new(b"shared-secret", body, hashlib.sha256).hexdigest()

    r = await client.post(
        "/api/clickup/webhook",
        content=body,
        headers={"X-Signature": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["action"] == "updated"

    # Status reflected on the response
    status = (
        await db.execute(
            text(
                "select clickup_status from public.responses where card_id = cast(:k as uuid)"
            ),
            {"k": card_row["id"]},
        )
    ).scalar()
    assert status == "Approved"


async def test_webhook_rejects_bad_signature(
    client: AsyncClient, db: AsyncSession, seed_admin_user: dict,
) -> None:
    enc_secret = crypto.encrypt("right-secret")
    await db.execute(
        text(
            "insert into public.clickup_workspaces "
            "(workspace_id, user_id, webhook_secret_enc) "
            "select 'ws1', cast(:u as uuid), :s"
        ),
        {"s": enc_secret, "u": seed_admin_user["id"]},
    )
    await db.flush()

    body = json.dumps({"team_id": "ws1", "event": "x"}).encode()
    bad_sig = hmac.new(b"wrong-secret", body, hashlib.sha256).hexdigest()

    r = await client.post(
        "/api/clickup/webhook",
        content=body,
        headers={"X-Signature": bad_sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 401


async def test_webhook_rejects_unknown_workspace(
    client: AsyncClient,
) -> None:
    body = json.dumps({"team_id": "never-registered", "event": "x"}).encode()
    sig = hmac.new(b"any", body, hashlib.sha256).hexdigest()
    r = await client.post(
        "/api/clickup/webhook",
        content=body,
        headers={"X-Signature": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 401


async def test_webhook_acknowledges_unknown_task(
    client: AsyncClient, db: AsyncSession, seed_admin_user: dict,
) -> None:
    """If ClickUp sends a status update for a task Pulse has no record
    of, the receiver MUST return 200 so ClickUp doesn't retry forever."""
    enc_secret = crypto.encrypt("shared")
    await db.execute(
        text(
            "insert into public.clickup_workspaces "
            "(workspace_id, user_id, webhook_secret_enc) "
            "select 'ws1', cast(:u as uuid), :s"
        ),
        {"s": enc_secret, "u": seed_admin_user["id"]},
    )
    await db.flush()

    payload = {
        "team_id": "ws1",
        "event": "taskStatusUpdated",
        "task_id": "task-we-never-saw",
        "history_items": [{"after": {"status": "Approved"}}],
    }
    body = json.dumps(payload).encode()
    sig = hmac.new(b"shared", body, hashlib.sha256).hexdigest()

    r = await client.post(
        "/api/clickup/webhook",
        content=body,
        headers={"X-Signature": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["action"] == "ignored-unknown-task"


async def test_webhook_400_on_malformed_body(client: AsyncClient) -> None:
    r = await client.post(
        "/api/clickup/webhook",
        content=b"{not: valid json",
        headers={"X-Signature": "x", "Content-Type": "application/json"},
    )
    assert r.status_code == 400


async def test_webhook_400_on_missing_team_id(client: AsyncClient) -> None:
    body = json.dumps({"event": "x", "task_id": "y"}).encode()
    r = await client.post(
        "/api/clickup/webhook",
        content=body,
        headers={"X-Signature": "x", "Content-Type": "application/json"},
    )
    assert r.status_code == 400


async def test_webhook_acknowledges_unsubscribed_events(
    client: AsyncClient, db: AsyncSession, seed_admin_user: dict,
) -> None:
    """Other events besides taskStatusUpdated → 200 noop. Lets us add
    event subscriptions later without breaking older receivers."""
    enc_secret = crypto.encrypt("shared")
    await db.execute(
        text(
            "insert into public.clickup_workspaces "
            "(workspace_id, user_id, webhook_secret_enc) "
            "select 'ws1', cast(:u as uuid), :s"
        ),
        {"s": enc_secret, "u": seed_admin_user["id"]},
    )
    await db.flush()

    body = json.dumps({"team_id": "ws1", "event": "taskCreated", "task_id": "x"}).encode()
    sig = hmac.new(b"shared", body, hashlib.sha256).hexdigest()
    r = await client.post(
        "/api/clickup/webhook",
        content=body,
        headers={"X-Signature": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["action"] == "noop"
