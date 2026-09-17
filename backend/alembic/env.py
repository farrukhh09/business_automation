"""Alembic environment.

URL precedence: ``-x db_url=...`` > ``sqlalchemy.url`` in the config (empty by default) >
``Settings.DATABASE_URL``. A ready connection can be passed programmatically via
``config.attributes["connection"]`` (used by tests).
"""

import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from alembic import context

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.base import UTCDateTime  # noqa: E402

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("db_url") or config.get_main_option("sqlalchemy.url") or get_settings().DATABASE_URL


def render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render app-specific column types as generic SQLAlchemy types in migrations."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def _configure_options(is_sqlite: bool) -> dict[str, Any]:
    return {
        "target_metadata": target_metadata,
        "compare_type": True,
        "render_as_batch": is_sqlite,
        "render_item": render_item,
    }


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_configure_options(url.startswith("sqlite")),
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, **_configure_options(connection.dialect.name == "sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_with_connection(connection)
        return
    connectable = create_engine(get_url(), poolclass=pool.NullPool)
    with connectable.connect() as new_connection:
        _run_with_connection(new_connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
