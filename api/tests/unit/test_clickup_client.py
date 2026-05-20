"""Unit tests for `pulse_api.clickup`.

Every outgoing call mocked via `respx`. Assertions cover:
- URL + method + headers (auth header has no `Bearer` prefix, per ClickUp's API)
- Body shape on the create/update paths
- `ClickUpError` raised on non-2xx
- `exchange_code` raises when the provider response is shaped right but
  missing `access_token`
"""
from __future__ import annotations

import httpx
import pytest
import respx

from pulse_api.clickup import (
    CLICKUP_AUTHORIZE,
    CLICKUP_BASE,
    ClickUpClient,
    ClickUpError,
    build_authorize_url,
    exchange_code,
)


def test_build_authorize_url_includes_required_params() -> None:
    url = build_authorize_url(
        client_id="abc", redirect_uri="https://pulse.axiolo.com/cb", state="xyz"
    )
    assert url.startswith(CLICKUP_AUTHORIZE)
    assert "client_id=abc" in url
    assert "state=xyz" in url
    # Redirect URI must be URL-encoded
    assert "redirect_uri=https%3A%2F%2Fpulse.axiolo.com%2Fcb" in url


# ── exchange_code ─────────────────────────────────────────────────────────


async def test_exchange_code_happy_path(respx_mock: respx.Router) -> None:
    respx_mock.post(f"{CLICKUP_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok_abc"})
    )
    token = await exchange_code(client_id="cid", client_secret="secret", code="code")
    assert token == "tok_abc"


async def test_exchange_code_raises_on_4xx(respx_mock: respx.Router) -> None:
    respx_mock.post(f"{CLICKUP_BASE}/oauth/token").mock(
        return_value=httpx.Response(400, json={"err": "invalid_grant"})
    )
    with pytest.raises(ClickUpError) as exc:
        await exchange_code(client_id="cid", client_secret="secret", code="bad")
    assert exc.value.status == 400


async def test_exchange_code_raises_when_no_token_in_body(respx_mock: respx.Router) -> None:
    respx_mock.post(f"{CLICKUP_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"oops": "missing"})
    )
    with pytest.raises(ClickUpError) as exc:
        await exchange_code(client_id="cid", client_secret="secret", code="c")
    assert "no access_token" in exc.value.detail


# ── ClickUpClient: auth header + URL + method ────────────────────────────


async def test_get_user_attaches_token_without_bearer_prefix(respx_mock: respx.Router) -> None:
    """ClickUp's docs and behavior: send the raw token in Authorization,
    NOT `Bearer <token>`. This is the most common integration footgun."""
    route = respx_mock.get(f"{CLICKUP_BASE}/user").mock(
        return_value=httpx.Response(200, json={"user": {"id": 42, "username": "tom"}})
    )
    client = ClickUpClient("pk_abc123")
    user = await client.get_authorized_user()
    assert user == {"id": 42, "username": "tom"}
    assert route.calls.last.request.headers["Authorization"] == "pk_abc123"
    # Confirm we did NOT prefix with Bearer
    assert "Bearer" not in route.calls.last.request.headers["Authorization"]


async def test_get_authorized_teams(respx_mock: respx.Router) -> None:
    respx_mock.get(f"{CLICKUP_BASE}/team").mock(
        return_value=httpx.Response(200, json={"teams": [{"id": "ws1", "name": "Axiolo"}]})
    )
    teams = await ClickUpClient("tok").get_authorized_teams()
    assert teams == [{"id": "ws1", "name": "Axiolo"}]


async def test_create_task_posts_json_payload(respx_mock: respx.Router) -> None:
    route = respx_mock.post(f"{CLICKUP_BASE}/list/901/task").mock(
        return_value=httpx.Response(200, json={"id": "task1", "name": "Q1"})
    )
    result = await ClickUpClient("tok").create_task(
        "901", {"name": "Q1", "status": "Axiolo Review"}
    )
    assert result["id"] == "task1"
    import json as _json
    body = _json.loads(route.calls.last.request.read())
    assert body == {"name": "Q1", "status": "Axiolo Review"}


async def test_update_task_puts_to_task_id(respx_mock: respx.Router) -> None:
    route = respx_mock.put(f"{CLICKUP_BASE}/task/task1").mock(
        return_value=httpx.Response(200, json={"id": "task1", "name": "Q1 v2"})
    )
    await ClickUpClient("tok").update_task("task1", {"name": "Q1 v2"})
    assert route.called


async def test_create_webhook_returns_secret(respx_mock: respx.Router) -> None:
    respx_mock.post(f"{CLICKUP_BASE}/team/ws1/webhook").mock(
        return_value=httpx.Response(200, json={
            "webhook": {"id": "wh1", "secret": "shhh", "endpoint": "https://x/y"}
        })
    )
    result = await ClickUpClient("tok").create_webhook(
        "ws1", endpoint="https://x/y", events=["taskStatusUpdated"]
    )
    assert result["id"] == "wh1"
    assert result["secret"] == "shhh"


async def test_delete_webhook(respx_mock: respx.Router) -> None:
    route = respx_mock.delete(f"{CLICKUP_BASE}/webhook/wh1").mock(
        return_value=httpx.Response(200, json={})
    )
    await ClickUpClient("tok").delete_webhook("wh1")
    assert route.called


async def test_non_2xx_raises_clickup_error(respx_mock: respx.Router) -> None:
    respx_mock.get(f"{CLICKUP_BASE}/user").mock(
        return_value=httpx.Response(401, json={"err": "OAUTH_017"})
    )
    with pytest.raises(ClickUpError) as exc:
        await ClickUpClient("expired").get_authorized_user()
    assert exc.value.status == 401


# ── upload_attachment: multipart body ────────────────────────────────────


async def test_upload_attachment_sends_multipart(respx_mock: respx.Router) -> None:
    route = respx_mock.post(f"{CLICKUP_BASE}/task/task1/attachment").mock(
        return_value=httpx.Response(200, json={"id": "att1", "title": "deck.pdf"})
    )
    result = await ClickUpClient("tok").upload_attachment(
        "task1", filename="deck.pdf", content=b"hello pdf bytes", mime_type="application/pdf"
    )
    assert result["id"] == "att1"
    # Multipart Content-Type with boundary
    ct = route.calls.last.request.headers["Content-Type"]
    assert ct.startswith("multipart/form-data; boundary=")
    # The filename should appear in the body
    body = route.calls.last.request.read()
    assert b"deck.pdf" in body
    assert b"hello pdf bytes" in body


async def test_upload_attachment_4xx_raises(respx_mock: respx.Router) -> None:
    respx_mock.post(f"{CLICKUP_BASE}/task/task1/attachment").mock(
        return_value=httpx.Response(413, text="file too large")
    )
    with pytest.raises(ClickUpError) as exc:
        await ClickUpClient("tok").upload_attachment(
            "task1", filename="big.bin", content=b"x" * 100, mime_type="application/octet-stream"
        )
    assert exc.value.status == 413
