import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class Upload(SQLModel, table=True):
    """File the client uploaded against a card.

    `org_id` is nullable in PR 1; gets a column DEFAULT from the
    `pulse.org_id` GUC so client-facing INSERTs populate it once the
    middleware sets the GUC.

    Attributes:
        id: UUID primary key.
        card_id: Card the upload belongs to.
        engagement_id: Owning engagement (the shared cards).
        recipient_id: Owning recipient — uploads are scoped per respondent.
            A column DEFAULT reads ``pulse_request_recipient_id()`` so
            client INSERTs populate it.
        org_id: Owning organization (nullable in PR 1, NOT NULL in PR 2).
        file_name: Original filename from the client browser.
        file_size_bytes: Byte size as stored.
        storage_path: Relative path under `settings.upload_dir`.
        mime_type: Best-effort content type; nullable.
        kind: Upload discriminator — ``'file'`` for answer-attachment files
            (the default, and what the `file-upload` card type uses) or
            ``'voice'`` for a recorded voice answer. Voice notes supplement
            the typed answer; they never change `response_value` shapes.
        uploaded_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "uploads"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    card_id: uuid.UUID = Field(foreign_key="cards.id")
    engagement_id: uuid.UUID = Field(foreign_key="engagements.id")
    recipient_id: uuid.UUID = Field(foreign_key="recipients.id")
    org_id: uuid.UUID | None = Field(
        default=None, foreign_key="organizations.id", index=True
    )
    file_name: str
    file_size_bytes: int
    storage_path: str
    mime_type: str | None = None
    kind: str = "file"
    uploaded_at: datetime = Field(default_factory=utcnow_naive)
