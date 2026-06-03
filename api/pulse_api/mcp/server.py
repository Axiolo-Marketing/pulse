"""FastMCP server + bearer-auth wiring.

The MCP server runs as a Starlette sub-app mounted under `/api/mcp` on
the main FastAPI app. Auth is mandatory on every tool call:

  • Each tool is decorated with `@mcp.tool()` and takes a `Context`
    parameter. Inside the tool, the first thing we do is call
    `authenticate_request(ctx)` which:
      1. Pulls the `Authorization: Bearer pulse_<key>` header off the
         underlying Starlette request the MCP transport exposes via
         `ctx.request_context.request`.
      2. Resolves the bearer string to a `User` row using the SAME
         primitives as the REST middleware (`api_keys_repo.get_by_prefix`
         + `api_keys_lib.hash_key` + `hmac.compare_digest`).
      3. Updates `last_used_at` on the key best-effort, identical to
         the REST path.
      4. Refuses non-admin keys with an MCP-shaped error.
  • Missing or malformed `Authorization` → `MCPAuthError("missing")`.
  • Unknown / wrong-hash / revoked key → `MCPAuthError("invalid")`.
  • Valid key on a non-admin user → `MCPAuthError("forbidden")`.

The session-management lifespan that FastMCP needs is installed on the
main FastAPI app in `pulse_api.main` — we expose `mcp_app` (a Starlette
ASGI app) and `lifespan` (the FastMCP session manager runner) for that
hookup.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth import api_keys as api_keys_lib
from pulse_api.config import settings as app_settings
from pulse_api.db import admin_engine
from pulse_api.models import User

if TYPE_CHECKING:
    from starlette.applications import Starlette


class MCPAuthError(Exception):
    """Authentication failure raised from inside a tool.

    FastMCP converts any exception raised inside a tool into a tool-error
    response with the exception's message. That's the shape an MCP client
    expects for "you can't call this tool" — the JSON-RPC envelope still
    succeeds; the tool result carries `isError=True`.
    """


# ── DB session factory (override-friendly for tests) ──────────────────────


@asynccontextmanager
async def _open_admin_session() -> AsyncIterator[AsyncSession]:
    """Open a short-lived admin-engine session for a tool call.

    Tools that mutate state call `session.commit()` themselves — this is
    just the context manager that wraps create/close. Tests monkeypatch
    this function to redirect through the test's rolled-back connection
    (see `tests/test_mcp.py`); production talks to the admin pool.
    """
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


# ── Authentication ────────────────────────────────────────────────────────


async def authenticate_request(ctx: Context) -> User:
    """Resolve the calling MCP request to an admin User row.

    Delegates the actual bearer-string → User resolution to
    `auth.api_keys.verify_bearer` — the single source of truth shared
    with the REST middleware. The MCP path adds two MCP-specific things:

      1. Extracting the `Authorization` header off the Starlette request
         that FastMCP exposes via `ctx.request_context.request`.
      2. The `is_admin` gate (MCP is admin-only). The REST layer enforces
         this separately via the `get_current_admin` dependency; for MCP
         we inline it here so a non-admin key produces the MCP-shaped
         tool-error rather than an HTTP 403.

    Raises `MCPAuthError` for any failure mode. FastMCP turns that into
    a tool-error response the client surfaces to the model.
    """
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        raise MCPAuthError("missing Authorization header")

    authorization = request.headers.get("authorization") if hasattr(request, "headers") else None
    if not authorization:
        raise MCPAuthError("missing Authorization header")

    async with _open_admin_session() as session:
        user = await api_keys_lib.verify_bearer(authorization, session)
        if user is None:
            raise MCPAuthError("invalid API key")
        if not user.is_admin:
            raise MCPAuthError("admin only")

    return user


# ── FastMCP instance ──────────────────────────────────────────────────────

# Stateless HTTP + JSON responses: no session continuation between tool
# calls (every call re-authenticates), and the response is a single
# JSON-RPC object instead of an SSE stream. Matches the call-and-return
# shape of every tool below — there's nothing to stream.
# DNS-rebinding protection. FastMCP refuses requests whose Host header
# isn't in this list (default is empty → everything rejected). We accept:
#   • localhost variants for local dev (Docker maps 58000)
#   • the frontend's canonical host derived from frontend_base_url
#   • the test fake host "test" used by httpx ASGITransport
# Production deploys override allowed hosts via env if needed.
def _allowed_hosts() -> list[str]:
    hosts: list[str] = [
        "localhost:*",
        "127.0.0.1:*",
        "0.0.0.0:*",
        "backend:*",  # in-cluster docker DNS
        "test",       # httpx ASGITransport's fake host in tests
    ]
    base = app_settings.frontend_base_url
    if base:
        # Pull the host out of frontend_base_url so the in-cluster proxy
        # path works without env overrides.
        from urllib.parse import urlparse
        parsed = urlparse(base)
        if parsed.hostname:
            hosts.append(parsed.hostname)
            if parsed.port:
                hosts.append(f"{parsed.hostname}:{parsed.port}")
            else:
                hosts.append(f"{parsed.hostname}:*")
    return hosts


mcp = FastMCP(
    "pulse",
    instructions=(
        "Pulse engagement-management tools. Drive client engagements, "
        "decks, and attachments via the same admin surface the web UI uses."
    ),
    stateless_http=True,
    json_response=True,
    # FastMCP defaults its route to "/mcp" inside the streamable_http_app.
    # We mount the whole sub-app at "/api/mcp" so collapsing this to "/"
    # gives the final URL `/api/mcp` (not `/api/mcp/mcp`). One endpoint,
    # no version suffix — matches the plan.
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(),
        # Origin is only checked when present. MCP clients (Claude Code,
        # the SDK's streamable_http_client) don't send Origin by default,
        # so an empty allowlist is fine — bearer-key auth is the real
        # gate. Browser-MCP clients can add their domain here if needed.
        allowed_origins=[],
    ),
)


# Tool registrations live in mcp.tools — importing it has the side effect
# of calling `@mcp.tool(...)` on each one. Each tool calls
# `authenticate_request(ctx)` before doing any work, so the per-tool gate
# is at the tool layer. `tools/list` (enumeration) stays unauthenticated
# on purpose: tool names + descriptions are documentation, not secrets,
# and gating enumeration would break the discovery shape every standard
# MCP client expects.
from pulse_api.mcp import tools as _tools  # noqa: E402, F401

mcp_app: "Starlette" = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_app) -> AsyncIterator[None]:  # type: ignore[no-untyped-def]
    """Run FastMCP's session manager for the lifetime of the host app.

    The main FastAPI app installs this as its lifespan so the MCP
    session manager's `anyio` task group is alive while requests flow.
    """
    async with mcp.session_manager.run():
        yield
