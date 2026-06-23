"""Per-engagement voice toggle — ``clients.voice_enabled``

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-22

Voice recording is gated per engagement and defaults OFF. The client deck
shows the record control only when this is ``true``; the backend upload
route refuses ``kind='voice'`` writes when it's ``false`` (a defence that
holds even if the UI is bypassed). Existing recordings are unaffected —
the column only governs *new* voice uploads and whether the deck offers
the control.

No RLS change: this is a plain boolean column on the already org-scoped
``clients`` table, which carries its multi-tenant policies from migration
0004. ``downgrade()`` drops the column.
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table public.clients "
        "add column voice_enabled boolean not null default false;"
    )


def downgrade() -> None:
    op.execute(
        "alter table public.clients drop column if exists voice_enabled;"
    )
