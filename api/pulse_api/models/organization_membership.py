"""SQLModel for the `organization_memberships` table.

A row links a `users` row to an `organizations` row with a role. Owners
can be in many orgs; members are restricted to one. The role enum is
stored as Postgres text — see the `role` CHECK constraint in the
migration for the source of truth.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class MemberRole(str, enum.Enum):
    """Role a user holds within an organization.

    Two values:
        OWNER: Full admin powers within the org (invite, settings, billing).
        MEMBER: Day-to-day operator — list/edit clients but no org admin.
    """

    OWNER = "owner"
    MEMBER = "member"


class OrganizationMembership(SQLModel, table=True):
    """Join row linking a user to an organization with a role.

    Attributes:
        id: UUID primary key.
        org_id: FK into `organizations`.
        user_id: FK into `users`.
        role: `owner` or `member`. Stored as text to keep DB enum churn out.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "organization_memberships"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    role: MemberRole = Field(
        default=MemberRole.MEMBER,
        sa_column=Column("role", Text, nullable=False),
    )
    created_at: datetime = Field(default_factory=utcnow_naive)
