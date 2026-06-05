import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class ApiKey(SQLModel, table=True):
    """Per-(user, org) Bearer token row.

    Each key authenticates as a ``(user_id, org_id)`` pair. The owning
    user must be a member of ``org_id`` at creation time; revoking the
    membership does NOT auto-revoke the key (that's a separate operator
    action), but a key with no matching active membership will fail
    membership lookup at request time and return 403.

    Migration 0005 makes ``org_id`` NOT NULL; 0004 backfilled every
    pre-existing row to the Axiolo org.

    Attributes:
        id: UUID primary key.
        user_id: Owning user.
        org_id: Owning organization. NOT NULL — every key is scoped.
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
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    prefix: str = Field(max_length=8, index=True)
    key_hash: str
    label: str
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
