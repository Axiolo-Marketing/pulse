"""Root-mounted OAuth 2.1 authorization-server routes + consent page.

Pulse is its own identity provider for the MCP endpoint. The
authorization-server surface (``/authorize`` ``/token`` ``/register``
``/revoke`` + AS metadata) and the RFC 9728 protected-resource-metadata
(PRM) document live at the **domain root**, NOT under ``/api/mcp`` — the
SDK builds those routes relative to the issuer, and the issuer is the
root origin. Mounting them under the MCP sub-app would mis-nest the PRM
to ``/api/mcp/.well-known/...`` and break discovery.

``oauth_root_routes()`` returns the full Starlette ``Route`` list the
main app registers on its own router:

  * ``create_auth_routes(...)`` — AS metadata + authorize/token/register
    /revoke, all driven by the PR-1 ``provider`` singleton.
  * ``create_protected_resource_routes(...)`` — the root PRM doc at
    ``/.well-known/oauth-protected-resource/api/mcp``, whose absolute URL
    is exactly what the MCP endpoint's ``WWW-Authenticate`` header points
    at.
  * the Pulse consent page (``GET``/``POST /authorize/consent``).

The consent page is server-rendered, Pulse-branded HTML with no client
JS (per the "no window.alert/confirm" rule — errors render inline). It
reads the existing ``pulse_session`` cookie to identify the operator,
lists their org memberships for the picker, and on Approve mints the
authorization code bound to ``(user, chosen org, client, redirect_uri,
PKCE challenge, resource)`` then 302s back to the client's redirect URI.

These routes run on the ROOT app, outside the member-scoped DI graph, so
they open their own ``pulse_admin`` session via ``_admin_session`` (the
same pattern as the provider). Tests monkeypatch that to bind through the
rolled-back connection.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from html import escape
from urllib.parse import quote, urlencode

from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.auth.routes import (
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from pulse_api.auth.session import InvalidSessionError, decode_session_payload
from pulse_api.config import settings
from pulse_api.db import admin_engine
from pulse_api.mcp.oauth import tokens as oauth_tokens
from pulse_api.mcp.oauth.provider import provider as oauth_provider
from pulse_api.models._helpers import utcnow_naive
from pulse_api.repos import oauth as oauth_repo
from pulse_api.repos import orgs as orgs_repo
from pulse_api.repos import users as users_repo

# Authorization codes are short-lived — they exist only long enough for
# the client to immediately exchange them at the token endpoint.
AUTH_CODE_TTL_SECONDS = 300

# Single scope description shown on the consent screen.
_SCOPE_DESCRIPTION = "manage your Pulse engagements (the same admin surface the web UI uses)"

_PRIMARY = "#2960F6"


@asynccontextmanager
async def _admin_session() -> AsyncIterator[AsyncSession]:
    """Open a short-lived ``pulse_admin`` session for the consent routes.

    The consent page runs on the root app, outside FastAPI's member DI,
    so it owns its own session lifecycle (same as the provider). Tests
    monkeypatch this to bind through the rolled-back test connection.

    Yields:
        An ``AsyncSession`` bound to ``admin_engine``.
    """
    async with AsyncSession(admin_engine, expire_on_commit=False) as session:
        yield session


# ── consent page rendering ────────────────────────────────────────────────


def _consent_page_html(
    *,
    blob: str,
    client_name: str,
    orgs: list[dict[str, object]],
    error: str | None = None,
) -> str:
    """Render the Pulse-branded consent page.

    Args:
        blob: The signed authorization-request blob, carried in a hidden
            field so the POST can re-decode it.
        client_name: Human-readable client name (falls back to the
            client id at the call site).
        orgs: The operator's org memberships
            (``{"id", "name", ...}`` dicts).
        error: Optional inline error message to surface above the form.

    Returns:
        A complete HTML document string.
    """
    if len(orgs) == 1:
        org = orgs[0]
        org_picker = (
            f'<input type="hidden" name="org_id" value="{escape(str(org["id"]))}">'
            f'<p class="org-fixed">Organization: '
            f'<strong>{escape(str(org["name"]))}</strong></p>'
        )
    else:
        options = "".join(
            f'<option value="{escape(str(o["id"]))}">{escape(str(o["name"]))}</option>'
            for o in orgs
        )
        org_picker = (
            '<label class="field"><span>Organization</span>'
            f'<select name="org_id" required>{options}</select></label>'
        )

    error_html = (
        f'<div class="error" role="alert">{escape(error)}</div>' if error else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Authorize {escape(client_name)} · Pulse</title>
  <style>
    :root {{ --primary: {_PRIMARY}; }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
      background: #f4f6fb; color: #0c1b3a; margin: 0;
      min-height: 100vh; display: flex; align-items: center;
      justify-content: center; padding: 24px;
    }}
    .card {{
      background: #fff; border-radius: 16px; max-width: 420px; width: 100%;
      padding: 32px; box-shadow: 0 12px 40px rgba(12, 27, 58, 0.12);
    }}
    h1 {{ font-size: 20px; margin: 0 0 4px; }}
    .sub {{ color: #5a6b8c; font-size: 14px; margin: 0 0 20px; }}
    .scope {{
      background: #eef3ff; border-radius: 10px; padding: 14px 16px;
      font-size: 14px; margin: 0 0 20px;
    }}
    .field {{ display: block; margin: 0 0 20px; }}
    .field span {{
      display: block; font-size: 13px; font-weight: 600;
      margin-bottom: 6px; color: #34456b;
    }}
    select {{
      width: 100%; padding: 10px 12px; border: 1px solid #cdd6ea;
      border-radius: 8px; font-size: 15px; background: #fff;
    }}
    .org-fixed {{ font-size: 14px; margin: 0 0 20px; color: #34456b; }}
    .actions {{ display: flex; gap: 12px; }}
    button {{
      flex: 1; padding: 12px 16px; border-radius: 10px; font-size: 15px;
      font-weight: 600; cursor: pointer; border: 1px solid transparent;
    }}
    .approve {{ background: var(--primary); color: #fff; }}
    .deny {{ background: #fff; color: #34456b; border-color: #cdd6ea; }}
    .error {{
      background: #fff1f0; color: #a8071a; border-radius: 8px;
      padding: 10px 12px; font-size: 14px; margin: 0 0 16px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Authorize {escape(client_name)}</h1>
    <p class="sub">Connect this application to your Pulse account.</p>
    {error_html}
    <div class="scope">
      <strong>{escape(client_name)}</strong> is requesting permission to
      {escape(_SCOPE_DESCRIPTION)}.
    </div>
    <form method="post" action="/authorize/consent">
      <input type="hidden" name="request" value="{escape(blob)}">
      {org_picker}
      <div class="actions">
        <button class="deny" type="submit" name="decision" value="deny">
          Deny
        </button>
        <button class="approve" type="submit" name="decision" value="approve">
          Approve
        </button>
      </div>
    </form>
  </div>
</body>
</html>"""


# ── consent helpers ────────────────────────────────────────────────────────


def _login_redirect(request: Request, blob: str) -> RedirectResponse:
    """Build a 302 to the admin login carrying a same-origin ``return_to``.

    The frontend (``src/scripts/admin.ts``) honors ``return_to`` after a
    successful login and sends the browser back to this consent URL, so
    the operator lands straight back on Approve/Deny.

    Args:
        request: The inbound consent request.
        blob: The signed authorization-request blob to re-present.

    Returns:
        A ``RedirectResponse`` (302) to ``/admin/?return_to=...``.
    """
    consent_path = f"/authorize/consent?{urlencode({'request': blob})}"
    target = f"{settings.frontend_base_url}/admin/?return_to={quote(consent_path)}"
    return RedirectResponse(target, status_code=302)


def _session_user_id(request: Request) -> str | None:
    """Return the authenticated user id from the ``pulse_session`` cookie.

    Args:
        request: The inbound request.

    Returns:
        The user id string, or None if the cookie is missing / invalid /
        expired.
    """
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    try:
        payload = decode_session_payload(
            raw, settings.session_max_age_seconds
        )
    except InvalidSessionError:
        return None
    return payload.get("user_id")


def _decode_blob_or_400(blob: str | None) -> tuple[dict | None, Response | None]:
    """Decode the signed authz-request blob, or build a 400 response.

    Args:
        blob: The raw ``request`` query/form value (may be None).

    Returns:
        ``(payload, None)`` on success, or ``(None, response)`` with a
        400 ``Response`` to return to the caller.
    """
    if not blob:
        return None, Response("missing authorization request", status_code=400)
    try:
        return oauth_tokens.read_authz_request(blob), None
    except InvalidSessionError:
        return None, Response(
            "invalid or expired authorization request", status_code=400
        )


# ── consent handlers ────────────────────────────────────────────────────────


async def consent_get(request: Request) -> Response:
    """Render the consent page (or redirect to login if not signed in).

    Args:
        request: The inbound ``GET /authorize/consent`` request.

    Returns:
        An HTML consent page, a 302 to login, or a 400 on a bad blob.
    """
    blob = request.query_params.get("request")
    payload, err = _decode_blob_or_400(blob)
    if err is not None:
        return err

    user_id = _session_user_id(request)
    if user_id is None:
        return _login_redirect(request, blob)

    async with _admin_session() as session:
        orgs = await orgs_repo.list_orgs_for_user(session, user_id)

    if not orgs:
        # Signed in but belongs to no org — nothing to scope a grant to.
        return HTMLResponse(
            _consent_page_html(
                blob=blob,
                client_name=_client_label(payload),
                orgs=[],
                error=(
                    "Your account isn't a member of any organization yet. "
                    "Ask an org owner to invite you."
                ),
            ),
            status_code=403,
        )

    return HTMLResponse(
        _consent_page_html(
            blob=blob,
            client_name=_client_label(payload),
            orgs=orgs,
        )
    )


async def consent_post(request: Request) -> Response:
    """Process the Approve/Deny decision and complete (or refuse) consent.

    On Approve: verify the chosen org is one the signed-in user belongs
    to, mint a single-use authorization code bound to that org, and 302
    back to the client redirect URI with ``code`` + ``state`` + ``iss``.
    On Deny: 302 back with ``error=access_denied``.

    Args:
        request: The inbound ``POST /authorize/consent`` request.

    Returns:
        A 302 to the client redirect URI, a 302 to login, or a 400.
    """
    form = await request.form()
    blob = form.get("request")
    payload, err = _decode_blob_or_400(blob if isinstance(blob, str) else None)
    if err is not None:
        return err

    user_id = _session_user_id(request)
    if user_id is None:
        # Session expired between render and submit — bounce through login.
        return _login_redirect(request, str(blob))

    redirect_uri = payload["redirect_uri"]
    state = payload.get("state")

    decision = form.get("decision")
    if decision != "approve":
        return RedirectResponse(
            construct_redirect_uri(
                redirect_uri,
                error="access_denied",
                error_description="The user denied the authorization request.",
                state=state,
            ),
            status_code=302,
        )

    chosen_org = form.get("org_id")
    if not isinstance(chosen_org, str) or not chosen_org:
        return Response("organization is required", status_code=400)

    async with _admin_session() as session:
        # The user must actually belong to the org they picked — never
        # trust the form value alone (it could be tampered).
        if not await orgs_repo.is_member_of(
            session, user_id=user_id, org_id=chosen_org
        ):
            return Response(
                "you are not a member of the selected organization",
                status_code=403,
            )
        user = await users_repo.get_user_by_id(session, user_id)
        if user is None:
            return Response("user no longer exists", status_code=403)

        raw_code = oauth_tokens.new_opaque_token()
        await oauth_repo.create_authorization_code(
            session,
            code_hash=oauth_tokens.hash_token(raw_code),
            client_id=payload["client_id"],
            user_id=user.id,
            org_id=chosen_org,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=payload[
                "redirect_uri_provided_explicitly"
            ],
            code_challenge=payload["code_challenge"],
            scopes=payload.get("scopes") or ["mcp"],
            resource=payload.get("resource"),
            expires_at=utcnow_naive() + timedelta(seconds=AUTH_CODE_TTL_SECONDS),
        )
        await session.commit()

    return RedirectResponse(
        construct_redirect_uri(
            redirect_uri,
            code=raw_code,
            state=state,
            iss=settings.mcp_issuer_base,
        ),
        status_code=302,
    )


def _client_label(payload: dict) -> str:
    """Return a display label for the requesting client.

    Args:
        payload: The decoded authorization-request blob.

    Returns:
        The client id (the blob carries no client name; the id is stable
        and safe to show — DCR clients are anonymous connectors).
    """
    return str(payload.get("client_id") or "this application")


# ── route assembly ──────────────────────────────────────────────────────────


def oauth_root_routes() -> list[Route]:
    """Return the full list of root-mounted OAuth Starlette routes.

    Returns:
        AS metadata + authorize/token/register/revoke + the root PRM doc
        + the Pulse consent page, ready to ``extend`` onto the root app's
        router.
    """
    # The SDK's route builders type these as ``AnyHttpUrl`` and call
    # ``validate_issuer_url`` (which reads ``.scheme`` / ``.host``), so
    # coerce the plain config strings before handing them over.
    issuer = AnyHttpUrl(settings.mcp_issuer_base)
    resource = AnyHttpUrl(settings.mcp_resource_url)
    routes = create_auth_routes(
        provider=oauth_provider,
        issuer_url=issuer,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mcp"],
            default_scopes=["mcp"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    routes += create_protected_resource_routes(
        resource_url=resource,
        authorization_servers=[issuer],
        scopes_supported=["mcp"],
    )
    routes.append(
        Route(
            "/authorize/consent",
            endpoint=consent_get,
            methods=["GET"],
        )
    )
    routes.append(
        Route(
            "/authorize/consent",
            endpoint=consent_post,
            methods=["POST"],
        )
    )
    return routes
