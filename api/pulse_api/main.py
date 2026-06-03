from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.config import settings
from pulse_api.db import get_session
from pulse_api.mcp.server import lifespan as mcp_lifespan
from pulse_api.mcp.server import mcp_app
from pulse_api.observability import (
    configure_logging,
    configure_sentry,
    limiter,
    log_unhandled_exceptions,
    rate_limit_exceeded_handler,
)
from pulse_api.routes import admin_api as admin_api_routes
from pulse_api.routes import attachments as attachments_routes
from pulse_api.routes import auth as auth_routes
from pulse_api.routes import client_api as client_api_routes
from pulse_api.routes import oauth as oauth_routes
from pulse_api.routes import uploads as uploads_routes

configure_logging()
configure_sentry()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run the MCP session manager alongside the rest of the app.

    FastMCP's streamable HTTP transport needs its `StreamableHTTPSessionManager`
    running inside an anyio task group for the lifetime of the host app —
    otherwise tool calls hang waiting for a stream that nobody is feeding.
    Mounting `mcp_app` as a sub-app is not enough on its own; the lifespan
    of the parent app (this one) is what actually drives the worker.
    """
    async with mcp_lifespan(_app):
        yield


app = FastAPI(title="Pulse API", version="0.1.0", lifespan=lifespan)

# Rate limiter: applies the default limit to every route, and tighter
# per-route limits via `@limiter.limit(...)` on specific handlers.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# 5xx + unhandled-exception logger. Runs before CORS so even errors
# inside the CORS layer get recorded.
app.middleware("http")(log_unhandled_exceptions)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_allowed_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Pulse-Token", "Authorization"],
)

app.include_router(auth_routes.router)
app.include_router(oauth_routes.router)
app.include_router(client_api_routes.router)
app.include_router(uploads_routes.router)
app.include_router(admin_api_routes.router)
app.include_router(attachments_routes.admin_router)
app.include_router(attachments_routes.public_router)

# MCP server (streamable HTTP transport) — single endpoint at /api/mcp.
# FastMCP's `streamable_http_path` is set to "/" in `pulse_api.mcp.server`
# so the sub-app's only route resolves at the mount point, not at
# `/api/mcp/mcp`. nginx already proxies /api/* to FastAPI in prod, so
# no separate proxy rule is needed.
app.mount("/api/mcp", mcp_app)


@app.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("select 1"))
    return {"status": "ok"}
