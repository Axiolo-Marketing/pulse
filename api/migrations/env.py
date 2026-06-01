import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

from pulse_api.config import settings
from pulse_api import models  # noqa: F401  (registers SQLModel tables)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Do NOT push settings.database_url through config.set_main_option — alembic
# writes that into configparser, which treats `%` as interpolation syntax and
# blows up on URL-encoded special chars in the password (e.g. `%3D`). We inject
# the URL directly into the engine config dict in run_async_migrations instead.

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = settings.database_url
    connectable = async_engine_from_config(section, prefix="sqlalchemy.")
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
