"""Smoke test the /healthz endpoint through the AsyncClient + override wiring.

If this fails, the test scaffolding is broken — not the app code.
"""
from httpx import AsyncClient


async def test_healthz_returns_ok(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
