import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class ApiKey(SQLModel, table=True):
    """Per-user Bearer token row.

    `org_id` is nullable in PR 1. PR 2 makes it NOT NULL and binds each
    key to a `(user, org)` pair so non-browser callers (CLI, CI, MCP)
    can authenticate against a specific tenant. PR 1 backfills existing
    rows to the Axiolo org via the data migration.

    Attributes:
        id: UUID primary key.
        user_id: Owning user.
        org_id: Owning organization (nullable in PR 1, NOT NULL in PR 2).
        prefix: First 8 hex chars of the raw key — indexed for cheap
            lookup before the constant-time hash compare.
        key_hash: SHA-256 of the raw key value.
        label: Human-readable name set at create time.
        last_used_at: Updated on each successful auth using the key.
        revoked_at: Set on revoke; nullable indexes exclude these rows.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "api_keys"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    org_id: uuid.UUID | None = Field(
        default=None, foreign_key="organizations.id", index=True
    )
    prefix: str = Field(max_length=8, index=True)
    key_hash: str
    label: str
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
