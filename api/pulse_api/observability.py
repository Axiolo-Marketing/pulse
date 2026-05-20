"""Logging + Sentry + rate-limiter wiring.

All initialization is idempotent and safe to call multiple times — tests
re-import the app module without re-bootstrapping the world.

Why this lives in its own module:
- `main.py` should be a thin app composition, not the place where
  side-effect-having global state (loggers, Sentry SDK) gets configured.
- Tests can swap pieces without touching the route layer.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse

from pulse_api.config import settings


# ── Structured logging ────────────────────────────────────────────────────


def configure_logging() -> None:
    """Configure structlog → stdout JSON. Idempotent."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# A single shared logger for app code — bind context per-call via .bind().
log = structlog.get_logger("pulse")


# ── Sentry ────────────────────────────────────────────────────────────────


def configure_sentry() -> None:
    """Initialize Sentry if SENTRY_DSN is set. No-op otherwise."""
    if not settings.sentry_dsn:
        return
    # Imported lazily so a missing DSN doesn't pay the import cost (and
    # the test environment doesn't need the SDK on PATH).
    import sentry_sdk
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,
        send_default_pii=False,  # email + cookies stay local
        integrations=[StarletteIntegration()],
    )


# ── Rate limiter ──────────────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    """Use the first X-Forwarded-For entry if nginx is in front; fall back
    to the direct peer address. nginx is configured (see deploy/) to set
    X-Forwarded-For; if anything else is fronting us it should too."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # The leftmost entry is the original client per RFC 7239.
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# One process-global Limiter so its in-memory store survives between requests
# in the same worker. (In a multi-worker deploy, per-worker buckets are
# accepted as a known limitation — for the real thing, swap to Redis-backed
# storage via the `storage_uri` arg.)
limiter = Limiter(
    key_func=_client_ip,
    default_limits=[settings.rate_limit_default],
)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Map slowapi's exception to a 429 with a clear retry-after header."""
    log.warning(
        "rate_limit.exceeded",
        path=request.url.path,
        client=_client_ip(request),
        limit=str(exc.limit.limit),
    )
    response = JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please slow down."},
    )
    response.headers["Retry-After"] = str(exc.limit.limit.get_expiry())
    return response


# ── 5xx + auth-failure logging middleware ────────────────────────────────


async def log_unhandled_exceptions(request: Request, call_next: Any) -> Any:
    """Log every 5xx response with the route + method.

    Doesn't suppress the exception — just records it before FastAPI's
    default handler converts it to a 500. Sentry (if configured) will
    also see the exception via its Starlette integration.
    """
    try:
        response = await call_next(request)
    except Exception:
        log.exception("route.5xx", method=request.method, path=request.url.path)
        raise

    if response.status_code >= 500:
        log.error(
            "route.5xx",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        )
    return response
