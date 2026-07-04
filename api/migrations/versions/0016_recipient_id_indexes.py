"""Bare indexes on responses.recipient_id and uploads.recipient_id

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-03

Migration 0015 added a unique constraint on ``(card_id, recipient_id)`` for
both ``responses`` and ``uploads``, which Postgres backs with a composite
btree index. That index is only useful for lookups that include
``card_id`` (or that filter on it as the leading column) — a query that
filters by ``recipient_id`` alone (e.g. "give me everything this recipient
has answered/uploaded", used when removing a recipient or rendering their
progress) can't use it and falls back to a sequential scan. Add bare
single-column indexes on ``recipient_id`` for both tables to serve those
lookups directly.

Hand-written to match the rest of this migration set's style — plain
``op.execute`` rather than autogen, since autogen doesn't reliably pick up
manually-managed index sets in this repo.
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "create index ix_responses_recipient_id on public.responses (recipient_id);"
    )
    op.execute(
        "create index ix_uploads_recipient_id on public.uploads (recipient_id);"
    )


def downgrade() -> None:
    op.execute("drop index if exists public.ix_uploads_recipient_id;")
    op.execute("drop index if exists public.ix_responses_recipient_id;")
