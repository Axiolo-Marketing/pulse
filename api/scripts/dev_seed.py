"""Idempotent dev-environment seeder.

Inserts (or updates) a verified admin user so the operator can log in to
`/admin/` without hand-running SQL or a one-off Python snippet every time
the dev database is reset. As of PR 1 of the multi-tenant refactor the
seeder also:

- upserts the "Axiolo" organization (idempotent on slug);
- inserts/upserts an owner membership for the seeded user;
- sets ``users.last_active_org_id`` to the Axiolo org;
- promotes the user to ``is_superadmin = true`` if their email appears
  in the ``SUPERADMIN_EMAILS`` env var.

Defaults to dev@example.com / dev-admin-password. Override via env:
    DEV_ADMIN_EMAIL=...
    DEV_ADMIN_PASSWORD=...
    DEV_ADMIN_NAME=...
    SUPERADMIN_EMAILS=...   (whitespace/comma separated)

Run from the project root:
    make seed-dev

…or directly inside the backend container:
    docker compose exec backend uv run python -m scripts.dev_seed

Refuses to run if `settings.database_url` looks like a production DSN
(presence of `sslmode=require` or non-loopback host) so this can't
accidentally seed prod.
"""
from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pulse_api.auth.password import hash_password
from pulse_api.config import settings

DEFAULT_EMAIL = "dev@example.com"
DEFAULT_PASSWORD = "dev-admin-password"
DEFAULT_NAME = "Dev Admin"

AXIOLO_ORG_NAME = "Axiolo"
AXIOLO_ORG_SLUG = "axiolo"


def _looks_like_prod(database_url: str) -> bool:
    """Refuse to run against anything that isn't obviously local."""
    parsed = urlparse(database_url)
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1", "db", ""}:
        return True
    if "sslmode=require" in (parsed.query or "").lower():
        return True
    return False


def _superadmin_emails() -> set[str]:
    """Lower-cased emails from the ``SUPERADMIN_EMAILS`` env var.

    Mirrors the parsing the 0004 migration uses so dev + prod data
    arrive at the same superadmin set.
    """
    raw = os.environ.get("SUPERADMIN_EMAILS", "").strip()
    if not raw:
        return set()
    return {p.strip().lower() for p in raw.replace(",", " ").split() if p}


async def seed_admin_user(
    email: str, password: str, name: str
) -> tuple[str, bool]:
    """Insert/update an admin user and ensure the Axiolo org wiring is in place.

    Operations performed (all idempotent):

    1. Upsert the Axiolo organization by slug.
    2. Insert or update the user (password, name, verified).
    3. Insert (or no-op) the owner membership row on Axiolo.
    4. Set ``users.last_active_org_id`` to the Axiolo org.
    5. Promote the user to ``is_superadmin = true`` if their email is in
       the ``SUPERADMIN_EMAILS`` env var.

    Args:
        email: Operator email (will be lower-cased on disk).
        password: Plaintext password — hashed before insert.
        name: Display name.

    Returns:
        Tuple of (user_id, was_created) where ``was_created`` is True
        on a fresh insert and False on update.
    """
    superadmins = _superadmin_emails()
    is_super = email.lower() in superadmins

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            # 1. Axiolo org
            org_id = (
                await conn.execute(
                    text(
                        "select id::text from public.organizations "
                        "where slug = :s"
                    ),
                    {"s": AXIOLO_ORG_SLUG},
                )
            ).scalar_one_or_none()
            if org_id is None:
                org_id = (
                    await conn.execute(
                        text(
                            "insert into public.organizations (name, slug) "
                            "values (:n, :s) returning id::text"
                        ),
                        {"n": AXIOLO_ORG_NAME, "s": AXIOLO_ORG_SLUG},
                    )
                ).scalar_one()

            # 2. User
            existing = (
                await conn.execute(
                    text("select id::text from public.users where email = :e"),
                    {"e": email.lower()},
                )
            ).scalar_one_or_none()

            was_created = existing is None
            if was_created:
                user_id = (
                    await conn.execute(
                        text(
                            "insert into public.users "
                            "(email, password_hash, name, "
                            " is_superadmin, last_active_org_id, "
                            " email_verified_at) "
                            "values (:e, :h, :n, :su, cast(:org as uuid), now()) "
                            "returning id::text"
                        ),
                        {
                            "e": email.lower(),
                            "h": hash_password(password),
                            "n": name,
                            "su": is_super,
                            "org": org_id,
                        },
                    )
                ).scalar_one()
            else:
                user_id = existing
                await conn.execute(
                    text(
                        "update public.users set "
                        "  password_hash = :h, "
                        "  name = :n, "
                        "  is_superadmin = case when :su then true else is_superadmin end, "
                        "  last_active_org_id = coalesce(last_active_org_id, cast(:org as uuid)), "
                        "  email_verified_at = coalesce(email_verified_at, now()) "
                        "where id = cast(:i as uuid)"
                    ),
                    {
                        "h": hash_password(password),
                        "n": name,
                        "su": is_super,
                        "org": org_id,
                        "i": user_id,
                    },
                )

            # 3. Owner membership (idempotent via the (org_id, user_id) UNIQUE).
            await conn.execute(
                text(
                    "insert into public.organization_memberships "
                    "(org_id, user_id, role) "
                    "values (cast(:org as uuid), cast(:uid as uuid), 'owner') "
                    "on conflict (org_id, user_id) do nothing"
                ),
                {"org": org_id, "uid": user_id},
            )

            return user_id, was_created
    finally:
        await engine.dispose()


def main() -> int:
    email = os.environ.get("DEV_ADMIN_EMAIL", DEFAULT_EMAIL)
    password = os.environ.get("DEV_ADMIN_PASSWORD", DEFAULT_PASSWORD)
    name = os.environ.get("DEV_ADMIN_NAME", DEFAULT_NAME)

    if _looks_like_prod(settings.database_url):
        sys.stderr.write(
            f"refusing to seed against {settings.database_url!r}: "
            "looks like a non-local DB. Set DATABASE_URL to a local DSN.\n"
        )
        return 2

    user_id, created = asyncio.run(seed_admin_user(email, password, name))
    action = "created" if created else "updated"
    sys.stdout.write(
        f"{action} admin {email} (id={user_id}) — password: {password}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
