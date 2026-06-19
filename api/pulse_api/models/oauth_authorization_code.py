"""SQLModel for the `oauth_authorization_codes` table.

Single-use PKCE authorization codes minted by the consent page (PR 2)
and exchanged at the token endpoint. Each code is bound to the
``(user, org, client)`` the operator approved. The stored
``code_challenge`` is the S256 challenge string — PKCE verification
happens in the SDK's token handler, which hashes the presented
``code_verifier`` and compares it to this value, so the provider must
surface ``code_challenge`` unchanged and never verify PKCE itself.

Rows are deleted on exchange (single-use) and rejected after
``expires_at``. No RLS — the provider runs on a ``pulse_admin`` session.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class OAuthAuthorizationCode(SQLModel, table=True):
    """A pending authorization code awaiting token exchange.

    Attributes:
        id: UUID primary key.
        code_hash: SHA-256 hex of the raw authorization code — the only
            form stored. Unique + indexed for lookup at exchange time.
        client_id: The client the code was issued to.
        user_id: Resource owner (the operator who approved consent).
        org_id: Organization the resulting tokens are scoped to.
        redirect_uri: The redirect URI used in the authorize request.
        redirect_uri_provided_explicitly: Whether the client passed
            ``redirect_uri`` explicitly (RFC 6749 §10.6 consistency
            check in the token handler).
        code_challenge: The PKCE challenge string.
        code_challenge_method: The PKCE method (``S256`` only today). Stored
            explicitly so a future ``plain`` cannot be silently verified as
            ``S256`` (downgrade-safe per RFC 7636).
        scopes: Requested scopes (list of strings).
        resource: RFC 8707 resource indicator, or None.
        expires_at: Hard expiry — codes are short-lived.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "oauth_authorization_codes"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code_hash: str = Field(unique=True, index=True)
    client_id: str
    user_id: uuid.UUID = Field(foreign_key="users.id")
    org_id: uuid.UUID = Field(foreign_key="organizations.id")
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    code_challenge: str
    code_challenge_method: str = Field(default="S256")
    scopes: list = Field(sa_column=Column(JSONB, nullable=False))
    resource: str | None = None
    expires_at: datetime
    created_at: datetime = Field(default_factory=utcnow_naive)
