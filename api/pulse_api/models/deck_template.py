import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class DeckTemplate(SQLModel, table=True):
    """A reusable snapshot of an engagement's deck (migration 0024).

    Org-scoped (RLS for ``pulse_member``); never visible to respondents.
    Starting an engagement from a template copies ``cards`` (in order) and
    applies ``settings``; the template is not linked live afterwards.

    Attributes:
        id: UUID primary key.
        org_id: Owning organization (NOT NULL; RLS scope).
        name: Display name, unique per org case-insensitively.
        description: Optional free text.
        cards: Ordered JSON array of operator-authored cards.
        settings: JSON object of engagement toggles to carry over.
        created_by: User who saved it (set null if they're removed).
        created_at: Insert timestamp (naive UTC).
        updated_at: Last change (naive UTC).
    """

    __tablename__ = "deck_templates"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    name: str
    description: str | None = None
    cards: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB, nullable=False)
    )
    settings: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    created_by: uuid.UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)
