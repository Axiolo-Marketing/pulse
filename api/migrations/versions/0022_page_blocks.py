"""Page blocks: questions that live in a web page instead of the deck.

A client document (e.g. a QuickHost page) marks its interactive parts with
``<pulse-question>`` / ``<pulse-grid>`` elements. Syncing the page creates one
card per block, and the page's embed script saves answers to those cards
with the respondent's ``?t=`` token — so answers, progress, alerts and the
Markdown export work exactly as they do for the deck.

* ``cards.block_id`` — the block's ``id`` attribute; unique per engagement,
  NULL for ordinary deck cards. The sync upserts on it.
* ``cards.block_config`` — block settings the card columns can't hold (a
  grid's rows and columns, each column's options).
* ``'grid'`` response type — ``response_value = {"cells": {row: {col: value}}}``.
* ``engagements.page_url`` — the document respondents open. When set, their
  links point at it (``{page_url}?t=<token>``) instead of the deck.

No RLS/grant change: plain columns on tables whose table-level grants and
policies already cover them.
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_TYPES = (
    "'confirm-edit','single-select','multi-select','short-text',"
    "'long-text','file-upload','document-link','contact-share'"
)


def upgrade() -> None:
    op.execute(
        """
        alter table public.cards
            add column block_id text,
            add column block_config jsonb;
        """
    )
    op.execute(
        "create unique index cards_engagement_block_id_key "
        "on public.cards (engagement_id, block_id) where block_id is not null;"
    )
    op.execute("alter table public.cards drop constraint cards_response_type_check;")
    op.execute(
        "alter table public.cards add constraint cards_response_type_check "
        f"check (response_type in ({_TYPES},'grid'));"
    )
    op.execute("alter table public.engagements add column page_url text;")


def downgrade() -> None:
    op.execute("alter table public.engagements drop column if exists page_url;")
    op.execute("delete from public.cards where response_type = 'grid';")
    op.execute("alter table public.cards drop constraint cards_response_type_check;")
    op.execute(
        "alter table public.cards add constraint cards_response_type_check "
        f"check (response_type in ({_TYPES}));"
    )
    op.execute("drop index if exists public.cards_engagement_block_id_key;")
    op.execute(
        "alter table public.cards drop column if exists block_config, "
        "drop column if exists block_id;"
    )
