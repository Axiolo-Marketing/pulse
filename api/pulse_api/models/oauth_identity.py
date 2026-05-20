import uuid
from datetime import datetime
from pulse_api.models._helpers import utcnow_naive

from sqlmodel import Field, SQLModel


class OAuthIdentity(SQLModel, table=True):
    __tablename__ = "oauth_identities"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    provider: str
    provider_user_id: str
    created_at: datetime = Field(default_factory=utcnow_naive)
