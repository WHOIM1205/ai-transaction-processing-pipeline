"""Alembic migration environment.

WHY THIS FILE EXISTS
--------------------
Alembic runs this module to set up a migration run (offline or online). Its job
here is to:
  1. Pull the database URL from our central settings (not from alembic.ini), so
     there is one source of truth and no credentials committed to the repo.
  2. Expose `Base.metadata` as the target so `--autogenerate` can diff the ORM
     models against the live database.

Models are imported (via `app.models`) so their tables register on
`Base.metadata` and autogenerate can diff them against the database.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base

# Importing the models package registers every table (Job, Transaction,
# JobSummary) on `Base.metadata`. Imported for its side effect only.
import app.models  # noqa: E402,F401  (must follow Base import; side-effect import)

# Alembic Config object — provides access to values in alembic.ini.
config = context.config

# Inject the real database URL from our settings, overriding the blank value in
# alembic.ini. This is what makes migrations work both locally and in Docker.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Configure Python logging from the ini file's logging sections.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata used for autogenerate (populated by the models import above).
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits SQL to stdout).

    Useful for generating SQL scripts to apply manually.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection (the normal path,
    used by `alembic upgrade head` at container startup)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # short-lived migration connection, no pooling
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
