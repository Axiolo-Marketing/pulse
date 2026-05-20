"""ClickUp API v2 client.

A thin httpx wrapper. The route layer composes calls into push / webhook
flows; this module's job is to map our pythonic kwargs to ClickUp's wire
format and to surface failures as a single typed exception.

All methods are async. All non-2xx responses raise `ClickUpError` (which
the routes map to either 502 — upstream problem — or surface as part of
a partial-failure response).

Auth model: a `ClickUpClient` is constructed with a personal-access-style
token. For OAuth-issued tokens, the value is the OAuth access_token
itself; ClickUp accepts both in the `Authorization` header without a
`Bearer` prefix.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

CLICKUP_BASE = "https://api.clickup.com/api/v2"
CLICKUP_AUTHORIZE = "https://app.clickup.com/api"


class ClickUpError(Exception):
    """Raised when a ClickUp API call returns non-2xx, OR when an
    expected field is missing from the response body."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"clickup {status}: {detail[:200]}")
        self.status = status
        self.detail = detail


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """The URL the operator is redirected to for OAuth consent."""
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "state": state}
    return f"{CLICKUP_AUTHORIZE}?{urlencode(params)}"


async def exchange_code(
    *, client_id: str, client_secret: str, code: str
) -> str:
    """Trade the OAuth `code` for an access_token. Returns the token string."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        r = await http.post(
            f"{CLICKUP_BASE}/oauth/token",
            data={"client_id": client_id, "client_secret": client_secret, "code": code},
        )
    if r.status_code >= 400:
        raise ClickUpError(r.status_code, f"oauth/token failed: {r.text}")
    body = r.json()
    token = body.get("access_token")
    if not token:
        raise ClickUpError(r.status_code, f"oauth/token returned no access_token: {body}")
    return token


class ClickUpClient:
    """Authenticated wrapper. Construct once per request handler with the
    operator's access token, then call methods. Always uses a fresh
    `httpx.AsyncClient` per call — at this volume per-call cost is
    negligible and the connection-pool semantics are simpler."""

    def __init__(self, access_token: str) -> None:
        self._token = access_token

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._token, "Content-Type": "application/json"}

    async def _request(
        self, method: str, path: str, *, json: Any = None
    ) -> dict:
        async with httpx.AsyncClient(timeout=20.0) as http:
            r = await http.request(
                method, f"{CLICKUP_BASE}{path}", headers=self._headers, json=json
            )
        if r.status_code >= 400:
            raise ClickUpError(r.status_code, r.text)
        return r.json() if r.content else {}

    async def get_authorized_user(self) -> dict:
        body = await self._request("GET", "/user")
        return body.get("user") or body

    async def get_authorized_teams(self) -> list[dict]:
        """ClickUp calls workspaces "teams" in the API. Returns each
        team's id and name."""
        body = await self._request("GET", "/team")
        return body.get("teams") or []

    async def get_list(self, list_id: str) -> dict:
        return await self._request("GET", f"/list/{list_id}")

    async def create_task(self, list_id: str, payload: dict) -> dict:
        return await self._request("POST", f"/list/{list_id}/task", json=payload)

    async def update_task(self, task_id: str, payload: dict) -> dict:
        return await self._request("PUT", f"/task/{task_id}", json=payload)

    async def create_webhook(
        self, team_id: str, *, endpoint: str, events: list[str]
    ) -> dict:
        """Subscribe to events for a workspace. Returns the webhook id +
        the secret ClickUp will use to sign payloads (X-Signature header)."""
        body = await self._request(
            "POST",
            f"/team/{team_id}/webhook",
            json={"endpoint": endpoint, "events": events},
        )
        return body.get("webhook") or body

    async def delete_webhook(self, webhook_id: str) -> None:
        await self._request("DELETE", f"/webhook/{webhook_id}")

    async def upload_attachment(
        self, task_id: str, *, filename: str, content: bytes, mime_type: str
    ) -> dict:
        """Multipart attachment upload. Uses a separate code path because
        the rest of the API is JSON. ClickUp expects the file under field
        name `attachment`."""
        async with httpx.AsyncClient(timeout=60.0) as http:
            r = await http.post(
                f"{CLICKUP_BASE}/task/{task_id}/attachment",
                headers={"Authorization": self._token},  # NO Content-Type — httpx sets boundary
                files={"attachment": (filename, content, mime_type)},
            )
        if r.status_code >= 400:
            raise ClickUpError(r.status_code, r.text)
        return r.json() if r.content else {}
