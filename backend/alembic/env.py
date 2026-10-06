import os
from logging.config import fileConfig

from sqlalchemy import Column, MetaData, String, Table, engine_from_config, inspect, text
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from the environment if DATABASE_URL is set.
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# add your model's MetaData object here
# for 'autogenerate' support
# Import Base and all models so that autogenerate can detect them.
from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402, F401
    user,
    ontology,
    file,
    prompt,
    model_config,
    entity,
    logic,
    action,
    relation,
    extraction_task,
    rules_config,
    ontology_revision,
)
from app.models.v2 import connection, dataset, pipeline, curated, mapping, temporal_profile, construction, multimodal, multimodal_install, construction_draft, temporal_replay  # noqa: E402, F401

target_metadata = Base.metadata
from app.models.v2 import reasoning  # noqa: E402, F401
from app.models.v2 import decision  # noqa: E402, F401
from app.models.v2 import agent  # noqa: E402, F401
from app.models.v2 import logic_asset  # noqa: E402, F401
from app.models.v2 import object_set  # noqa: E402, F401
from app.models.v2 import query_view  # noqa: E402, F401
from app.models.v2 import query_job  # noqa: E402, F401
from app.models.v2 import scenario  # noqa: E402, F401
from app.models.v2 import time_series  # noqa: E402, F401
from app.models.v2 import object_view  # noqa: E402, F401


def _ensure_version_column_capacity(connection) -> bool:
    """Bootstrap Alembic's own table before revisions exceed its 32-char default."""
    inspector = inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        Table(
            "alembic_version",
            MetaData(),
            Column("version_num", String(255), primary_key=True, nullable=False),
        ).create(connection)
        return True
    version_column = next(
        (column for column in inspector.get_columns("alembic_version") if column["name"] == "version_num"),
        None,
    )
    length = getattr(version_column.get("type"), "length", None) if version_column else None
    if connection.dialect.name == "postgresql" and length is not None and length < 255:
        connection.execute(text(
            "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)"
        ))
        return True
    return False

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    supplied_connection = config.attributes.get('connection')
    if supplied_connection is not None:
        _ensure_version_column_capacity(supplied_connection)
        context.configure(connection=supplied_connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _ensure_version_column_capacity(connection)
        # SQLAlchemy 2 inspection opens an implicit transaction.  Alembic
        # must start with a clean connection or its version-row updates can
        # be rolled back when this connection closes (SQLite DDL may remain).
        connection.commit()
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
