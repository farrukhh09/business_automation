"""Declarative base, naming convention, shared column types and mixins.

- Constraint names are deterministic (naming convention) so Alembic migrations are stable.
- ``UTCDateTime``: ``DateTime(timezone=True)`` that always returns aware UTC datetimes, also on
  SQLite (which drops tz info). Rendered as ``sa.DateTime(timezone=True)`` in migrations.
- ``enum_type``: ``sa.Enum(E, native_enum=False, length=32, validate_strings=True)`` storing
  ``.value`` with a named CHECK constraint (01-overview.md §4).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.core.time import now_utc

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MONEY_PRECISION = 12
MONEY_SCALE = 2
COORDINATE_PRECISION = 9
COORDINATE_SCALE = 6
ENUM_LENGTH = 32


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetime on every backend."""

    impl = sa.DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"UTCDateTime expects datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def enum_type(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    """VARCHAR(32) + CHECK constraint ``ck_<table>_<name>``; the DB stores ``member.value``."""
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=ENUM_LENGTH,
        validate_strings=True,
        values_callable=_enum_values,
    )


def money_type() -> sa.Numeric[Any]:
    return sa.Numeric(MONEY_PRECISION, MONEY_SCALE)


def coordinate_type() -> sa.Numeric[Any]:
    return sa.Numeric(COORDINATE_PRECISION, COORDINATE_SCALE)


def json_dict_type() -> sa.types.TypeEngine[Any]:
    """JSON object column with in-place mutation tracking (top-level keys)."""
    return MutableDict.as_mutable(sa.JSON())


def json_list_type() -> sa.types.TypeEngine[Any]:
    """JSON array column with in-place mutation tracking (append/remove)."""
    return MutableList.as_mutable(sa.JSON())


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        state = sa.inspect(self)
        identity = state.identity
        if identity is None:
            return f"<{type(self).__name__} (transient)>"
        key = identity[0] if len(identity) == 1 else identity
        return f"<{type(self).__name__} {key!r}>"


class CreatedAtMixin:
    """``created_at`` only (tables marked "без updated_at" in 02-data-model.md)."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=now_utc,
        server_default=sa.func.now(),
    )


class TimestampMixin:
    """``created_at`` + ``updated_at`` (UTC)."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=now_utc,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=now_utc,
        onupdate=now_utc,
        server_default=sa.func.now(),
    )
