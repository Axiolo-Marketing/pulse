import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class Recipient(SQLModel, table=True):
    """One respondent on an engagement — owns the magic-link token + answers.

    An engagement (the shared question set) can have many recipients; each
    carries its own ``token`` (its private deck link), answers the cards
    independently (``responses``/``uploads`` carry ``recipient_id``), and
    has its own activity + invite/reminder state. The owning org is
    denormalised onto the row so RLS scopes by ``org_id`` like every other
    tenant table.

    Attributes:
        id: UUID primary key.
        engagement_id: Owning engagement (the shared cards).
        org_id: Owning organization (tenant scope).
        email: Recipient's email. Nullable — legacy recipients backfilled
            from pre-multi-respondent engagements have none; the admin
            requires it for newly added recipients (invites/reminders need
            it). Unique per engagement (case-insensitive) when present.
        name: Optional display name for greetings.
        token: 16-hex magic-link token the recipient uses in ``?t=``.
        last_active_at: Updated by the recipient's deck heartbeat.
        invited_at: When the initial invite email was sent (NULL = not yet).
        last_reminded_at: When the last reminder was sent.
        reminder_count: How many reminders have gone out.
        unsubscribed_at: Set when the recipient opts out of reminders.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "recipients"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    engagement_id: uuid.UUID = Field(foreign_key="engagements.id", index=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    email: str | None = None
    name: str | None = None
    token: str = Field(unique=True, index=True)
    last_active_at: datetime | None = None
    invited_at: datetime | None = None
    last_reminded_at: datetime | None = None
    reminder_count: int = 0
    unsubscribed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
