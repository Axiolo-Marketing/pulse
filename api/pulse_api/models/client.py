import uuid
from datetime import datetime
from pulse_api.models._helpers import utcnow_naive

from sqlmodel import Field, SQLModel


class Client(SQLModel, table=True):
    __tablename__ = "clients"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    org_name: str | None = None
    engagement_name: str | None = None
    token: str = Field(unique=True, index=True)
    brief: str | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    last_active_at: datetime | None = None
    # Per-engagement ClickUp target. NULL = ClickUp push button disabled.
    clickup_list_id: str | None = None
    clickup_list_name: str | None = None
