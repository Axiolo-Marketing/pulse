"""Repository helpers for `cards`."""
import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CARD_COLS = (
    "id::text, engagement_id::text, order_index, category, title, context, question, "
    "response_type, options, default_value, skip_allowed, attachment_path, created_at, "
    "recipient_id::text, source, generated_from_response_id::text"
)


def _valid_uuid(value: str) -> bool:
    """True if `value` parses as a UUID.

    Used as an explicit guard at function entry in place of a blanket
    `except Exception` around the query — a malformed id is a routine
    "not found" case, but a broad catch there also hid genuine DB errors
    (connection loss, constraint violations, RLS refusals) as a silent
    None/[]/False with no log. Guarding up front lets real errors
    propagate to the 5xx logger while preserving the same not-found
    response for bad ids.
    """
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def list_for_my_engagement(session: AsyncSession) -> list[dict]:
    """RLS narrows this to cards belonging to the token-bound engagement."""
    result = await session.execute(
        text(f"select {CARD_COLS} from public.cards order by order_index")
    )
    return [dict(r) for r in result.mappings().all()]


# ── admin-mode helpers (BYPASSRLS — explicit engagement_id filters) ────────


async def list_for_engagement(session: AsyncSession, engagement_id: str) -> list[dict]:
    if not _valid_uuid(engagement_id):
        return []
    result = await session.execute(
        text(
            f"select {CARD_COLS} from public.cards "
            "where engagement_id = cast(:cid as uuid) order by order_index"
        ),
        {"cid": engagement_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def create_card(
    session: AsyncSession,
    *,
    engagement_id: str,
    category: str,
    title: str,
    context: str,
    question: str,
    response_type: str,
    options: list[str] | None,
    default_value: str | None,
    skip_allowed: bool,
    attachment_path: str | None,
    org_id: str,
    recipient_id: str | None = None,
    source: str = "operator",
    generated_from_response_id: str | None = None,
) -> dict | None:
    """Insert one card. ``org_id`` is NOT NULL on the table (0005), so
    every call must thread the active org's id through. Callers running
    on a ``pulse_member`` session pull this from the resolved
    membership; ``pulse_admin`` callers (none in current code, reserved
    for superadmin work) must pick the right org explicitly.

    ``recipient_id`` / ``source`` / ``generated_from_response_id`` are
    new in migration 0017 and default to the operator-authored,
    engagement-shared shape every card had before reactive cards existed
    — existing callers need no changes. The generation engine (PR 2) is
    the only caller that passes ``recipient_id`` + ``source="ai"`` +
    ``generated_from_response_id``.
    """
    if not (_valid_uuid(engagement_id) and _valid_uuid(org_id)):
        return None
    result = await session.execute(
        text(
            f"""
            insert into public.cards
              (engagement_id, order_index, category, title, context, question,
               response_type, options, default_value, skip_allowed,
               attachment_path, org_id, recipient_id, source, generated_from_response_id)
            values
              (cast(:cid as uuid),
               coalesce((select max(order_index) from public.cards where engagement_id = cast(:cid as uuid)), 0) + 1,
               :cat, :title, :ctx, :q, :rt,
               cast(:opts as jsonb), :dv, :sa, :ap, cast(:org as uuid),
               cast(:rid as uuid), :src, cast(:gfr as uuid))
            returning {CARD_COLS}
            """
        ),
        {
            "cid": engagement_id,
            "cat": category,
            "title": title,
            "ctx": context,
            "q": question,
            "rt": response_type,
            "opts": json.dumps(options) if options is not None else None,
            "dv": default_value,
            "sa": skip_allowed,
            "ap": attachment_path,
            "org": org_id,
            "rid": recipient_id,
            "src": source,
            "gfr": generated_from_response_id,
        },
    )
    return dict(result.mappings().one())


async def update_card(session: AsyncSession, card_id: str, fields: dict) -> dict | None:
    """Partial update. `response_type` is intentionally not in the
    accepted-fields whitelist at the route layer — changing it would
    invalidate any existing responses whose `response_value` shape is
    derived from the type."""
    if not _valid_uuid(card_id):
        return None
    if not fields:
        result = await session.execute(
            text(f"select {CARD_COLS} from public.cards where id = cast(:cid as uuid)"),
            {"cid": card_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    # JSONB columns need explicit casts on bound params
    if "options" in fields and fields["options"] is not None:
        fields = {**fields, "options": json.dumps(fields["options"])}

    set_clauses = []
    for k in fields:
        if k == "options":
            set_clauses.append("options = cast(:options as jsonb)")
        else:
            set_clauses.append(f"{k} = :{k}")
    params = {"cid": card_id, **fields}
    result = await session.execute(
        text(
            f"update public.cards set {', '.join(set_clauses)} "
            f"where id = cast(:cid as uuid) returning {CARD_COLS}"
        ),
        params,
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def delete_card(session: AsyncSession, card_id: str) -> bool:
    if not _valid_uuid(card_id):
        return False
    result = await session.execute(
        text("delete from public.cards where id = cast(:cid as uuid)"),
        {"cid": card_id},
    )
    return result.rowcount > 0


async def peek_title(session: AsyncSession, card_id: str) -> str | None:
    """Return the card's title, or ``None`` if the row doesn't resolve.

    Used by delete handlers (REST + MCP) to capture the title BEFORE the
    row cascades away, so the audit log can render a human-readable
    label instead of a bare UUID once the row is gone. RLS scopes the
    read to the caller's session (org-scoped for admin/MCP callers).
    """
    if not _valid_uuid(card_id):
        return None
    result = await session.execute(
        text("select title from public.cards where id = cast(:cid as uuid)"),
        {"cid": card_id},
    )
    row = result.mappings().one_or_none()
    return None if row is None else row.get("title")


async def delete_generated_for_engagement(session: AsyncSession, engagement_id: str) -> int:
    """Delete every AI-generated card (``source = 'ai'``) on one engagement.

    Used by the engagement reset route, run BEFORE the responses/uploads
    wipe: a respondent's answer to a generated card cascades away with
    the card itself (``responses.card_id`` is ``on delete cascade``), and
    any ``card_generations`` row tied to that answered card's response
    cascades too — the remaining generation rows (keyed to the
    *triggering* response, on an operator card) are cleaned up when the
    caller subsequently wipes all responses for the engagement. Returns
    the number of cards removed, for the audit metadata."""
    if not _valid_uuid(engagement_id):
        return 0
    result = await session.execute(
        text(
            "delete from public.cards "
            "where engagement_id = cast(:cid as uuid) and source = 'ai'"
        ),
        {"cid": engagement_id},
    )
    return result.rowcount or 0


async def count_generated_for_recipient(session: AsyncSession, recipient_id: str) -> int:
    """How many AI-generated cards a recipient already has, across the
    engagement's whole lifetime — the per-recipient lifetime cap the
    generation engine gates on (``reactive_max_generated_per_recipient``,
    PR 2). Uses the partial ``cards_recipient_idx`` index."""
    if not _valid_uuid(recipient_id):
        return 0
    result = await session.execute(
        text(
            "select count(*) from public.cards "
            "where recipient_id = cast(:rid as uuid) and source = 'ai'"
        ),
        {"rid": recipient_id},
    )
    return result.scalar_one()


async def has_unanswered_ai_followup(
    session: AsyncSession, *, response_id: str, recipient_id: str
) -> bool:
    """Duplicate-follow-up guard for `reactive.run_generation`.

    `responses` upserts on the `(card_id, recipient_id)` unique
    constraint, so a respondent who edits the same card's correction a
    second time reuses the SAME `response_id` — only the text (and
    therefore the `card_generations` dedup key, `trigger_hash`) changes.
    That means the `(response_id, trigger_hash)` dedup claim does NOT
    catch a re-edit: it would happily start a second generation for the
    same underlying correction while the first one's follow-up card is
    still sitting unanswered in the respondent's deck, producing two
    near-duplicate follow-ups (observed live). This check closes that
    gap: `run_generation` calls it before the dedup claim and skips
    generating outright if it returns True. Once the respondent answers
    or skips the earlier follow-up, a fresh edit is free to generate
    again.

    True iff at least one `source='ai'` card exists with
    `generated_from_response_id = response_id` for this recipient that
    has no `responses` row in `answered`/`skipped` state — i.e. it's
    still outstanding. Cross-checks `recipient_id` on both sides
    (defense in depth; a recipient-scoped AI card and its own answer
    should never disagree on whose it is)."""
    if not (_valid_uuid(response_id) and _valid_uuid(recipient_id)):
        return False
    result = await session.execute(
        text(
            """
            select exists (
              select 1 from public.cards c
              where c.generated_from_response_id = cast(:resp as uuid)
                and c.recipient_id = cast(:rid as uuid)
                and c.source = 'ai'
                and not exists (
                  select 1 from public.responses r
                  where r.card_id = c.id
                    and r.recipient_id = cast(:rid as uuid)
                    and r.state in ('answered', 'skipped')
                )
            )
            """
        ),
        {"resp": response_id, "rid": recipient_id},
    )
    return bool(result.scalar_one())
