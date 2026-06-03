"""Idempotent dev-environment seeder.

Inserts (or updates) a verified admin user so the operator can log in to
`/admin/` without hand-running SQL or a one-off Python snippet every time
the dev database is reset.

Defaults to dev@example.com / dev-admin-password. Override via env:
    DEV_ADMIN_EMAIL=...
    DEV_ADMIN_PASSWORD=...
    DEV_ADMIN_NAME=...

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


def _looks_like_prod(database_url: str) -> bool:
    """Refuse to run against anything that isn't obviously local."""
    parsed = urlparse(database_url)
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1", "db", ""}:
        return True
    if "sslmode=require" in (parsed.query or "").lower():
        return True
    return False


async def seed_admin_user(
    email: str, password: str, name: str
) -> tuple[str, bool]:
    """Insert the user if missing, or update password/name + ensure admin
    and verified flags otherwise. Returns (user_id, was_created)."""
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            existing = (
                await conn.execute(
                    text("select id::text from public.users where email = :e"),
                    {"e": email.lower()},
                )
            ).scalar_one_or_none()

            if existing is None:
                row = (
                    await conn.execute(
                        text(
                            "insert into public.users "
                            "(email, password_hash, name, is_admin, email_verified_at) "
                            "values (:e, :h, :n, true, now()) "
                            "returning id::text"
                        ),
                        {"e": email.lower(), "h": hash_password(password), "n": name},
                    )
                ).scalar_one()
                return row, True

            await conn.execute(
                text(
                    "update public.users set "
                    "  password_hash = :h, "
                    "  name = :n, "
                    "  is_admin = true, "
                    "  email_verified_at = coalesce(email_verified_at, now()) "
                    "where id = cast(:i as uuid)"
                ),
                {"h": hash_password(password), "n": name, "i": existing},
            )
            return existing, False
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
