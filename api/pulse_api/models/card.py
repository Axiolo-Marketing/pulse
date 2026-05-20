import uuid
from datetime import datetime
from pulse_api.models._helpers import utcnow_naive
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Card(SQLModel, table=True):
    __tablename__ = "cards"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    client_id: uuid.UUID = Field(foreign_key="clients.id")
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
    # Back-reference for idempotent re-pushes. Set on first push;
    # subsequent pushes UPDATE the existing ClickUp task by this id.
    clickup_task_id: str | None = None
