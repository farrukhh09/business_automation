"""Statistics, dashboard and time series (03-business-rules.md §4, 04-api.md §7, SPEC §20–21, §32).

Only VALID orders (``CONFIRMED…COMPLETED``) are counted anywhere. The period is measured on
``delivery_date`` by default; ``date_basis="created"`` measures it on ``confirmed_at`` converted to
the business timezone.

Read-only service: it never writes, so it never commits.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ValidationError
from app.core.time import business_today, to_business
from app.models.enums import DeliveryType, OrderStatus, PaymentStatus
from app.models.order import Order
from app.repositories.conversations import ConversationRepository
from app.repositories.customers import CustomerRepository
from app.repositories.orders import DATE_BASES, OrderRepository
from app.schemas.order import build_order_list_item
from app.schemas.statistics import (
    DEFAULT_DATE_BASIS,
    DEFAULT_PERIOD,
    CustomerStats,
    DashboardOut,
    DeliveryStats,
    FinanceStats,
    StatisticsOut,
    StatisticsPeriodInfo,
    TimeseriesPoint,
)

PERIOD_NAMES: tuple[str, ...] = ("today", "yesterday", "week", "month", "custom")
WEEK_DAYS = 7
#: A custom period (and a time series range) may not be longer than this.
MAX_PERIOD_DAYS = 366
#: ``GET /statistics/timeseries`` without dates: the last 30 days including today.
DEFAULT_TIMESERIES_DAYS = 30
#: 04-api.md §7 ``DashboardOut.unpaid_orders_count``: valid orders with ``delivery_date >= today-30``.
DASHBOARD_UNPAID_DAYS = 30
RECENT_ORDERS_LIMIT = 10

MONEY_QUANT = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value: Decimal | int | None) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Period:
    """A resolved, inclusive date range (``name`` is echoed back in ``StatisticsOut.period``)."""

    name: str
    date_from: date
    date_to: date

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    def dates(self) -> list[date]:
        """Every calendar day of the period, ascending."""
        return [self.date_from + timedelta(days=offset) for offset in range(self.days)]


def validate_range(date_from: date, date_to: date) -> tuple[date, date]:
    """``date_from ≤ date_to`` and at most ``MAX_PERIOD_DAYS`` days, otherwise 422."""
    if date_from > date_to:
        raise ValidationError("Начало периода не может быть позже его конца")
    if (date_to - date_from).days + 1 > MAX_PERIOD_DAYS:
        raise ValidationError(f"Период не может быть длиннее {MAX_PERIOD_DAYS} дней")
    return date_from, date_to


def resolve_period(
    name: str | None = DEFAULT_PERIOD,
    date_from: date | None = None,
    date_to: date | None = None,
    today: date | None = None,
) -> Period:
    """03 §4 "Периоды": ``today``, ``yesterday``, ``week`` (last 7 days including today),
    ``month`` (from the 1st of the current month), ``custom`` (``date_from``..``date_to``).

    ``today`` defaults to the current business date. Invalid input → 422 ``validation_error``.
    """
    current = today or business_today()
    key = (name or DEFAULT_PERIOD).strip().lower()
    if key == "today":
        return Period("today", current, current)
    if key == "yesterday":
        yesterday = current - timedelta(days=1)
        return Period("yesterday", yesterday, yesterday)
    if key == "week":
        return Period("week", current - timedelta(days=WEEK_DAYS - 1), current)
    if key == "month":
        return Period("month", current.replace(day=1), current)
    if key == "custom":
        if date_from is None or date_to is None:
            raise ValidationError("Для произвольного периода укажите date_from и date_to")
        start, end = validate_range(date_from, date_to)
        return Period("custom", start, end)
    raise ValidationError(f"Недопустимый период: ожидается один из {', '.join(PERIOD_NAMES)}")


def resolve_date_basis(date_basis: str | None) -> str:
    """``delivery`` (default) or ``created`` (03 §4 "База даты"); anything else → 400."""
    basis = (date_basis or DEFAULT_DATE_BASIS).strip().lower()
    if basis not in DATE_BASES:
        raise BadRequestError("Недопустимая база даты: ожидается delivery или created")
    return basis


def order_basis_date(order: Order, date_basis: str) -> date | None:
    """The business date the order is counted on for the given basis."""
    if date_basis == "created":
        return to_business(order.confirmed_at).date() if order.confirmed_at is not None else None
    return order.delivery_date


def _first_order_key(order: Order, date_basis: str) -> tuple[date, time, int]:
    """Sort key deciding which order of a customer is "the first one of the period" (03 §4)."""
    basis_date = order_basis_date(order, date_basis) or date.max
    if date_basis == "created":
        moment = to_business(order.confirmed_at).time() if order.confirmed_at is not None else time.min
    else:
        moment = order.delivery_time or time.min
    return basis_date, moment, order.id or 0


def customer_groups(orders: Iterable[Order], date_basis: str = DEFAULT_DATE_BASIS) -> dict[int, bool]:
    """``{customer_id: is_repeat_customer of their FIRST valid order of the period}`` (03 §4).

    A customer who ordered twice in the period is counted once, in the group of the earlier order.
    """
    groups: dict[int, bool] = {}
    for order in sorted(orders, key=lambda item: _first_order_key(item, date_basis)):
        groups.setdefault(order.customer_id, bool(order.is_repeat_customer))
    return groups


def finance_stats(orders: Sequence[Order]) -> FinanceStats:
    """03 §4 "Финансы". ``paid_amount`` never exceeds the order total (overpayments are not revenue)."""
    revenue = ZERO
    paid_amount = ZERO
    counts = {status: 0 for status in PaymentStatus}
    for order in orders:
        total = _money(order.total_amount)
        revenue += total
        paid = _money(order.paid_amount)
        paid_amount += min(paid, total) if paid > ZERO else ZERO
        counts[order.payment_status] = counts.get(order.payment_status, 0) + 1
    orders_count = len(orders)
    average_check = (revenue / orders_count).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP) if orders_count else ZERO
    return FinanceStats(
        revenue=revenue,
        orders_count=orders_count,
        paid_orders_count=counts[PaymentStatus.PAID],
        partially_paid_orders_count=counts[PaymentStatus.PARTIALLY_PAID],
        unpaid_orders_count=counts[PaymentStatus.UNPAID],
        paid_amount=paid_amount,
        unpaid_amount=revenue - paid_amount,
        average_check=average_check,
    )


def delivery_stats(orders: Iterable[Order]) -> DeliveryStats:
    """03 §4 "Доставка"."""
    delivery_orders = 0
    pickup_orders = 0
    for order in orders:
        if order.delivery_type == DeliveryType.DELIVERY:
            delivery_orders += 1
        elif order.delivery_type == DeliveryType.PICKUP:
            pickup_orders += 1
    return DeliveryStats(delivery_orders=delivery_orders, pickup_orders=pickup_orders)


def customer_stats(
    orders: Sequence[Order],
    date_basis: str = DEFAULT_DATE_BASIS,
    *,
    total_customers: int = 0,
    customers_registered: int = 0,
) -> CustomerStats:
    """03 §4 "Клиенты" (SPEC §21).

    A customer belongs to the group of their first valid order of the period; ``new_customer_orders``
    / ``regular_customer_orders`` count **all** orders of the customers in each group, so the two
    always add up to ``orders_count``.
    """
    groups = customer_groups(orders, date_basis)
    new_customers = sum(1 for is_repeat in groups.values() if not is_repeat)
    new_customer_orders = sum(1 for order in orders if not groups.get(order.customer_id, bool(order.is_repeat_customer)))
    return CustomerStats(
        new_customers=new_customers,
        regular_customers=len(groups) - new_customers,
        total_customers=total_customers,
        new_customer_orders=new_customer_orders,
        regular_customer_orders=len(orders) - new_customer_orders,
        customers_registered=customers_registered,
    )


class StatisticsService:
    """``GET /statistics``, ``/statistics/dashboard``, ``/statistics/timeseries`` (04 §7)."""

    #: Exposed on the class so routes and other services use one implementation.
    resolve_period = staticmethod(resolve_period)
    resolve_date_basis = staticmethod(resolve_date_basis)

    def __init__(self, db: Session) -> None:
        self.db = db
        self.orders = OrderRepository(db)
        self.customers = CustomerRepository(db)
        self.conversations = ConversationRepository(db)

    # ------------------------------------------------------------------ period statistics

    def statistics(
        self,
        period: Period | str | None = DEFAULT_PERIOD,
        date_basis: str | None = DEFAULT_DATE_BASIS,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        today: date | None = None,
    ) -> StatisticsOut:
        """Finance / customers / delivery blocks for the period (04 §7 ``StatisticsOut``).

        ``period`` accepts an already resolved :class:`Period` or a period name from the query string.
        """
        resolved = period if isinstance(period, Period) else resolve_period(period, date_from, date_to, today)
        basis = resolve_date_basis(date_basis)
        orders = self.orders.valid_orders_for_period(resolved.date_from, resolved.date_to, basis)
        return StatisticsOut(
            period=StatisticsPeriodInfo(
                name=resolved.name,
                date_from=resolved.date_from,
                date_to=resolved.date_to,
                date_basis=basis,
            ),
            finance=finance_stats(orders),
            customers=customer_stats(
                orders,
                basis,
                total_customers=self.customers.count_all(),
                customers_registered=self.customers.count_registered(resolved.date_from, resolved.date_to),
            ),
            delivery=delivery_stats(orders),
        )

    # ------------------------------------------------------------------ dashboard

    def dashboard(self, today: date | None = None) -> DashboardOut:
        """04 §7 ``DashboardOut`` (SPEC §32). "Today" is measured on ``delivery_date``."""
        day = today or business_today()
        orders = self.orders.valid_orders_for_period(day, day, DEFAULT_DATE_BASIS)
        finance = finance_stats(orders)
        customers = customer_stats(orders, DEFAULT_DATE_BASIS)
        delivery = delivery_stats(orders)
        return DashboardOut(
            date=day,
            orders_today=finance.orders_count,
            revenue_today=finance.revenue,
            unpaid_orders_count=self.orders.count_unpaid_valid(day - timedelta(days=DASHBOARD_UNPAID_DAYS)),
            new_customers_today=customers.new_customers,
            regular_customers_today=customers.regular_customers,
            delivery_orders_today=delivery.delivery_orders,
            pickup_orders_today=delivery.pickup_orders,
            waiting_confirmation_count=self.orders.count_by_statuses([OrderStatus.WAITING_CONFIRMATION]),
            conversations_needing_attention=self.conversations.count_needing_attention(),
            recent_orders=[build_order_list_item(order) for order in self.orders.recent(RECENT_ORDERS_LIMIT)],
        )

    # ------------------------------------------------------------------ time series

    def timeseries(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        date_basis: str | None = DEFAULT_DATE_BASIS,
        today: date | None = None,
    ) -> list[TimeseriesPoint]:
        """One point per calendar day of the range (days without orders are returned with zeros).

        Without dates: the last ``DEFAULT_TIMESERIES_DAYS`` days including today.
        """
        basis = resolve_date_basis(date_basis)
        end = date_to or (today or business_today())
        start = date_from or end - timedelta(days=DEFAULT_TIMESERIES_DAYS - 1)
        start, end = validate_range(start, end)
        period = Period("custom", start, end)

        revenue = {day: ZERO for day in period.dates()}
        paid = dict.fromkeys(revenue, ZERO)
        counts = dict.fromkeys(revenue, 0)
        for order in self.orders.valid_orders_for_period(start, end, basis):
            day = order_basis_date(order, basis)
            if day is None or day not in revenue:  # pragma: no cover - the query already filters
                continue
            total = _money(order.total_amount)
            order_paid = _money(order.paid_amount)
            revenue[day] += total
            paid[day] += min(order_paid, total) if order_paid > ZERO else ZERO
            counts[day] += 1
        return [
            TimeseriesPoint(date=day, revenue=revenue[day], orders_count=counts[day], paid_amount=paid[day])
            for day in period.dates()
        ]
