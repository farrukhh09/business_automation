"""Generic repository (01-overview.md §3: queries only — select/add/flush, never commit)."""

from collections.abc import Iterable
from enum import Enum
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.base import Base

M = TypeVar("M", bound=Base)
E = TypeVar("E", bound=Enum)


def like_pattern(text: str) -> str:
    """``%text%`` with LIKE wildcards escaped (use with ``escape="\\\\"``)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


LIKE_ESCAPE = "\\"


def coerce_enum(enum_cls: type[E], value: E | str, detail: str) -> E:
    """Filter value (query string or enum) → enum member; unknown value → 400 ``bad_request``.

    Repositories accept ``Enum | str`` so that callers may pass raw query values; an unknown one is
    a bad request, never an unhandled ``ValueError`` (500).
    """
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise BadRequestError(detail) from exc


class BaseRepository(Generic[M]):
    """CRUD helpers shared by all repositories. Subclasses set ``model`` (and ``not_found_detail``)."""

    model: type[M]
    not_found_detail: str = "Объект не найден"

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ reads

    def get(self, entity_id: Any) -> M | None:
        if entity_id is None:
            return None
        return self.db.get(self.model, entity_id)

    def get_or_raise(self, entity_id: Any, detail: str | None = None) -> M:
        """``NotFoundError`` (404 ``not_found``) with a Russian ``detail`` when missing."""
        entity = self.get(entity_id)
        if entity is None:
            raise NotFoundError(detail or self.not_found_detail)
        return entity

    # ------------------------------------------------------------------ writes (flush only)

    def add(self, entity: M) -> M:
        self.db.add(entity)
        self.db.flush()
        return entity

    def add_all(self, entities: Iterable[M]) -> list[M]:
        items = list(entities)
        self.db.add_all(items)
        self.db.flush()
        return items

    def delete(self, entity: M) -> None:
        self.db.delete(entity)
        self.db.flush()

    def flush(self) -> None:
        self.db.flush()

    # ------------------------------------------------------------------ pagination

    def paginate(self, stmt: Select[Any], page: int, page_size: int) -> tuple[list[Any], int]:
        """Run ``stmt`` for one page and count all matching rows.

        - Returns ORM objects when ``stmt`` selects a single entity/column, otherwise ``Row`` tuples.
        - The total is ``SELECT count(*) FROM (stmt without ORDER BY/LIMIT/OFFSET)``; loader options
          (``selectinload``) do not affect it. Statements must not multiply rows with collection
          joins — filter through ``EXISTS``/``IN`` subqueries instead.
        """
        page = max(int(page), 1)
        page_size = max(int(page_size), 1)
        count_stmt = select(func.count()).select_from(stmt.order_by(None).limit(None).offset(None).subquery())
        total = int(self.db.execute(count_stmt).scalar_one())
        if total == 0:
            return [], 0
        page_stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        result = self.db.execute(page_stmt)
        if len(stmt.column_descriptions) == 1:
            return list(result.scalars().unique().all()), total
        return list(result.all()), total
