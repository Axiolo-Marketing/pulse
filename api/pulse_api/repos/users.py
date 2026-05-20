"""Repository functions for `users` and `oauth_identities`.

All take an explicit session as the first argument — RLS doesn't apply to
these tables (no policies), so the session's role doesn't gate access.
Calling routes must enforce admin-permission themselves via the auth
middleware.
"""
import uuid
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from pulse_api.models import OAuthIdentity, User
from pulse_api.models._helpers import utcnow_naive


async def get_user_by_id(session: AsyncSession, user_id: str | uuid.UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str | None,
    name: str | None = None,
    is_admin: bool = False,
    email_verified_at: datetime | None = None,
) -> User:
    user = User(
        email=email.lower(),
        password_hash=password_hash,
        name=name,
        is_admin=is_admin,
        email_verified_at=email_verified_at,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def touch_last_login(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(last_login_at=utcnow_naive())
    )


async def mark_email_verified(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(User)
        .where(User.id == user_id, User.email_verified_at.is_(None))
        .values(email_verified_at=utcnow_naive())
    )


async def update_password_hash(
    session: AsyncSession, user_id: uuid.UUID, password_hash: str
) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(password_hash=password_hash)
    )


async def find_oauth_identity(
    session: AsyncSession, provider: str, provider_user_id: str
) -> OAuthIdentity | None:
    result = await session.execute(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == provider,
            OAuthIdentity.provider_user_id == provider_user_id,
        )
    )
    return result.scalar_one_or_none()


async def link_oauth_identity(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    provider: str,
    provider_user_id: str,
) -> OAuthIdentity:
    identity = OAuthIdentity(
        user_id=user_id, provider=provider, provider_user_id=provider_user_id
    )
    session.add(identity)
    await session.flush()
    await session.refresh(identity)
    return identity


# ── ClickUp OAuth + per-workspace webhook metadata ─────────────────────────
#
# All secret-bearing values pass through pulse_api.crypto. Repo callers
# work in plaintext; the encrypted form never leaves this module.

async def set_clickup_token(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    access_token: str,
    clickup_user_id: str | None,
) -> None:
    """Store the access token encrypted. Plaintext token never lands on disk."""
    from pulse_api import crypto

    enc = crypto.encrypt(access_token)
    await session.execute(
        text(
            "update public.users set clickup_access_token_enc = :enc, "
            "clickup_user_id = :uid where id = :id"
        ),
        {"enc": enc, "uid": clickup_user_id, "id": user_id},
    )


async def get_clickup_token(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    """Decrypts on read. Returns None if no token stored."""
    from pulse_api import crypto

    result = await session.execute(
        text(
            "select clickup_access_token_enc from public.users where id = :id"
        ),
        {"id": user_id},
    )
    enc = result.scalar()
    if enc is None:
        return None
    return crypto.decrypt(enc)


async def clear_clickup_token(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        text(
            "update public.users set clickup_access_token_enc = null, "
            "clickup_user_id = null where id = :id"
        ),
        {"id": user_id},
    )


async def save_clickup_workspace(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: str,
    workspace_name: str | None,
    webhook_id: str | None,
    webhook_secret: str | None,
) -> dict:
    """Upsert a (user_id, workspace_id) row. webhook_secret is encrypted
    before INSERT/UPDATE."""
    from pulse_api import crypto

    enc = crypto.encrypt(webhook_secret) if webhook_secret is not None else None
    result = await session.execute(
        text(
            """
            insert into public.clickup_workspaces
              (user_id, workspace_id, workspace_name, webhook_id, webhook_secret_enc)
            values (:uid, :wid, :name, :hid, :enc)
            on conflict (user_id, workspace_id) do update set
              workspace_name = excluded.workspace_name,
              webhook_id = excluded.webhook_id,
              webhook_secret_enc = excluded.webhook_secret_enc
            returning id::text, user_id::text, workspace_id, workspace_name, webhook_id, created_at
            """
        ),
        {"uid": user_id, "wid": workspace_id, "name": workspace_name, "hid": webhook_id, "enc": enc},
    )
    return dict(result.mappings().one())


async def list_clickup_workspaces_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> list[dict]:
    """Returns metadata only — webhook_secret is NEVER returned to callers
    (and would be ciphertext anyway)."""
    result = await session.execute(
        text(
            "select id::text, workspace_id, workspace_name, webhook_id, created_at "
            "from public.clickup_workspaces where user_id = :uid order by created_at"
        ),
        {"uid": user_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def delete_clickup_workspaces_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> list[dict]:
    """Returns the deleted rows (id, workspace_id, webhook_id) so the
    caller can also DELETE the webhook via the ClickUp API. Plaintext
    secrets never appear here — disconnect doesn't need them."""
    result = await session.execute(
        text(
            "delete from public.clickup_workspaces where user_id = :uid "
            "returning id::text, workspace_id, webhook_id"
        ),
        {"uid": user_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_workspace_webhook_secret(
    session: AsyncSession, workspace_id: str
) -> str | None:
    """Decrypt and return the webhook secret for the given workspace_id.
    Used at the webhook receiver to verify HMAC signatures. Returns None
    if no workspace is registered for this id (any user)."""
    from pulse_api import crypto

    result = await session.execute(
        text(
            "select webhook_secret_enc from public.clickup_workspaces "
            "where workspace_id = :wid limit 1"
        ),
        {"wid": workspace_id},
    )
    enc = result.scalar()
    if enc is None:
        return None
    return crypto.decrypt(enc)
