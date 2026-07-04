"""FastMCP server + resource-server auth wiring.

The MCP server runs as a Starlette sub-app mounted under `/api/mcp` on
the main FastAPI app, in OAuth 2.1 **resource-server mode**: the
``FastMCP(...)`` constructor receives ``token_verifier`` +
``auth=AuthSettings(...)`` (but NOT ``auth_server_provider`` — the
authorization-server routes are mounted separately at the domain root in
``pulse_api.mcp.oauth.routes``). That makes ``streamable_http_app()``:

  • wrap the endpoint in ``RequireAuthMiddleware`` — an unauthenticated
    request gets an HTTP ``401`` with a ``WWW-Authenticate`` header whose
    ``resource_metadata`` URL points at the absolute root PRM document
    (``/.well-known/oauth-protected-resource/api/mcp``);
  • install ``BearerAuthBackend`` + ``AuthContextMiddleware`` so a valid
    bearer token is validated ONCE at the HTTP layer (via ``verifier``)
    and exposed to tool handlers as an ``AccessToken`` contextvar.

Two credential shapes converge in ``verifier`` (see
``pulse_api.mcp.oauth.verifier``): a legacy ``pulse_<key>`` API key and
an OAuth access token. Both yield an ``AccessToken`` carrying
``subject = user_id`` and ``claims["org_id"]`` — the verifier also
re-checks org membership on every request. Tools therefore no longer
re-parse the header; ``authenticate_request`` reads the already-validated
principal off the context.

The session-management lifespan that FastMCP needs is installed on the
main FastAPI app in `pulse_api.main` — we expose `mcp_app` (a Starlette
ASGI app) and `lifespan` (the FastMCP session manager runner) for that
hookup.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.config import settings as app_settings
from pulse_api.db import admin_engine, member_engine
from pulse_api.mcp.oauth.verifier import verifier
from pulse_api.models import User
from pulse_api.repos import users as users_repo

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
    """Open a short-lived admin-engine session.

    Used by ``authenticate_request`` to load the ``User`` row for the
    already-validated access token. (Tool bodies open member-scoped
    sessions via ``_open_member_session(org_id)`` for their own
    queries.) Tests monkeypatch this to redirect through the test's
    rolled-back connection (see ``tests/test_mcp.py``); production talks
    to the admin pool.
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


async def authenticate_request(ctx: Context) -> tuple[User, str]:
    """Resolve the calling MCP request to ``(User, org_id)``.

    By the time a tool body runs, ``RequireAuthMiddleware`` +
    ``BearerAuthBackend`` have already validated the bearer credential
    against ``verifier`` (which handles both legacy ``pulse_<key>`` API
    keys and OAuth access tokens, AND re-checks org membership). The
    validated principal is stashed in a contextvar we read here via
    ``get_access_token()`` — there is no header re-parse, and no second
    membership check (the verifier already did it).

    The token's ``subject`` is the user id and ``claims["org_id"]`` is
    the granting org; each tool opens a member-scoped session against
    that org id, regardless of whether the credential was a key or an
    OAuth grant.

    Args:
        ctx: The FastMCP tool-call context (unused — kept for signature
            stability and because the contextvar is request-scoped).

    Returns:
        A ``(User, org_id)`` tuple. ``org_id`` is a UUID string.

    Raises:
        MCPAuthError: If no validated access token is present, the token
            carries no ``org_id`` claim, or the user row has vanished.
    """
    access = get_access_token()
    if access is None:
        # Reachable only if a tool runs outside RequireAuthMiddleware
        # (e.g. a future unauthenticated route); the middleware otherwise
        # 401s before any tool body executes.
        raise MCPAuthError("missing or invalid credential")

    org_id = (access.claims or {}).get("org_id")
    if not org_id or access.subject is None:
        raise MCPAuthError("token is missing org context")

    async with _open_admin_session() as session:
        user = await users_repo.get_user_by_id(session, access.subject)
    if user is None:
        raise MCPAuthError("authenticated user no longer exists")

    return user, str(org_id)


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
    # Resource-server mode: validate every bearer token through `verifier`
    # (API key OR OAuth grant). Passing `auth` (but NOT auth_server_provider)
    # makes streamable_http_app() wrap the endpoint in RequireAuthMiddleware
    # (401 + WWW-Authenticate pointing at the root PRM doc) and install the
    # bearer/auth-context middleware. The authorization-server routes
    # (/authorize, /token, /register, /revoke + AS metadata) are mounted at
    # the domain root separately (see pulse_api.mcp.oauth.routes), so they
    # don't mis-nest under /api/mcp.
    token_verifier=verifier,
    auth=AuthSettings(
        # AnyHttpUrl fields — pydantic coerces the plain strings.
        issuer_url=app_settings.mcp_issuer_base,
        resource_server_url=app_settings.mcp_resource_url,
        required_scopes=["mcp"],
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(),
        # Origin is only checked when present. MCP clients (Claude Code,
        # the SDK's streamable_http_client) don't send Origin by default,
        # so an empty allowlist is fine — bearer auth is the real gate.
        # Browser-MCP clients can add their domain here if needed.
        allowed_origins=[],
    ),
)


# Tool registrations live in mcp.tools — importing it has the side effect
# of calling `@mcp.tool(...)` on each one. Each tool still calls
# `authenticate_request(ctx)` to resolve the org from the validated token,
# but the real HTTP gate is now `RequireAuthMiddleware`: under RS mode the
# ENTIRE streamable endpoint — including `tools/list` — requires a valid
# bearer token, so an unauthenticated request 401s with the OAuth
# discovery challenge. That is the correct shape for the OAuth connector
# flow (clients complete authorization, THEN list tools) and does not
# break legacy `pulse_<key>` callers, which always send their credential.
from pulse_api.mcp import tools as _tools  # noqa: E402, F401

mcp_app: Starlette = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_app) -> AsyncIterator[None]:  # type: ignore[no-untyped-def]
    """Run FastMCP's session manager for the lifetime of the host app.

    The main FastAPI app installs this as its lifespan so the MCP
    session manager's `anyio` task group is alive while requests flow.
    """
    async with mcp.session_manager.run():
        yield
