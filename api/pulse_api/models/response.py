import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class Response(SQLModel, table=True):
    """Client's answer (or non-answer) for a card.

    `org_id` is nullable in PR 1. The migration installs a column
    DEFAULT that reads from the `pulse.org_id` GUC, so client-facing
    INSERTs continue to populate it without code changes once the
    middleware sets the GUC from the resolved client's org.

    Attributes:
        id: UUID primary key.
        card_id: Card being answered.
        client_id: Owning engagement.
        org_id: Owning organization (nullable in PR 1, NOT NULL in PR 2).
        state: One of `not_started|viewed|answered|skipped|needs_edit`.
        response_value: JSONB blob whose shape depends on `response_type`.
        viewed_at, answered_at, created_at, updated_at: Timestamps.
    """

    __tablename__ = "responses"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    card_id: uuid.UUID = Field(foreign_key="cards.id")
    client_id: uuid.UUID = Field(foreign_key="clients.id")
    org_id: uuid.UUID | None = Field(
        default=None, foreign_key="organizations.id", index=True
    )
    state: str
    response_value: Any | None = Field(default=None, sa_column=Column(JSONB))
    viewed_at: datetime | None = None
    answered_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)
