"""SQLModel for the `audit_logs` table.

One row per mutating action inside an org. Read by the activity feed UI
(PR 6) and written via a small `record_audit()` helper invoked from
admin routes. Org-scoped via `org_id` like every other tenant row, so
the same `pulse_member` policy on `pulse.org_id` keeps cross-org leaks
out.

The model attribute is `event_metadata` to dodge SQLAlchemy's reserved
`metadata` name on declarative bases. The DB column is still named
`metadata` so SQL helpers don't need any rename.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class AuditLog(SQLModel, table=True):
    """Append-only audit trail for org-scoped actions.

    Attributes:
        id: UUID primary key.
        org_id: FK into `organizations`.
        user_id: FK into `users`; nullable for actions that aren't tied
            to a single operator (e.g. system maintenance jobs).
        action: Stable enum-like string (`engagement.create`, `member.invite`).
        target_type: The object class the action affected (`engagement`,
            `card`, `member`, etc.).
        target_id: Stringified UUID/identifier of the affected object.
        event_metadata: JSONB blob with action-specific fields. Stored
            on disk under the column name `metadata`.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "audit_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    org_id: uuid.UUID = Field(foreign_key="organizations.id", index=True)
    user_id: uuid.UUID | None = Field(default=None, foreign_key="users.id")
    action: str
    target_type: str | None = None
    target_id: str | None = None
    event_metadata: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("metadata", JSONB),
    )
    created_at: datetime = Field(default_factory=utcnow_naive)
