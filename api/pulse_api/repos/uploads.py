"""Repository helpers for `uploads`. Reads only here — create/delete land
with the file-storage phase, since they need the disk-side counterpart."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UPLOAD_COLS = (
    "id::text, card_id::text, engagement_id::text, recipient_id::text, file_name, "
    "file_size_bytes, storage_path, mime_type, kind, uploaded_at"
)


async def list_for_my_engagement(session: AsyncSession) -> list[dict]:
    result = await session.execute(
        text(f"select {UPLOAD_COLS} from public.uploads order by uploaded_at")
    )
    return [dict(r) for r in result.mappings().all()]


async def list_for_engagement(session: AsyncSession, engagement_id: str) -> list[dict]:
    try:
        result = await session.execute(
            text(
                f"select {UPLOAD_COLS} from public.uploads "
                "where engagement_id = cast(:cid as uuid) order by uploaded_at"
            ),
            {"cid": engagement_id},
        )
    except Exception:
        return []
    return [dict(r) for r in result.mappings().all()]


async def delete_all_for_engagement(session: AsyncSession, engagement_id: str) -> list[dict]:
    """Admin reset: delete every upload row for one engagement and return
    the deleted rows so the route can remove the on-disk files. BYPASSRLS
    session with an explicit engagement_id filter."""
    result = await session.execute(
        text(
            f"delete from public.uploads where engagement_id = cast(:cid as uuid) "
            f"returning {UPLOAD_COLS}"
        ),
        {"cid": engagement_id},
    )
    return [dict(r) for r in result.mappings().all()]


# ── Client-mode (RLS-filtered) ─────────────────────────────────────────────


async def create_upload(
    session: AsyncSession,
    *,
    card_id: str,
    file_name: str,
    file_size_bytes: int,
    storage_path: str,
    mime_type: str | None,
    kind: str = "file",
) -> dict | None:
    """Insert an uploads row. recipient_id + engagement_id are derived
    server-side from `pulse_request_recipient_id()` /
    `pulse_request_engagement_id()` so the wire body can't address another
    recipient's uploads. ``kind`` discriminates answer files (``'file'``)
    from recorded voice answers (``'voice'``). Returns None if card_id is
    invalid (cast fails) or RLS rejects the insert because the card
    doesn't belong to caller."""
    try:
        result = await session.execute(
            text(
                f"""
                insert into public.uploads
                  (card_id, engagement_id, recipient_id, file_name, file_size_bytes,
                   storage_path, mime_type, kind)
                values
                  (cast(:cid as uuid), public.pulse_request_engagement_id(),
                   public.pulse_request_recipient_id(),
                   :fn, :sz, :sp, :mt, :kind)
                returning {UPLOAD_COLS}
                """
            ),
            {
                "cid": card_id,
                "fn": file_name,
                "sz": file_size_bytes,
                "sp": storage_path,
                "mt": mime_type,
                "kind": kind,
            },
        )
    except Exception:
        return None
    return dict(result.mappings().one())


async def get_by_id_for_caller(session: AsyncSession, upload_id: str) -> dict | None:
    """RLS narrows to the caller's uploads. Returns None for unknown
    or other-engagement uploads — same response so existence isn't leaked."""
    try:
        result = await session.execute(
            text(f"select {UPLOAD_COLS} from public.uploads where id = cast(:uid as uuid)"),
            {"uid": upload_id},
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def delete_for_caller(session: AsyncSession, upload_id: str) -> dict | None:
    """RLS gates the DELETE. Returns the deleted row (so the route knows
    which on-disk file to also remove), or None if no match."""
    try:
        result = await session.execute(
            text(
                f"delete from public.uploads where id = cast(:uid as uuid) "
                f"returning {UPLOAD_COLS}"
            ),
            {"uid": upload_id},
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Admin-mode (BYPASSRLS) ─────────────────────────────────────────────────


async def admin_get_by_id(session: AsyncSession, upload_id: str) -> dict | None:
    try:
        result = await session.execute(
            text(f"select {UPLOAD_COLS} from public.uploads where id = cast(:uid as uuid)"),
            {"uid": upload_id},
        )
    except Exception:
        return None
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Helpers used by both modes ─────────────────────────────────────────────


async def get_current_engagement_id(session: AsyncSession) -> str | None:
    """Resolve the current request's engagement_id via the helper SQL function.
    Returns None if no token is bound. Used by client-facing upload routes
    that need to construct the on-disk path."""
    result = await session.execute(text("select public.pulse_request_engagement_id()::text"))
    return result.scalar()


async def get_current_recipient_id(session: AsyncSession) -> str | None:
    """Resolve the current request's recipient_id via the helper SQL function.
    Returns None if no token is bound. Used alongside the engagement_id to
    build the per-recipient on-disk upload path."""
    result = await session.execute(text("select public.pulse_request_recipient_id()::text"))
    return result.scalar()


async def card_belongs_to_caller(session: AsyncSession, card_id: str) -> bool:
    """RLS-filtered existence check — analogue of the one in responses.py.
    Returns False on malformed UUIDs without raising."""
    try:
        result = await session.execute(
            text("select 1 from public.cards where id = cast(:cid as uuid)"),
            {"cid": card_id},
        )
    except Exception:
        return False
    return result.scalar() is not None
