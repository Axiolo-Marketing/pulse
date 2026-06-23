"""Rename ``clients`` → ``engagements`` (pure rename, no behaviour change)

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-23

The table literally named ``clients`` actually stores ENGAGEMENTS — it
owns the magic-link ``token`` plus the ``cards`` / ``responses`` /
``uploads`` children. This revision frees the names ``clients`` /
``client_id`` / ``pulse_request_client_id`` so a LATER migration can add
a real Client (company) entity. It renames only; it adds no feature and
changes no behaviour.

Postgres carries dependent FK constraints, indexes, and **RLS policies by
OID** through ``ALTER … RENAME``, so every policy keeps firing against the
renamed object without a drop/recreate. The one landmine is that a SQL
function's BODY is *not* rewritten when a table it references is renamed:
after the table rename, ``pulse_request_engagement_id``'s body still says
``from public.clients`` (now nonexistent). We ``create or replace`` the
function with the corrected body — ``create or replace`` keeps the same
OID, so the RLS policies that reference it keep working.

All RLS policy / index identifiers are renamed for clarity too (the OID
survives the rename, so the policies stay attached to the corrected
function and the renamed columns throughout).

``downgrade()`` reverses every step symmetrically, including restoring the
old ``pulse_request_client_id`` function body.
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Rename the table ────────────────────────────────────────────
    # FKs, indexes, RLS policies, grants, and the unique/PK constraints
    # all follow by OID — nothing else needs to move for them to work.
    op.execute("alter table public.clients rename to engagements;")

    # ── 2. Rename the child FK columns ─────────────────────────────────
    op.execute(
        "alter table public.cards rename column client_id to engagement_id;"
    )
    op.execute(
        "alter table public.responses rename column client_id to engagement_id;"
    )
    op.execute(
        "alter table public.uploads rename column client_id to engagement_id;"
    )

    # ── 3. Rename the helper function ──────────────────────────────────
    op.execute(
        "alter function public.pulse_request_client_id() "
        "rename to pulse_request_engagement_id;"
    )

    # ── 4. CRITICAL: fix the renamed function's BODY ───────────────────
    # The table rename in step 1 did NOT rewrite this function's body — it
    # still selected ``from public.clients``. ``create or replace`` keeps
    # the same OID so the RLS policies referencing it keep working, but
    # now with the corrected body that reads from ``public.engagements``.
    op.execute(
        """
        create or replace function public.pulse_request_engagement_id()
        returns uuid language sql stable as $$
            select id from public.engagements
            where token = public.pulse_request_token()
            limit 1;
        $$;
        """
    )

    # ── 5. Cosmetic: rename indexes + RLS policy identifiers ───────────
    # Purely for clarity — the objects already work post-rename. Index
    # renames keep their definitions (now over ``engagement_id``).
    op.execute(
        "alter index public.cards_client_order_idx "
        "rename to cards_engagement_order_idx;"
    )
    op.execute(
        "alter index public.responses_client_idx "
        "rename to responses_engagement_idx;"
    )
    op.execute(
        "alter index public.uploads_client_card_idx "
        "rename to uploads_engagement_card_idx;"
    )
    op.execute(
        "alter index public.clients_org_idx rename to engagements_org_idx;"
    )

    # anon self-access policies on the renamed table
    op.execute(
        "alter policy clients_self_read on public.engagements "
        "rename to engagements_self_read;"
    )
    op.execute(
        "alter policy clients_self_touch on public.engagements "
        "rename to engagements_self_touch;"
    )
    # member-scope policy on the renamed table (added in 0004)
    op.execute(
        "alter policy clients_member_scope on public.engagements "
        "rename to engagements_member_scope;"
    )


def downgrade() -> None:
    # Reverse order of upgrade(). Every rename has an exact inverse;
    # restore the old function body last.

    # ── 5. Restore policy + index identifiers ──────────────────────────
    op.execute(
        "alter policy engagements_member_scope on public.engagements "
        "rename to clients_member_scope;"
    )
    op.execute(
        "alter policy engagements_self_touch on public.engagements "
        "rename to clients_self_touch;"
    )
    op.execute(
        "alter policy engagements_self_read on public.engagements "
        "rename to clients_self_read;"
    )
    op.execute(
        "alter index public.engagements_org_idx rename to clients_org_idx;"
    )
    op.execute(
        "alter index public.uploads_engagement_card_idx "
        "rename to uploads_client_card_idx;"
    )
    op.execute(
        "alter index public.responses_engagement_idx "
        "rename to responses_client_idx;"
    )
    op.execute(
        "alter index public.cards_engagement_order_idx "
        "rename to cards_client_order_idx;"
    )

    # ── 2. Rename the child FK columns back ────────────────────────────
    op.execute(
        "alter table public.uploads rename column engagement_id to client_id;"
    )
    op.execute(
        "alter table public.responses rename column engagement_id to client_id;"
    )
    op.execute(
        "alter table public.cards rename column engagement_id to client_id;"
    )

    # ── 1. Rename the table back ───────────────────────────────────────
    # Must happen BEFORE the function body is restored — the ``create or
    # replace`` below parses ``from public.clients`` at creation time, so
    # the table has to exist under that name first.
    op.execute("alter table public.engagements rename to clients;")

    # ── 3. Rename the helper function back ─────────────────────────────
    op.execute(
        "alter function public.pulse_request_engagement_id() "
        "rename to pulse_request_client_id;"
    )

    # ── 4. Restore the function's ORIGINAL body ────────────────────────
    # Same OID-preserving ``create or replace``; restore the body that
    # selects from ``public.clients`` (renamed back above). The RLS
    # policies that reference this function keep working via its OID.
    op.execute(
        """
        create or replace function public.pulse_request_client_id()
        returns uuid language sql stable as $$
            select id from public.clients
            where token = public.pulse_request_token()
            limit 1;
        $$;
        """
    )
