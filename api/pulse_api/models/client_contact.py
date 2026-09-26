import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class ClientContact(SQLModel, table=True):
    """A saved person at a client — the respondent picker's type-ahead source.

    Unique per client on ``lower(email)`` (migration 0018). Adding a
    recipient to any of the client's engagements upserts a contact, and
    operators can manage the list directly. Operator-only: the client deck
    never reads contacts.

    Attributes:
        id: UUID primary key.
        org_id: Owning organization (NOT NULL; RLS scope).
        client_id: The client this person belongs to (cascade delete).
        email: Contact email (case-insensitively unique per client).
        name: Optional display name.
        role: Optional job title / role.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "client_contacts"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    client_id: uuid.UUID = Field(foreign_key="clients.id")
    email: str
    name: str | None = None
    role: str | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
