from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from chessfen.db import Base, engine
from chessfen.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve the URL once from the project settings (env-driven). The URL is not
# written back into the ConfigParser because percent-encoded credentials would
# be mangled by interpolation.
database_url = config.get_main_option("sqlalchemy.url") or get_settings().database_url

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
