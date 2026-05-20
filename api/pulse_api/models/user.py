import uuid
from datetime import datetime
from pulse_api.models._helpers import utcnow_naive

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str | None = None
    name: str | None = None
    is_admin: bool = False
    email_verified_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    last_login_at: datetime | None = None
    # Fernet ciphertext of the user's ClickUp OAuth access token. Decrypt
    # via pulse_api.crypto.decrypt at the moment of API call.
    clickup_access_token_enc: str | None = None
    clickup_user_id: str | None = None
