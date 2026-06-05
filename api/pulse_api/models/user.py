import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class User(SQLModel, table=True):
    """Operator user — signs into `/admin/` via cookie or API key.

    Permissions are no longer carried on the user row. Two replacements,
    introduced by migration 0004 and locked in by 0005:

    * Per-org admin powers come from ``organization_memberships.role``
      (``owner`` vs ``member``). A user can be an owner of org A and a
      member of org B at the same time.
    * Cross-org operator powers come from ``is_superadmin``.

    Attributes:
        id: UUID primary key.
        email: Lower-cased unique email.
        password_hash: Argon2 hash, or NULL for OAuth-only accounts.
        name: Display name; nullable.
        is_superadmin: When True the user can hit `/api/superadmin/*`.
        last_active_org_id: Org the user last switched into; the session
            payload (`active_org_id`) defaults to this on fresh logins.
        email_verified_at: Set when the user clicked the verify link.
        created_at: Insert timestamp (naive UTC).
        last_login_at: Updated on every successful login.
    """

    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str | None = None
    name: str | None = None
    is_superadmin: bool = False
    last_active_org_id: uuid.UUID | None = Field(
        default=None, foreign_key="organizations.id"
    )
    email_verified_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    last_login_at: datetime | None = None
