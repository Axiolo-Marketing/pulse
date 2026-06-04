import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class Card(SQLModel, table=True):
    """Denormalized question card belonging to an engagement.

    `org_id` is nullable in PR 1 — admin routes still run as
    `pulse_admin` (BYPASSRLS) so the column is not yet load-bearing for
    isolation. PR 2 makes it NOT NULL when admin routes flip to
    `pulse_member` and need RLS to filter by `pulse.org_id`.

    Attributes:
        id: UUID primary key.
        client_id: Owning engagement.
        org_id: Owning organization (nullable in PR 1, NOT NULL in PR 2).
        order_index: Position in the deck (unique per client).
        category, title, context, question: Card content.
        response_type: One of the SPEC §4 enum strings.
        options: JSONB option set for select-type cards.
        default_value: Pre-populated answer shown to the client.
        skip_allowed: Whether the client can skip without answering.
        attachment_path: Optional path under `public/deliverables/`.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "cards"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    client_id: uuid.UUID = Field(foreign_key="clients.id")
    org_id: uuid.UUID | None = Field(
        default=None, foreign_key="organizations.id", index=True
    )
    order_index: int
    category: str
    title: str
    context: str
    question: str
    response_type: str
    options: Any | None = Field(default=None, sa_column=Column(JSONB))
    default_value: str | None = None
    skip_allowed: bool = True
    attachment_path: str | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
