"""Repository functions for `api_keys`.

All paths run on a `pulse_admin` (BYPASSRLS) session — there is no RLS on
this table because keys carry no tenant rows themselves and `pulse_anon`
never reaches them. The auth gate that protects every caller of these
helpers is at the application layer (`get_current_user`).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.models import ApiKey
from pulse_api.models._helpers import utcnow_naive


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    prefix: str,
    key_hash: str,
    label: str,
) -> ApiKey:
    api_key = ApiKey(
        user_id=user_id,
        prefix=prefix,
        key_hash=key_hash,
        label=label,
    )
    session.add(api_key)
    await session.flush()
    await session.refresh(api_key)
    return api_key


async def list_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> list[ApiKey]:
    """All non-revoked keys for a user, newest first.

    Revoked keys are filtered out at the data layer — the UI has no need
    to render tombstones, and `DELETE` is hard-revoke from the operator's
    perspective. The row stays for forensic queries.
    """
    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


async def get_by_prefix(
    session: AsyncSession, prefix: str
) -> ApiKey | None:
    """Single active key matching this prefix, or None.

    Hits the partial index `api_keys_prefix_idx`. The auth path then
    constant-time-compares the candidate's hash against the supplied raw
    key; a `None` here means the wire prefix doesn't exist (or has been
    revoked) and the caller returns 401.
    """
    result = await session.execute(
        select(ApiKey).where(
            ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None)
        )
    )
    return result.scalar_one_or_none()


async def mark_used(
    session: AsyncSession, api_key_id: uuid.UUID
) -> None:
    """Best-effort `last_used_at` update — fire-and-forget from auth.

    Doesn't raise on no-op: if the key was revoked between the lookup and
    here (race), we just don't update. The session caller commits.
    """
    await session.execute(
        update(ApiKey)
        .where(ApiKey.id == api_key_id)
        .values(last_used_at=utcnow_naive())
    )


async def revoke(
    session: AsyncSession,
    *,
    api_key_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Set `revoked_at` on the key iff it belongs to `user_id`.

    Returns True if a row was updated. The `user_id` predicate is the
    cross-user-leak defense — passing someone else's key id is a no-op.
    Already-revoked keys also return False (no change).
    """
    result = await session.execute(
        update(ApiKey)
        .where(
            ApiKey.id == api_key_id,
            ApiKey.user_id == user_id,
            ApiKey.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow_naive())
    )
    return result.rowcount > 0
