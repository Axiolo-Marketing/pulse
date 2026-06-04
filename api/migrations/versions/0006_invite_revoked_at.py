"""invites: split revoked_at out from accepted_at

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-04

Before this revision, ``organization_invites.revoke_pending`` reused
``accepted_at`` as the "no longer pending" sentinel — which made the
public token-resolve endpoint return ``status = "accepted"`` for
revoked invites, with no way for the UI (or future auditors) to tell
the two states apart.

This revision adds a dedicated ``revoked_at timestamptz`` column. The
repo layer is updated in the same PR to:

* stamp ``revoked_at = now()`` from ``revoke_pending`` (not
  ``accepted_at``),
* prefer ``revoked_at`` over ``accepted_at`` when computing the
  ``invite_status`` label, so the recipient sees ``"revoked"`` and the
  acceptance UI can show a recovery path instead of a misleading
  "already used" message.

Backfill: none. Revocation was never invoked in production before this
PR landed (PR 3 of the multi-tenant refactor is still in-flight), so
any row with ``accepted_at IS NOT NULL`` is genuinely an acceptance.
Pre-existing rows that the DB can no longer disambiguate (revoked vs.
accepted) do not exist; if a future migration discovers them, that is
the moment to add a one-off backfill — for now this column starts
empty and stays correct going forward.

``downgrade()`` drops the column. Note that any post-upgrade
revocations are then unrecoverable — the previous ``accepted_at``
overload is gone with them.
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table public.organization_invites "
        "add column revoked_at timestamptz;"
    )


def downgrade() -> None:
    op.execute(
        "alter table public.organization_invites "
        "drop column if exists revoked_at;"
    )
