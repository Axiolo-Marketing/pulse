"""OAuth provider configuration + low-level flow helpers.

Two providers behind one shape: Google and Microsoft 365. The route layer
uses these via a `(provider: str) -> OAuthProvider` lookup so authorize +
callback can be a single parameterized endpoint.

We deliberately use the userinfo endpoint (Bearer-auth'd HTTP call to the
provider) rather than parsing the id_token directly — avoids JWKS fetch +
JWT signature validation, which adds complexity for no extra security at
this scale (we still trust the provider, which is what id_token sig
verification establishes).
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from pulse_api.config import settings


class OAuthProviderError(Exception):
    """Raised when a provider returns a non-2xx during code exchange or userinfo."""


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

    async def fetch_userinfo(self, access_token: str) -> dict:
        """GET the provider's userinfo endpoint with the access token. Returns JSON."""
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(
                self.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if r.status_code >= 400:
            raise OAuthProviderError(
                f"{self.name} userinfo failed: {r.status_code} {r.text[:200]}"
            )
        return r.json()


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
