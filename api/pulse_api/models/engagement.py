import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class Engagement(SQLModel, table=True):
    """An engagement — one consultant↔customer relationship.

    Carries `org_id` (NOT NULL after the 0004 migration) — every
    engagement belongs to exactly one organization. As of migration 0013
    it also belongs to a real ``Client`` (``client_id``, NOT NULL) that
    owns the customer-facing name; the engagement no longer carries its
    own ``name`` column.

    Attributes:
        id: UUID primary key.
        org_id: Owning organization.
        client_id: Owning client (company). NOT NULL after the 0013
            backfill — the client owns the customer-facing name.
        created_by: User who created the engagement, or NULL when the
            owner couldn't be backfilled from the original create audit
            row. ``on delete set null`` keeps the engagement if the user
            is removed.
        engagement_name: Optional human label for the engagement.
        brief: Free-text brief shown in admin.
        voice_enabled: When ``True``, the client deck shows the voice
            record control and the upload route accepts ``kind='voice'``
            writes. Defaults ``False`` — voice is opt-in per engagement.
        reminders_enabled: Per-engagement pause switch for the scheduled
            reminder job. Defaults ``True``; the magic-link token and
            per-recipient activity now live on ``Recipient`` (migration
            0015), so reminders fan out per recipient.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "engagements"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    client_id: uuid.UUID = Field(foreign_key="clients.id")
    created_by: uuid.UUID | None = Field(default=None, foreign_key="users.id")
    engagement_name: str | None = None
    brief: str | None = None
    voice_enabled: bool = False
    reminders_enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow_naive)
