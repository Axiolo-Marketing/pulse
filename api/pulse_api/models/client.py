import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class Client(SQLModel, table=True):
    """A real client (company) that owns many engagements.

    Per-org and operator-only — the client deck never sees the ``clients``
    table directly (the deck shows the client name via a join on
    ``engagements.client_id``). A client name is unique within an org
    (``unique (org_id, name)``) but may repeat across orgs, so two
    different tenants can both have a "Acme" client.

    New engagements either reference an existing client (autocomplete in
    the new-engagement dialog) or get-or-create one by name.

    Attributes:
        id: UUID primary key.
        org_id: Owning organization (NOT NULL).
        name: Company name shown in the admin list + the client deck.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "clients"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    name: str
    created_at: datetime = Field(default_factory=utcnow_naive)
