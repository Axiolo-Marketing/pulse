"""SQLModel for the `organizations` table.

The top of the multi-tenant tree: every tenant-scoped row (`clients`,
`cards`, `responses`, `uploads`, `api_keys`, `audit_logs`) carries an
`org_id` that points back here, and the `pulse.org_id` GUC RLS reads at
query time is set from the membership the request resolves to.
"""
import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from pulse_api.models._helpers import utcnow_naive


class Organization(SQLModel, table=True):
    """A tenant — one company's slice of Pulse data.

    Slug is the public-ish handle (URL-safe, lowercase, unique). The
    Axiolo organization is created by the data migration and is the
    backfill target for all pre-existing rows.

    Attributes:
        id: UUID primary key.
        name: Human-readable organization name.
        slug: URL-safe unique handle.
        logo_path: Optional path to the uploaded org logo on disk.
        created_at: Insert timestamp (naive UTC).
    """

    __tablename__ = "organizations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    logo_path: str | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
