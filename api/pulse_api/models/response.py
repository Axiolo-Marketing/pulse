import uuid
from datetime import datetime
from pulse_api.models._helpers import utcnow_naive
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Response(SQLModel, table=True):
    __tablename__ = "responses"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    card_id: uuid.UUID = Field(foreign_key="cards.id")
    client_id: uuid.UUID = Field(foreign_key="clients.id")
    state: str
    response_value: Any | None = Field(default=None, sa_column=Column(JSONB))
    viewed_at: datetime | None = None
    answered_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)
    # Last-known ClickUp status, set by the webhook receiver when a task's
    # status changes upstream. May differ from the Pulse-suggested status
    # until the next push or webhook event.
    clickup_status: str | None = None
    clickup_status_updated_at: datetime | None = None
