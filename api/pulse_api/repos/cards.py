"""Repository helpers for `cards`."""
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CARD_COLS = (
    "id::text, engagement_id::text, order_index, category, title, context, question, "
    "response_type, options, default_value, skip_allowed, attachment_path, created_at"
)


async def list_for_my_engagement(session: AsyncSession) -> list[dict]:
    """RLS narrows this to cards belonging to the token-bound engagement."""
    result = await session.execute(
        text(f"select {CARD_COLS} from public.cards order by order_index")
    )
    return [dict(r) for r in result.mappings().all()]


# ── admin-mode helpers (BYPASSRLS — explicit engagement_id filters) ────────


async def list_for_engagement(session: AsyncSession, engagement_id: str) -> list[dict]:
    try:
        result = await session.execute(
            text(
                f"select {CARD_COLS} from public.cards "
                "where engagement_id = cast(:cid as uuid) order by order_index"
            ),
            {"cid": engagement_id},
        )
    except Exception:
        return []
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
) -> dict | None:
    """Insert one card. ``org_id`` is NOT NULL on the table (0005), so
    every call must thread the active org's id through. Callers running
    on a ``pulse_member`` session pull this from the resolved
    membership; ``pulse_admin`` callers (none in current code, reserved
    for superadmin work) must pick the right org explicitly.
    """
    try:
        result = await session.execute(
            text(
                f"""
                insert into public.cards
                  (engagement_id, order_index, category, title, context, question,
                   response_type, options, default_value, skip_allowed,
                   attachment_path, org_id)
                values
                  (cast(:cid as uuid),
                   coalesce((select max(order_index) from public.cards where engagement_id = cast(:cid as uuid)), 0) + 1,
                   :cat, :title, :ctx, :q, :rt,
                   cast(:opts as jsonb), :dv, :sa, :ap, cast(:org as uuid))
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
            },
        )
    except Exception:
        return None
    return dict(result.mappings().one())


async def update_card(session: AsyncSession, card_id: str, fields: dict) -> dict | None:
    """Partial update. `response_type` is intentionally not in the
    accepted-fields whitelist at the route layer — changing it would
    invalidate any existing responses whose `response_value` shape is
    derived from the type."""
    if not fields:
        try:
            result = await session.execute(
                text(f"select {CARD_COLS} from public.cards where id = cast(:cid as uuid)"),
                {"cid": card_id},
            )
        except Exception:
            return None
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
    try:
        result = await session.execute(
            text(
                f"update public.cards set {', '.join(set_clauses)} "
                f"where id = cast(:cid as uuid) returning {CARD_COLS}"
            ),
            params,
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def delete_card(session: AsyncSession, card_id: str) -> bool:
    try:
        result = await session.execute(
            text("delete from public.cards where id = cast(:cid as uuid)"),
            {"cid": card_id},
        )
    except Exception:
        return False
    return result.rowcount > 0
