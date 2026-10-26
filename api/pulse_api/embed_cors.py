"""CORS for the embed API only.

Client documents live on other origins (QuickHost pages), so their
``pulse-blocks.js`` calls ``/api/embed/*`` cross-origin with the respondent's
``X-Pulse-Token``. This middleware answers those requests for the origins in
``settings.embed_allowed_origins`` and nothing else: no other path, no
cookies (``Allow-Credentials`` is never sent), so an approved page can't
reach the cookie-authed admin API.

Origin patterns are exact (``https://pages.example.com``) or a subdomain
wildcard (``https://*.quickhost.ing``).

Added outermost so its preflight answer wins over the app-wide
``CORSMiddleware`` (which only knows the admin frontend's origin).
"""
from __future__ import annotations

from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

PREFIX = "/api/embed/"
_ALLOW_HEADERS = "Content-Type, X-Pulse-Token"
_ALLOW_METHODS = "GET, POST"


def origin_allowed(origin: str, patterns: list[str]) -> bool:
    for p in patterns:
        if origin == p:
            return True
        if "://*." in p:
            scheme, host = p.split("://*.", 1)
            if origin.startswith(f"{scheme}://") and origin.endswith(f".{host}"):
                sub = origin[len(scheme) + 3 : -len(host) - 1]
                if sub and "/" not in sub and ":" not in sub:
                    return True
    return False


class EmbedCORSMiddleware:
    def __init__(self, app: ASGIApp, origins: Callable[[], list[str]]) -> None:
        self.app = app
        self.origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PREFIX):
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        origin = headers.get(b"origin", b"").decode()
        if not origin or not origin_allowed(origin, self.origins()):
            await self.app(scope, receive, send)
            return
        cors = [
            (b"access-control-allow-origin", origin.encode()),
            (b"vary", b"Origin"),
        ]
        if scope["method"] == "OPTIONS" and b"access-control-request-method" in headers:
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": cors
                    + [
                        (b"access-control-allow-methods", _ALLOW_METHODS.encode()),
                        (b"access-control-allow-headers", _ALLOW_HEADERS.encode()),
                        (b"access-control-max-age", b"600"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if not k.lower().startswith(b"access-control-") and k.lower() != b"vary"
                ]
                message = {**message, "headers": kept + cors}
            await send(message)

        await self.app(scope, receive, send_with_cors)
