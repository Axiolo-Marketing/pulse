"""Smoke test the /healthz endpoint through the AsyncClient + override wiring.

If this fails, the test scaffolding is broken — not the app code.
"""
from httpx import AsyncClient

from pulse_api.config import settings


async def test_healthz_returns_ok(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_rest_and_mcp_share_one_verify_bearer() -> None:
    """Both the REST middleware and the MCP server must funnel bearer
    validation through `auth.api_keys.verify_bearer` — otherwise harden
    one and the other quietly stays vulnerable.

    The REST path exposes `_user_from_bearer` (which delegates), and the
    MCP path's `authenticate_request` reads `api_keys_lib.verify_bearer`
    by module attribute access. Importing `api_keys_lib` from both modules
    must yield the same module object, and the function must be the same
    callable.
    """
    from pulse_api.auth import api_keys as api_keys_lib
    from pulse_api.auth import middleware as mw
    from pulse_api.mcp import server as mcp_server

    assert mw.api_keys_lib is api_keys_lib
    assert mcp_server.api_keys_lib is api_keys_lib
    assert mw.api_keys_lib.verify_bearer is api_keys_lib.verify_bearer
    assert mcp_server.api_keys_lib.verify_bearer is api_keys_lib.verify_bearer


async def test_cors_preflight_allows_authorization_header(
    client: AsyncClient,
) -> None:
    """Browser-based MCP clients send `Authorization: Bearer ...`. Without
    `Authorization` in the CORS allow-list, preflight strips the header and
    the bearer token never reaches FastAPI. Lock down the allow-list shape
    so this can't quietly regress.
    """
    r = await client.options(
        "/api/mcp/",
        headers={
            "Origin": settings.cors_allowed_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert r.status_code == 200
    allowed = r.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allowed
    assert "content-type" in allowed
    assert "x-pulse-token" in allowed
