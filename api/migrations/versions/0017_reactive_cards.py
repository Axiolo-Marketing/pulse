"""Reactive cards: schema + scoped-card read path

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-13

Lays the schema groundwork for LLM-generated follow-up cards (PR 1 of the
"reactive cards" feature — see
``~/.claude/plans/when-a-respondent-makes-virtual-pelican.md``). This
migration adds no behaviour on its own: nothing writes AI-sourced cards
yet (that's the generation engine, PR 2). It only:

1. Lets a ``cards`` row be scoped to a single recipient instead of the
   whole engagement (``recipient_id``, NULL = shared, the status quo for
   every existing card), tags provenance (``source``: ``'operator'`` or
   ``'ai'``), and records which correction a generated card came from
   (``generated_from_response_id``).
2. Adds ``card_generations`` — the generation lifecycle record, the
   ``(response_id, trigger_hash)`` idempotency lock, and the LLM cost
   ledger (token counts + a computed dollar estimate per call).
3. Narrows the ``cards`` anon read policy so a recipient only sees cards
   that are either shared (``recipient_id is null``) or scoped to them —
   a strict narrowing of the existing engagement-grain predicate, so
   every pre-existing (unscoped) card is unaffected.
4. Adds the two feature flags that gate the whole feature end-to-end:
   ``organizations.reactive_cards_allowed`` (superadmin-managed) and
   ``engagements.reactive_cards_enabled`` (org member, per engagement).
   Both default ``false`` — a no-op for every existing org/engagement.

Hand-written raw SQL (``op.execute``), like 0015/0016 — autogen can't
represent RLS / partial indexes / check constraints reliably in this
repo's style.
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. cards: recipient scoping + provenance ────────────────────────
    op.execute(
        "alter table public.cards add column recipient_id uuid "
        "references public.recipients(id) on delete cascade;"
        # NULL = engagement-shared (every existing card, and every
        # operator-authored card going forward).
    )
    op.execute(
        "alter table public.cards add column source text not null default 'operator' "
        "check (source in ('operator', 'ai'));"
    )
    op.execute(
        "alter table public.cards add column generated_from_response_id uuid "
        "references public.responses(id) on delete set null;"
        # Provenance / deck-splice parent link: the correction response
        # that triggered this card's generation. ``set null`` (not
        # cascade) — losing the triggering response shouldn't delete the
        # card it produced.
    )
    op.execute(
        "create index cards_recipient_idx on public.cards (recipient_id) "
        "where recipient_id is not null;"
    )

    # ── 2. card_generations: lifecycle record + dedup lock + cost ledger ─
    op.execute(
        """
        create table public.card_generations (
            id                 uuid primary key default gen_random_uuid(),
            org_id             uuid not null references public.organizations(id) on delete cascade,
            engagement_id      uuid not null references public.engagements(id) on delete cascade,
            recipient_id       uuid not null references public.recipients(id) on delete cascade,
            response_id        uuid not null references public.responses(id) on delete cascade,
            card_id            uuid not null references public.cards(id) on delete cascade,
            trigger_hash       text not null,
            status             text not null default 'pending'
                                 check (status in ('pending', 'completed', 'skipped', 'failed')),
            model              text,
            error              text,
            input_tokens       integer,
            output_tokens      integer,
            cost_usd           numeric(10, 6),
            created_card_ids   uuid[] not null default '{}',
            created_at         timestamptz not null default now(),
            completed_at       timestamptz,
            unique (response_id, trigger_hash)
        );
        """
    )
    op.execute(
        "create index card_generations_org_created_idx "
        "on public.card_generations (org_id, created_at);"
    )
    op.execute(
        "create index card_generations_recipient_idx "
        "on public.card_generations (recipient_id);"
    )
    op.execute(
        "create index card_generations_engagement_idx "
        "on public.card_generations (engagement_id);"
    )

    # ── 3. cards RLS: narrow cards_self_read to the recipient grain ────
    # Existing predicate is the engagement-grain check installed in 0001
    # (and carried through 0012's table/column renames); AND in the
    # recipient check so a scoped card is only visible to the recipient
    # it belongs to, while every shared card (recipient_id is null) stays
    # visible to the whole engagement exactly as before.
    op.execute("drop policy cards_self_read on public.cards;")
    op.execute(
        """
        create policy cards_self_read on public.cards
            for select to pulse_anon
            using (
                engagement_id = public.pulse_request_engagement_id()
                and (
                    recipient_id is null
                    or recipient_id = public.pulse_request_recipient_id()
                )
            );
        """
    )

    # ── 4. card_generations RLS + grants ─────────────────────────────────
    # No INSERT policy/grant for pulse_anon anywhere — generation rows are
    # only ever written by the BYPASSRLS admin engine (PR 2).
    op.execute("alter table public.card_generations enable row level security;")
    op.execute(
        """
        create policy card_generations_member_scope on public.card_generations
            for select to pulse_member
            using (org_id = public.pulse_request_org_id());
        """
    )
    op.execute(
        """
        create policy card_generations_self_read on public.card_generations
            for select to pulse_anon
            using (recipient_id = public.pulse_request_recipient_id());
        """
    )
    op.execute("grant select on public.card_generations to pulse_anon;")
    op.execute("grant select on public.card_generations to pulse_member;")
    op.execute("grant all on public.card_generations to pulse_admin;")

    # ── 5. Feature flags (all default false — no-op for existing rows) ──
    op.execute(
        "alter table public.organizations add column reactive_cards_allowed "
        "boolean not null default false;"
    )
    op.execute(
        "alter table public.engagements add column reactive_cards_enabled "
        "boolean not null default false;"
    )


def downgrade() -> None:
    # Reverse order of upgrade().

    # ── 5'. Feature flags ────────────────────────────────────────────────
    op.execute(
        "alter table public.engagements drop column if exists reactive_cards_enabled;"
    )
    op.execute(
        "alter table public.organizations drop column if exists reactive_cards_allowed;"
    )

    # ── 4'. card_generations RLS + grants (table drop below removes the rest) ─
    op.execute("drop policy if exists card_generations_self_read on public.card_generations;")
    op.execute("drop policy if exists card_generations_member_scope on public.card_generations;")

    # ── 3'. Restore the pre-0017 cards_self_read (engagement grain only) ─
    op.execute("drop policy cards_self_read on public.cards;")
    op.execute(
        """
        create policy cards_self_read on public.cards
            for select to pulse_anon
            using (engagement_id = public.pulse_request_engagement_id());
        """
    )

    # ── 2'. card_generations table (drops its indexes + policies + grants) ─
    op.execute("drop table if exists public.card_generations;")

    # ── 1'. cards: drop recipient scoping + provenance ──────────────────
    op.execute("drop index if exists public.cards_recipient_idx;")
    op.execute(
        "alter table public.cards drop column if exists generated_from_response_id;"
    )
    op.execute("alter table public.cards drop column if exists source;")
    op.execute("alter table public.cards drop column if exists recipient_id;")
