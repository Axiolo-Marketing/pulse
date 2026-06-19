"""SQLModel for the `oauth_grants` table.

The issued access + refresh token pair for one authorization. Opaque
tokens are stored as ``prefix`` (first 8 chars, indexed) + SHA-256
``hash`` — identical to the API-key pattern — so a grant is instantly
revocable and tokens never live on disk in plaintext.

A grant binds ``(user, org, client, scopes, resource)``. The
``org_id`` is what flows into the access token's ``org_id`` claim and
then into each tool's member-scoped session. Rotation (refresh) updates
the same row in place with fresh access + refresh material.

No RLS — the provider/verifier run on a ``pulse_admin`` session.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class OAuthGrant(SQLModel, table=True):
    """An active (or revoked) OAuth token grant.

    Attributes:
        id: UUID primary key.
        access_prefix: First 8 chars of the access token — indexed for
            cheap lookup before the constant-time hash compare.
        access_hash: SHA-256 hex of the access token.
        access_expires_at: Access-token hard expiry.
        refresh_prefix: First 8 chars of the refresh token (nullable —
            a grant may have no refresh token).
        refresh_hash: SHA-256 hex of the refresh token, or None.
        refresh_expires_at: Refresh-token hard expiry, or None.
        user_id: Resource owner.
        org_id: Organization the tokens are scoped to.
        client_id: The client the grant was issued to.
        scopes: Granted scopes (list of strings).
        resource: RFC 8707 resource indicator (the MCP URL), or None.
        revoked_at: Set on revoke; the active-only partial indexes
            exclude these rows.
        last_used_at: Updated best-effort on each successful access.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "oauth_grants"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    access_prefix: str = Field(max_length=8, index=True)
    access_hash: str
    access_expires_at: datetime
    refresh_prefix: str | None = Field(default=None, max_length=8, index=True)
    refresh_hash: str | None = None
    refresh_expires_at: datetime | None = None
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    client_id: str
    scopes: list = Field(sa_column=Column(JSONB, nullable=False))
    resource: str | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
