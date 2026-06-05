import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class Client(SQLModel, table=True):
    """An engagement — one consultant↔customer relationship.

    Carries `org_id` (NOT NULL after the 0004 migration) — every client
    belongs to exactly one organization. Backfill puts pre-existing rows
    on the Axiolo org.

    Attributes:
        id: UUID primary key.
        org_id: Owning organization.
        name: Customer-facing name.
        org_name: Optional customer org name (legacy column; predates
            multi-tenant orgs and stays as plain text on the client row).
        engagement_name: Optional human label for the engagement.
        token: 16-hex magic-link token the client uses in `?t=`.
        brief: Free-text brief shown in admin.
        created_at: Insert timestamp (naive UTC).
        last_active_at: Updated by the client touch-endpoint.
    """

    __tablename__ = "clients"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    name: str
    org_name: str | None = None
    engagement_name: str | None = None
    token: str = Field(unique=True, index=True)
    brief: str | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    last_active_at: datetime | None = None
