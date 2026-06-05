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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.auth import api_keys as api_keys_lib
from pulse_api.config import settings as app_settings
from pulse_api.db import admin_engine, member_engine
from pulse_api.models import ApiKey, User

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

    PR 2 swap: ``authenticate_request`` now returns ``(user, api_key)``
    and tool handlers open a member-scoped session via
    ``_open_member_session(api_key.org_id)`` instead. ``_open_admin_session``
    is retained for the auth path (looking up the api key row itself)
    and for tests that monkeypatch the session factory.
    """
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


@asynccontextmanager
async def _open_member_session(org_id: str) -> AsyncIterator[AsyncSession]:
    """Open a short-lived ``pulse_member`` session with ``pulse.org_id`` set.

    Mirrors the REST ``get_org_scoped_session`` dep — the role has no
    BYPASSRLS and the GUC is set per request. A tool handler that
    forgets a ``where org_id = ...`` therefore cannot leak across
    tenants; Postgres refuses the row.

    Tests monkeypatch this to bind through the rolled-back test
    connection.
    """
    async with member_engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(
                text("select set_config('pulse.org_id', :org_id, true)"),
                {"org_id": str(org_id)},
            )
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
                yield session
            await trans.commit()
        except Exception:
            await trans.rollback()
            raise


# ── Authentication ────────────────────────────────────────────────────────


async def authenticate_request(ctx: Context) -> tuple[User, ApiKey]:
    """Resolve the calling MCP request to (User, ApiKey).

    Returns the ApiKey alongside the user so each tool handler can pull
    ``api_key.org_id`` and open a member-scoped session against the
    right tenant — the key, not the cookie (MCP has no cookies anyway),
    determines org context.

    Delegates the actual bearer-string → (User, ApiKey) resolution to
    ``auth.api_keys.verify_bearer`` — the single source of truth shared
    with the REST middleware. The MCP path adds two MCP-specific things:

      1. Extracting the ``Authorization`` header off the Starlette
         request that FastMCP exposes via ``ctx.request_context.request``.
      2. Membership-existence check on the resolved ``(user, org_id)``
         pair — a key whose owning user was removed from the org should
         stop working immediately (the REST layer reaches the same
         outcome through ``get_current_org_member``).

    Raises ``MCPAuthError`` for any failure mode. FastMCP turns that
    into a tool-error response the client surfaces to the model.
    """
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        raise MCPAuthError("missing Authorization header")

    authorization = request.headers.get("authorization") if hasattr(request, "headers") else None
    if not authorization:
        raise MCPAuthError("missing Authorization header")

    async with _open_admin_session() as session:
        resolved = await api_keys_lib.verify_bearer(authorization, session)
        if resolved is None:
            raise MCPAuthError("invalid API key")
        user, api_key = resolved
        # Membership existence check — keys outlive memberships only as
        # tombstones; an active key whose user lost their seat must
        # stop working immediately.
        result = await session.execute(
            text(
                "select 1 from public.organization_memberships "
                "where user_id = cast(:u as uuid) "
                "  and org_id  = cast(:o as uuid) limit 1"
            ),
            {"u": str(user.id), "o": str(api_key.org_id)},
        )
        if result.scalar() is None:
            raise MCPAuthError("user is not a member of the key's organization")

    return user, api_key


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
