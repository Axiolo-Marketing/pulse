"""Repository helpers for `card_generations` — the reactive-cards
generation lifecycle record, dedup lock, and LLM cost ledger (migration
0017). `pulse_anon` / `pulse_member` only have SELECT grants on this
table; every write in this module runs on the BYPASSRLS `admin_engine`
session the generation engine (`pulse_api.reactive`) opens for itself.
"""
import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


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


async def claim_pending(
    session: AsyncSession,
    *,
    org_id: str,
    engagement_id: str,
    recipient_id: str,
    response_id: str,
    card_id: str,
    trigger_hash: str,
) -> str | None:
    """Insert a `pending` generation row, claiming the idempotency lock.

    The unique `(response_id, trigger_hash)` constraint (migration 0017)
    means an identical re-save of the same correction — same triggering
    response, same normalized text — loses this INSERT and gets `None`
    back rather than a second generation. The caller MUST commit this
    claim immediately, before making the LLM request: two concurrent
    saves of the same correction race on the unique constraint (a DB-level
    guarantee), not on wall-clock timing.

    Returns:
        The new row's id, or `None` if a generation for this exact
        `(response_id, trigger_hash)` pair already exists.
    """
    if not (
        _valid_uuid(org_id)
        and _valid_uuid(engagement_id)
        and _valid_uuid(recipient_id)
        and _valid_uuid(response_id)
        and _valid_uuid(card_id)
    ):
        return None
    result = await session.execute(
        text(
            """
            insert into public.card_generations
              (org_id, engagement_id, recipient_id, response_id, card_id,
               trigger_hash, status)
            values
              (cast(:org as uuid), cast(:eid as uuid), cast(:rid as uuid),
               cast(:resp as uuid), cast(:cid as uuid), :hash, 'pending')
            on conflict (response_id, trigger_hash) do nothing
            returning id::text
            """
        ),
        {
            "org": org_id,
            "eid": engagement_id,
            "rid": recipient_id,
            "resp": response_id,
            "cid": card_id,
            "hash": trigger_hash,
        },
    )
    return result.scalar_one_or_none()


async def count_for_recipient(session: AsyncSession, recipient_id: str) -> int:
    """How many generation ATTEMPTS (any status — pending, completed,
    skipped, or failed) have been made for this recipient across the
    engagement's whole lifetime. This is the query the per-recipient
    lifetime cap (`reactive_max_generated_per_recipient`) gates on.

    Deliberately counts rows here rather than `cards.count_generated_for_
    recipient` (which only counts successfully-CREATED `source='ai'`
    cards): a `skipped` generation (the model said `needs_followup:
    false`, or every proposal failed server-side validation) or a
    `failed` one (API error/timeout) still made a real, billed-or-
    attempted Anthropic call and still claimed a row here, but creates NO
    card. Capping on created cards alone lets a caller who keeps
    submitting distinct corrections (each a fresh `trigger_hash`, so the
    dedup lock never engages) rack up unbounded billed calls forever,
    since the system prompt's "prefer proposing nothing" bar means most
    real corrections legitimately skip. Counting attempts is what
    actually bounds LLM call volume / cost per recipient.
    """
    if not _valid_uuid(recipient_id):
        return 0
    result = await session.execute(
        text(
            "select count(*) from public.card_generations "
            "where recipient_id = cast(:rid as uuid)"
        ),
        {"rid": recipient_id},
    )
    return result.scalar_one()


async def _update_status(
    session: AsyncSession,
    generation_id: str,
    *,
    status: str,
    model: str | None,
    error: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: Decimal | None,
    created_card_ids: list[str] | None,
) -> None:
    if not _valid_uuid(generation_id):
        return
    await session.execute(
        text(
            """
            update public.card_generations set
              status = :status,
              model = :model,
              error = :error,
              input_tokens = :itok,
              output_tokens = :otok,
              cost_usd = :cost,
              created_card_ids = coalesce(cast(:ids as uuid[]), created_card_ids),
              completed_at = now()
            where id = cast(:gid as uuid)
            """
        ),
        {
            "gid": generation_id,
            "status": status,
            "model": model,
            "error": error,
            "itok": input_tokens,
            "otok": output_tokens,
            "cost": cost_usd,
            "ids": created_card_ids,
        },
    )


async def mark_completed(
    session: AsyncSession,
    generation_id: str,
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: Decimal | None,
    created_card_ids: list[str],
) -> None:
    """Terminal state: at least one AI card was created for this
    generation. `created_card_ids` reflects what actually got inserted
    (server-side validation may have dropped some of the model's
    proposals), not the raw proposal count."""
    await _update_status(
        session,
        generation_id,
        status="completed",
        model=model,
        error=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        created_card_ids=created_card_ids,
    )


async def mark_skipped(
    session: AsyncSession,
    generation_id: str,
    *,
    model: str | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
    error: str | None = None,
) -> None:
    """Terminal state: no follow-up was warranted (the model said so, a
    safety refusal declined the request, or every proposal failed
    server-side validation). Not a failure — this is the expected common
    case for most corrections."""
    await _update_status(
        session,
        generation_id,
        status="skipped",
        model=model,
        error=error,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        created_card_ids=None,
    )


async def mark_failed(
    session: AsyncSession,
    generation_id: str,
    *,
    error: str,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: Decimal | None = None,
) -> None:
    """Terminal state: the call itself broke (SDK exception, timeout, or
    the model was cut off by `max_tokens` before finishing). The
    respondent's own answer is completely unaffected — only this ledger
    row records the failure, invisibly to the respondent."""
    await _update_status(
        session,
        generation_id,
        status="failed",
        model=model,
        error=error,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        created_card_ids=None,
    )


async def usage_report(session: AsyncSession, *, days: int) -> list[dict]:
    """Per-org reactive-cards usage + cost aggregates over a trailing window.

    Backs ``GET /api/superadmin/reactive-usage`` — the operating-cost
    monitoring surface (not billing; see the route docstring). One
    ``GROUP BY`` over ``card_generations`` joined to ``organizations``,
    filtered to the trailing ``days``-day window by ``created_at``
    (served by the ``(org_id, created_at)`` index from migration 0017).
    Superadmin-only at the route layer; runs on the BYPASSRLS
    ``pulse_admin`` session since the report spans every org.

    ``make_interval(days => :days)`` builds the window bound rather than
    string-concatenating ``:days`` into an interval literal — the latter
    needs the bound parameter to already be text, which asyncpg doesn't
    coerce automatically for an ``integer`` bind.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        days: Trailing window size in days (already validated/clamped
            at the route layer).

    Returns:
        One dict per org that had at least one generation in the
        window (an inner join — orgs with zero activity are omitted):
        ``{org_id, org_name, generations, completed, skipped, failed,
        input_tokens, output_tokens, cost_usd}``. ``cost_usd`` is a
        ``Decimal`` (``0`` when every generation in the window recorded
        no cost, e.g. all dev-fake-mode calls). The route sums these
        rows to build the all-orgs totals row rather than issuing a
        second query.
    """
    result = await session.execute(
        text(
            """
            select
              org.id::text as org_id,
              org.name as org_name,
              count(*)::int as generations,
              count(*) filter (where cg.status = 'completed')::int as completed,
              count(*) filter (where cg.status = 'skipped')::int as skipped,
              count(*) filter (where cg.status = 'failed')::int as failed,
              coalesce(sum(cg.input_tokens), 0)::bigint as input_tokens,
              coalesce(sum(cg.output_tokens), 0)::bigint as output_tokens,
              coalesce(sum(cg.cost_usd), 0) as cost_usd
            from public.card_generations cg
            join public.organizations org on org.id = cg.org_id
            where cg.created_at >= now() - make_interval(days => :days)
            group by org.id, org.name
            order by generations desc, org.name
            """
        ),
        {"days": int(days)},
    )
    return [dict(r) for r in result.mappings().all()]


async def usage_report_by_engagement(
    session: AsyncSession, *, days: int
) -> list[dict]:
    """Per-engagement reactive-cards usage + cost aggregates over a
    trailing window — the superadmin drill-down alongside the per-org
    ``usage_report``.

    One ``GROUP BY`` over ``card_generations`` joined to ``engagements``
    (for the label + org attribution) and ``clients`` (the label
    fallback), filtered to the trailing ``days``-day window the same way
    ``usage_report`` is. An inner join throughout means engagements with
    zero generations in the window are simply absent from the result —
    no zero-rows to filter out in Python.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        days: Trailing window size in days (already validated/clamped
            at the route layer).

    Returns:
        One dict per engagement that had at least one generation in the
        window: ``{engagement_id, engagement_label, org_id, org_name,
        generations, input_tokens, output_tokens, cost_usd}``.
        ``engagement_label`` is ``engagement_name`` when set (non-blank),
        else the owning client's name. ``cost_usd`` is a ``Decimal``
        (``0`` when every generation in the window recorded no cost,
        e.g. all dev-fake-mode calls).
    """
    result = await session.execute(
        text(
            """
            select
              e.id::text as engagement_id,
              coalesce(nullif(trim(e.engagement_name), ''), cl.name)
                as engagement_label,
              org.id::text as org_id,
              org.name as org_name,
              count(*)::int as generations,
              coalesce(sum(cg.input_tokens), 0)::bigint as input_tokens,
              coalesce(sum(cg.output_tokens), 0)::bigint as output_tokens,
              coalesce(sum(cg.cost_usd), 0) as cost_usd
            from public.card_generations cg
            join public.engagements e on e.id = cg.engagement_id
            join public.clients cl on cl.id = e.client_id
            join public.organizations org on org.id = cg.org_id
            where cg.created_at >= now() - make_interval(days => :days)
            group by e.id, e.engagement_name, cl.name, org.id, org.name
            order by generations desc, engagement_label
            """
        ),
        {"days": int(days)},
    )
    return [dict(r) for r in result.mappings().all()]


async def usage_report_monthly(
    session: AsyncSession, *, months: int = 6
) -> list[dict]:
    """Monthly per-org reactive-cards usage + cost aggregates, independent
    of the 30/90-day window selector — the superadmin's month-over-month
    cost trend.

    One ``GROUP BY`` over ``card_generations`` joined to
    ``organizations``, bucketed by ``date_trunc('month', created_at)`` and
    windowed to the trailing ``months`` CALENDAR months (the current
    month plus ``months - 1`` prior full months) — served by the
    ``(org_id, created_at)`` index from migration 0017, same as
    ``usage_report``.

    Args:
        session: ``pulse_admin`` session (BYPASSRLS).
        months: Number of trailing calendar months to include (default
            6). Not caller-configurable at the route layer — this report
            is a fixed "last 6 months" view.

    Returns:
        One dict per ``(month, org)`` pair that had at least one
        generation: ``{month, org_id, org_name, generations,
        input_tokens, output_tokens, cost_usd}``, most recent month
        first (ties broken by org name). ``month`` is a ``"YYYY-MM"``
        text label. ``cost_usd`` is a ``Decimal`` (``0`` when every
        generation in that month recorded no cost).
    """
    result = await session.execute(
        text(
            """
            select
              to_char(date_trunc('month', cg.created_at), 'YYYY-MM')
                as month,
              org.id::text as org_id,
              org.name as org_name,
              count(*)::int as generations,
              coalesce(sum(cg.input_tokens), 0)::bigint as input_tokens,
              coalesce(sum(cg.output_tokens), 0)::bigint as output_tokens,
              coalesce(sum(cg.cost_usd), 0) as cost_usd
            from public.card_generations cg
            join public.organizations org on org.id = cg.org_id
            where cg.created_at >= date_trunc('month', now())
              - make_interval(months => cast(:months as integer) - 1)
            group by date_trunc('month', cg.created_at), org.id, org.name
            order by date_trunc('month', cg.created_at) desc, org.name
            """
        ),
        {"months": int(months)},
    )
    return [dict(r) for r in result.mappings().all()]


async def list_for_my_recipient(
    session: AsyncSession, response_id: str | None = None
) -> list[dict]:
    """Anon poll surface (`GET /api/generations`). RLS
    (`card_generations_self_read`, migration 0017) scopes this to the
    caller's own recipient for free — no explicit recipient filter needed
    here. Optionally narrowed to one `response_id`, since the deck polls
    right after a single save and only cares about that response's
    generation."""
    if response_id is not None and not _valid_uuid(response_id):
        return []
    query = (
        "select id::text, response_id::text, status, "
        "created_card_ids::text[] as card_ids, created_at, completed_at "
        "from public.card_generations"
    )
    params: dict = {}
    if response_id is not None:
        query += " where response_id = cast(:resp as uuid)"
        params["resp"] = response_id
    query += " order by created_at desc"
    result = await session.execute(text(query), params)
    return [dict(r) for r in result.mappings().all()]
