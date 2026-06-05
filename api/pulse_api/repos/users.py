"""Repository functions for `users` and `oauth_identities`.

All take an explicit session as the first argument — RLS doesn't apply to
these tables (no policies), so the session's role doesn't gate access.
Calling routes must enforce admin-permission themselves via the auth
middleware.
"""
import uuid
from datetime import datetime

from sqlalchemy import select, update
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
    email_verified_at: datetime | None = None,
) -> User:
    """Insert a new operator user row and return it.

    Admin powers are not on the user row anymore (PR 2 dropped
    ``is_admin``). Per-org operator status comes from a
    ``organization_memberships`` row inserted separately — the OAuth
    invite-acceptance path is the canonical example.
    """
    user = User(
        email=email.lower(),
        password_hash=password_hash,
        name=name,
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


async def update_name(
    session: AsyncSession, user_id: uuid.UUID, name: str | None
) -> None:
    await session.execute(update(User).where(User.id == user_id).values(name=name))


async def list_oauth_identities(
    session: AsyncSession, user_id: uuid.UUID
) -> list[OAuthIdentity]:
    result = await session.execute(
        select(OAuthIdentity)
        .where(OAuthIdentity.user_id == user_id)
        .order_by(OAuthIdentity.created_at)
    )
    return list(result.scalars().all())


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
