"""SQLModel for the `oauth_clients` table.

Dynamic Client Registration (RFC 7591) records for the MCP OAuth 2.1
authorization server. Claude Desktop self-registers a public client
(``token_endpoint_auth_method = "none"``, no secret), so
``client_secret_hash`` is nullable. The JSONB columns hold the
registered ``redirect_uris`` / ``grant_types`` / ``response_types``
arrays the SDK token + authorize handlers validate against.

No RLS — the OAuth provider always runs on a ``pulse_admin`` session
because DCR happens before any tenant context exists.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class OAuthClient(SQLModel, table=True):
    """A registered OAuth client (one per Claude connector instance).

    Attributes:
        id: UUID primary key.
        client_id: Public client identifier issued at registration —
            unique, returned to the client in the DCR response.
        client_secret_hash: SHA-256 of the client secret, or None for
            public clients (the common case for Claude Desktop).
        redirect_uris: Registered redirect URIs (list of strings).
        grant_types: Supported grant types, e.g.
            ``["authorization_code", "refresh_token"]``.
        response_types: Supported response types, e.g. ``["code"]``.
        token_endpoint_auth_method: One of ``none`` /
            ``client_secret_post`` / ``client_secret_basic``.
        client_name: Optional human-readable client name.
        scope: Optional space-delimited registered scope string.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "oauth_clients"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    client_id: str = Field(unique=True, index=True)
    client_secret_hash: str | None = None
    redirect_uris: list = Field(sa_column=Column(JSONB, nullable=False))
    grant_types: list = Field(sa_column=Column(JSONB, nullable=False))
    response_types: list = Field(sa_column=Column(JSONB, nullable=False))
    token_endpoint_auth_method: str
    client_name: str | None = None
    scope: str | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
