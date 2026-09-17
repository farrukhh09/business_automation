"""Database engine and sessions (synchronous SQLAlchemy 2.x, docs/architecture/01-overview.md §1).

- PostgreSQL (``postgresql+psycopg://``) in all real environments.
- SQLite for tests: ``check_same_thread=False``; ``StaticPool`` for ``:memory:`` so every session
  shares the one in-memory database; ``PRAGMA foreign_keys=ON`` so ON DELETE rules work.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

POSTGRES_CONNECT_TIMEOUT_SECONDS = 5


def _is_sqlite_memory(url: URL) -> bool:
    database = url.database or ""
    return database in ("", ":memory:") or url.query.get("mode") == "memory"


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend == "sqlite":
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if _is_sqlite_memory(url):
            kwargs["poolclass"] = StaticPool
        sqlite_engine = create_engine(url, echo=echo, **kwargs)
        event.listen(sqlite_engine, "connect", _enable_sqlite_foreign_keys)
        return sqlite_engine

    connect_args: dict[str, Any] = {}
    if backend == "postgresql":
        connect_args["connect_timeout"] = POSTGRES_CONNECT_TIMEOUT_SECONDS
    return create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        connect_args=connect_args,
    )


engine: Engine = create_db_engine(get_settings().DATABASE_URL)

SessionLocal: sessionmaker[Session] = sessionmaker(bind=engine, class_=Session, expire_on_commit=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and Celery tasks: commit on success, rollback on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
