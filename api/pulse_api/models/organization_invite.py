"""SQLModel for the `organization_invites` table.

A pending invite represents an email + role pair an owner has staked for
a future member. Acceptance creates the `organization_memberships` row,
flips `accepted_at`, and optionally creates the `users` row if the
invitee doesn't have an account yet.

Token storage: the migration stores only the SHA-256 hash of the
signed-token value (column `token_hash`) — the raw signed token is in
the email link and never persisted. This mirrors the API-key pattern.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive
from pulse_api.models.organization_membership import MemberRole


class OrganizationInvite(SQLModel, table=True):
    """Outstanding invitation to join an organization.

    Attributes:
        id: UUID primary key.
        org_id: FK into `organizations`.
        email: Lower-cased invitee email.
        role: Role the invite will grant on acceptance.
        token_hash: SHA-256 of the signed invite token (never the raw token).
        invited_by_user_id: FK into `users` — who created the invite.
        expires_at: Hard expiry (typically 7 days from issue).
        accepted_at: Set on acceptance; NULL while pending.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "organization_invites"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    email: str = Field(index=True)
    role: MemberRole = Field(
        default=MemberRole.MEMBER,
        sa_column=Column("role", Text, nullable=False),
    )
    token_hash: str = Field(unique=True, index=True)
    invited_by_user_id: uuid.UUID | None = Field(
        default=None, foreign_key="users.id"
    )
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
