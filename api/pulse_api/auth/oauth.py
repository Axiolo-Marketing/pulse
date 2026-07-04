"""OAuth provider configuration + low-level flow helpers.

Two providers behind one shape: Google and Microsoft 365. The route layer
uses these via a `(provider: str) -> OAuthProvider` lookup so authorize +
callback can be a single parameterized endpoint.

We deliberately use the userinfo endpoint (Bearer-auth'd HTTP call to the
provider) rather than parsing the id_token directly — avoids JWKS fetch +
JWT signature validation, which adds complexity for no extra security at
this scale (we still trust the provider, which is what id_token sig
verification establishes).

One exception: Microsoft's `/oidc/userinfo` response never includes the
`tid` (tenant id) claim, and Microsoft is configured with tenant="common"
(personal + work/school accounts), so `tid` is the only signal the
callback route has to decide whether a Microsoft account's `email` claim
is trustworthy enough to drive the email-lookup paths (see
`decode_id_token_claims`). We read `tid` out of the id_token's payload
WITHOUT verifying its signature — the id_token arrives directly from the
provider's token endpoint over a TLS connection authenticated with our
client secret, the same trust level we already extend to the
access_token used to call userinfo, so skipping signature verification
here doesn't lower the bar. It is never used to authenticate the
request, only to read this one auxiliary claim.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from pulse_api.config import settings


class OAuthProviderError(Exception):
    """Raised when a provider returns a non-2xx during code exchange or userinfo."""


def decode_id_token_claims(id_token: str) -> dict:
    """Best-effort, UNVERIFIED decode of a JWT's payload segment.

    Used only to read auxiliary claims the userinfo endpoint doesn't
    expose (notably Microsoft's `tid`) — never to authenticate anything.
    Returns ``{}`` for anything malformed rather than raising; a missing
    auxiliary claim should degrade to "untrusted", not 500.
    """
    try:
        parts = id_token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


def microsoft_allowed_tenant_ids() -> set[str]:
    """Parse ``settings.microsoft_allowed_tenant_ids`` into a lower-cased set.

    Whitespace- or comma-separated, mirroring the ``SUPERADMIN_EMAILS``
    parsing convention used elsewhere (migration 0004, ``dev_seed.py``).
    Empty setting -> empty set, meaning no Microsoft tenant is pinned.
    """
    raw = (settings.microsoft_allowed_tenant_ids or "").strip()
    if not raw:
        return set()
    return {p.strip().lower() for p in raw.replace(",", " ").split() if p.strip()}


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]

    def build_authorize_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            "prompt": "select_account",
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        """POST the code to the provider's token endpoint. Returns the JSON body."""
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(
                self.token_url,
                data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        if r.status_code >= 400:
            raise OAuthProviderError(
                f"{self.name} token exchange failed: {r.status_code} {r.text[:200]}"
            )
        return r.json()

    async def fetch_userinfo(
        self, access_token: str, id_token: str | None = None
    ) -> dict:
        """GET the provider's userinfo endpoint with the access token. Returns JSON.

        When ``id_token`` is supplied (from the token-exchange response),
        merges in the ``email_verified`` / ``tid`` claims it carries when
        the userinfo response didn't already provide them — Microsoft's
        `/oidc/userinfo` never sends `tid`, and some providers omit
        `email_verified` from userinfo too. See module docstring for why
        decoding the id_token without signature verification is safe here.
        """
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(
                self.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if r.status_code >= 400:
            raise OAuthProviderError(
                f"{self.name} userinfo failed: {r.status_code} {r.text[:200]}"
            )
        userinfo = r.json()
        if id_token:
            claims = decode_id_token_claims(id_token)
            for key in ("email_verified", "tid"):
                if key in claims and key not in userinfo:
                    userinfo[key] = claims[key]
        return userinfo


def google_provider() -> OAuthProvider:
    return OAuthProvider(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://www.googleapis.com/oauth2/v3/userinfo",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=settings.google_redirect_uri,
        scopes=("openid", "email", "profile"),
    )


def microsoft_provider() -> OAuthProvider:
    tenant = settings.microsoft_tenant_id or "common"
    return OAuthProvider(
        name="microsoft",
        authorize_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        token_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/oidc/userinfo",
        client_id=settings.microsoft_client_id,
        client_secret=settings.microsoft_client_secret,
        redirect_uri=settings.microsoft_redirect_uri,
        scopes=("openid", "email", "profile"),
    )


PROVIDER_FACTORIES = {
    "google": google_provider,
    "microsoft": microsoft_provider,
}


def get_provider(name: str) -> OAuthProvider | None:
    factory = PROVIDER_FACTORIES.get(name)
    return factory() if factory else None
