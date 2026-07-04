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
    """Resolve the caller's IP for rate-limiting keys, trusting only what
    nginx itself sets.

    Order of preference:
    1. ``X-Real-IP`` — nginx sets this itself (see deploy/) from its own
       view of the connecting peer; the client cannot forge it because
       nginx overwrites the header on every request, it doesn't append.
    2. The LAST hop of ``X-Forwarded-For`` — nginx appends via
       ``$proxy_add_x_forwarded_for``, so the last entry is the address
       nginx observed directly. The client fully controls every entry to
       its *left* (it can prepend arbitrary "X-Forwarded-For: 1.2.3.4"
       before nginx appends its own hop), so trusting the leftmost entry
       let a client pick a fresh rate-limit bucket on every request. Only
       the rightmost (closest, trusted) hop is safe to key on.
    3. The direct peer address, then ``"unknown"`` if nothing is present.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


# One process-global Limiter so its in-memory store survives between requests
# in the same worker. (In a multi-worker deploy, per-worker buckets are
# accepted as a known limitation — for the real thing, swap to Redis-backed
# storage via the `storage_uri` arg.)
limiter = Limiter(
    key_func=_client_ip,
    default_limits=[settings.rate_limit_default],
)


def _patch_slowapi_route_handler_lookup() -> None:
    """Work around a slowapi/MCP-SDK interaction that would otherwise
    crash (or worse, spuriously 429) every request to our root-mounted
    OAuth routes once `SlowAPIMiddleware` is registered app-wide.

    `oauth_root_routes()` (mcp/oauth/routes.py) hands back raw Starlette
    `Route` objects built by the `mcp` SDK's `create_auth_routes` /
    `create_protected_resource_routes`. Several of those routes'
    `endpoint` is a `CORSMiddleware`-wrapped ASGI callable, not a plain
    Python function — it has no `__name__`. slowapi's
    `SlowAPIMiddleware.dispatch` unconditionally builds
    `f"{handler.__module__}.{handler.__name__}"` (in `_get_route_name`,
    called from `_should_exempt`) to decide per-route exemptions, which
    raises `AttributeError` for those routes. That exception isn't just
    an uncaught 500: slowapi's own `_check_limits` wraps the *next* call
    site in a bare `except Exception` and maps whatever it catches to
    the rate-limit-exceeded handler, so left unpatched every request to
    these routes would 429 unconditionally instead of 500ing loudly.

    Patch `_find_route_handler` (the one place a handler is resolved for
    a request) to return `None` for any endpoint lacking `__name__` —
    the same effective behavior as before this middleware existed:
    those specific OAuth-root routes are unthrottled, but they no longer
    crash or get spuriously rate-limited. Every other route in the app
    is a normal `async def` FastAPI handler and is unaffected.
    """
    import slowapi.middleware as _slowapi_middleware

    if getattr(_slowapi_middleware._find_route_handler, "_pulse_patched", False):
        return  # idempotent — safe to call from a re-imported module

    _original_find_route_handler = _slowapi_middleware._find_route_handler

    def _safe_find_route_handler(routes: Any, scope: Any) -> Any:
        handler = _original_find_route_handler(routes, scope)
        if handler is not None and not hasattr(handler, "__name__"):
            return None
        return handler

    _safe_find_route_handler._pulse_patched = True  # type: ignore[attr-defined]
    _slowapi_middleware._find_route_handler = _safe_find_route_handler


_patch_slowapi_route_handler_lookup()


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
