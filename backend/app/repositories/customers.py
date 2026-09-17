"""Customers (02-data-model.md: customers; 04-api.md §3).

``orders_count`` / ``total_spent`` are derived over VALID orders only (03 §4) with one aggregate
subquery joined to the page of customers — no per-customer queries.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select

from app.core.exceptions import BadRequestError
from app.core.time import business_day_bounds_utc
from app.models.customer import Customer
from app.models.order import Order
from app.repositories.base import LIKE_ESCAPE, BaseRepository, like_pattern
from app.services.constants import VALID_ORDER_STATUSES, sorted_statuses
from app.services.phone import phone_digits

CUSTOMER_TYPES = ("new", "regular")
CUSTOMER_SORTS = ("last_order_at", "created_at", "total_spent")
DEFAULT_CUSTOMER_SORT = "-last_order_at"
MIN_PHONE_SEARCH_DIGITS = 3


@dataclass(frozen=True, slots=True)
class CustomerStats:
    """One row of ``CustomerRepository.search_with_stats``."""

    customer: Customer
    orders_count: int
    total_spent: Decimal


def valid_order_stats_subquery() -> Any:
    """``(customer_id, orders_count, total_spent)`` over VALID orders, grouped by customer."""
    return (
        select(
            Order.customer_id.label("customer_id"),
            func.count(Order.id).label("orders_count"),
            func.coalesce(func.sum(Order.total_amount), 0).label("total_spent"),
        )
        .where(Order.status.in_(sorted_statuses(VALID_ORDER_STATUSES)))
        .group_by(Order.customer_id)
        .subquery("customer_order_stats")
    )


def customer_phone_condition(digits: str) -> Any:
    """Normalized phone contains the digit fragment."""
    return Customer.phone.like(like_pattern(digits), escape=LIKE_ESCAPE)


def search_patterns(text: str) -> list[str]:
    """LIKE patterns covering the case variants of ``text``.

    ``ILIKE`` folds case in the database, but only for ASCII when the collation is ``C`` (always on
    SQLite, and on PostgreSQL initialized with ``--no-locale``), so a Cyrillic query would otherwise
    be case-sensitive: ``"алия"`` would not find ``"Алия"``. Non-ASCII text is therefore also matched
    against its lower/upper/capitalized/title forms (``ProductRepository`` folds in Python instead).
    """
    variants = {text}
    if not text.isascii():
        variants |= {text.lower(), text.upper(), text.capitalize(), text.title()}
    return [like_pattern(variant) for variant in sorted(variants)]


def customer_search_condition(search: str) -> Any:
    """Name / username (``@`` optional) / phone by digits — only with at least
    ``MIN_PHONE_SEARCH_DIGITS`` digits, so ``"3"`` does not match every phone containing a 3
    (``"90 123"`` matches ``+99290123...``). Case-insensitive, Cyrillic included."""
    text = search.strip()
    conditions = [Customer.name.ilike(pattern, escape=LIKE_ESCAPE) for pattern in search_patterns(text)]
    handle = text.lstrip("@")
    if handle:
        conditions += [Customer.username.ilike(pattern, escape=LIKE_ESCAPE) for pattern in search_patterns(handle)]
    digits = phone_digits(text)
    if len(digits) >= MIN_PHONE_SEARCH_DIGITS:
        conditions.append(customer_phone_condition(digits))
    return or_(*conditions)


def _to_decimal(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(Decimal("0.01"))


class CustomerRepository(BaseRepository[Customer]):
    model = Customer
    not_found_detail = "Клиент не найден"

    def get_by_instagram_id(self, instagram_user_id: str) -> Customer | None:
        return self.db.scalars(select(Customer).where(Customer.instagram_user_id == instagram_user_id)).first()

    def get_by_phone(self, phone: str) -> Customer | None:
        """Oldest customer with this (normalized) phone; phones are not unique."""
        return self.db.scalars(select(Customer).where(Customer.phone == phone).order_by(Customer.id)).first()

    def search_with_stats(
        self,
        search: str | None = None,
        customer_type: str | None = None,
        sort: str | None = DEFAULT_CUSTOMER_SORT,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[CustomerStats], int]:
        """``GET /customers`` (04 §3): filters, sort (``last_order_at``/``created_at``/``total_spent``,
        ``-`` prefix = desc; NULLs last), pagination. Unknown sort/type → ``BadRequestError``."""
        stats = valid_order_stats_subquery()
        orders_count = func.coalesce(stats.c.orders_count, 0).label("orders_count")
        total_spent = func.coalesce(stats.c.total_spent, 0).label("total_spent")
        stmt: Select[Any] = select(Customer, orders_count, total_spent).outerjoin(
            stats, stats.c.customer_id == Customer.id
        )

        if search and search.strip():
            stmt = stmt.where(customer_search_condition(search))
        if customer_type:
            kind = customer_type.strip().lower()
            if kind not in CUSTOMER_TYPES:
                raise BadRequestError("Недопустимый тип клиента: ожидается new или regular")
            stmt = stmt.where(Customer.is_new.is_(kind == "new"))

        sort_key = (sort or DEFAULT_CUSTOMER_SORT).strip()
        descending = sort_key.startswith("-")
        field = sort_key.lstrip("-+")
        if field not in CUSTOMER_SORTS:
            raise BadRequestError("Недопустимая сортировка клиентов")
        column: Any = {
            "last_order_at": Customer.last_order_at,
            "created_at": Customer.created_at,
            "total_spent": total_spent,
        }[field]
        ordered = column.desc() if descending else column.asc()
        tie_breaker = Customer.id.desc() if descending else Customer.id.asc()
        stmt = stmt.order_by(ordered.nulls_last(), tie_breaker)

        rows, total = self.paginate(stmt, page, page_size)
        return [
            CustomerStats(customer=row[0], orders_count=int(row[1] or 0), total_spent=_to_decimal(row[2]))
            for row in rows
        ], total

    def get_stats(self, customer_id: int) -> tuple[int, Decimal]:
        """``(orders_count, total_spent)`` over VALID orders of one customer."""
        stmt = select(func.count(Order.id), func.coalesce(func.sum(Order.total_amount), 0)).where(
            Order.customer_id == customer_id,
            Order.status.in_(sorted_statuses(VALID_ORDER_STATUSES)),
        )
        count, total = self.db.execute(stmt).one()
        return int(count or 0), _to_decimal(total)

    def count_all(self) -> int:
        """03 §4 ``total_customers``: every customer in the database."""
        return int(self.db.execute(select(func.count()).select_from(Customer)).scalar_one())

    def count_registered(self, date_from: date, date_to: date) -> int:
        """03 §4 ``customers_registered``: customers created within the inclusive period.

        The period is the business-timezone days ``date_from``..``date_to`` (``created_at`` is UTC).
        """
        start, _ = business_day_bounds_utc(date_from)
        _, end = business_day_bounds_utc(date_to)
        stmt = (
            select(func.count())
            .select_from(Customer)
            .where(Customer.created_at >= start, Customer.created_at < end)
        )
        return int(self.db.execute(stmt).scalar_one())
