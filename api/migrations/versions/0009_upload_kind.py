"""Add ``uploads.kind`` discriminator (file vs. voice)

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-22

Voice answers reuse the existing upload pipeline: a recorded note is just
a client upload tied to ``(card_id, client_id)``. A ``kind`` column tells
voice notes apart from the ``file-upload`` answer files so the operator
viewer can render the former as an inline player on any card.

The column defaults to ``'file'`` so every existing row and the
``file-upload`` path are unchanged; ``response_value`` shapes are
untouched. The CHECK constraint bounds the value to the two known kinds.

``downgrade()`` drops the column.
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table public.uploads "
        "add column kind text not null default 'file' "
        "check (kind in ('file', 'voice'));"
    )


def downgrade() -> None:
    op.execute("alter table public.uploads drop column kind;")
