from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.config import settings
from pulse_api.db import get_session
from pulse_api.observability import (
    configure_logging,
    configure_sentry,
    limiter,
    log_unhandled_exceptions,
    rate_limit_exceeded_handler,
)
from pulse_api.routes import admin_api as admin_api_routes
from pulse_api.routes import auth as auth_routes
from pulse_api.routes import client_api as client_api_routes
from pulse_api.routes import clickup as clickup_routes
from pulse_api.routes import oauth as oauth_routes
from pulse_api.routes import uploads as uploads_routes

configure_logging()
configure_sentry()

app = FastAPI(title="Pulse API", version="0.1.0")

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
    allow_headers=["Content-Type", "X-Pulse-Token"],
)

app.include_router(auth_routes.router)
# clickup_routes registers static paths under /api/auth/clickup/* which
# would otherwise be consumed by oauth_routes' parameterized
# /{provider}/authorize. Register clickup FIRST so its more-specific
# static path matches before the generic provider pattern.
app.include_router(clickup_routes.router)
app.include_router(oauth_routes.router)
app.include_router(client_api_routes.router)
app.include_router(uploads_routes.router)
app.include_router(admin_api_routes.router)


@app.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("select 1"))
    return {"status": "ok"}
