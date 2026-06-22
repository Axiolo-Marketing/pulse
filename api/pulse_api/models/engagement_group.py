import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class EngagementGroup(SQLModel, table=True):
    """A flat folder operators use to organize the engagement list.

    One folder per engagement (engagements point back via the nullable
    ``clients.group_id``). Folders are per-org and operator-only — the
    client deck never sees them. Deleting a folder ungroups its
    engagements (``on delete set null`` on the FK), it never deletes
    them; a NULL ``group_id`` is the implicit "Ungrouped" bucket.

    Attributes:
        id: UUID primary key.
        org_id: Owning organization (NOT NULL).
        name: Folder label shown in the admin list.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "engagement_groups"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    name: str
    created_at: datetime = Field(default_factory=utcnow_naive)
