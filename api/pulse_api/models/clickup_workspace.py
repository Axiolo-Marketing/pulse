import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class ClickUpWorkspace(SQLModel, table=True):
    """One row per (operator, ClickUp workspace). Pulse registers a webhook
    per workspace at OAuth-connect time; the secret returned by ClickUp is
    stored encrypted (column suffixed `_enc`) for HMAC verification of
    incoming events.

    `workspace_id` is ClickUp's team_id. `webhook_id` is the id ClickUp
    assigned to the webhook subscription; needed at disconnect-time to
    DELETE it. Neither of those are secrets — only `webhook_secret_enc` is.
    """

    __tablename__ = "clickup_workspaces"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    workspace_id: str = Field(index=True)
    workspace_name: str | None = None
    webhook_id: str | None = None
    webhook_secret_enc: str | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
