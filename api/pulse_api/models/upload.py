import uuid
from datetime import datetime
from pulse_api.models._helpers import utcnow_naive

from sqlmodel import Field, SQLModel


class Upload(SQLModel, table=True):
    __tablename__ = "uploads"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    card_id: uuid.UUID = Field(foreign_key="cards.id")
    client_id: uuid.UUID = Field(foreign_key="clients.id")
    file_name: str
    file_size_bytes: int
    storage_path: str
    mime_type: str | None = None
    uploaded_at: datetime = Field(default_factory=utcnow_naive)
