"""Orders aggregate: orders, order events, payments (02-data-model.md; 04-api.md §5, §7)."""

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, false, func, or_, select
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.time import business_day_bounds_utc
from app.models.customer import Customer
from app.models.enums import DeliveryType, OrderStatus, PaymentKind, PaymentStatus
from app.models.order import Order, OrderEvent, Payment
from app.repositories.base import BaseRepository, coerce_enum
from app.repositories.customers import customer_phone_condition, customer_search_condition
from app.services.constants import (
    ACTIVE_ORDER_STATUSES,
    DRAFT_ORDER_STATUSES,
    VALID_ORDER_STATUSES,
    sorted_statuses,
)

ORDER_SORTS = ("created_at", "delivery_date", "total_amount")
DEFAULT_ORDER_SORT = "-created_at"
DATE_BASES = ("delivery", "created")
MAX_ORDER_ID_DIGITS = 9
# A bare number in the order search is also a phone fragment only from this many digits on
# (shorter numbers are order ids: "3" must not match every phone containing a 3).
MIN_ORDER_PHONE_SEARCH_DIGITS = 5


def _money(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(Decimal("0.01"))


class OrderRepository(BaseRepository[Order]):
    model = Order
    not_found_detail = "Заказ не найден"

    # ------------------------------------------------------------------ single orders

    def get_detail(self, order_id: int) -> Order | None:
        """Order with items, customer, delivery, payments and events (+ event actors) loaded."""
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.items),
                selectinload(Order.customer),
                selectinload(Order.delivery),
                selectinload(Order.payments),
                selectinload(Order.events).selectinload(OrderEvent.actor_user),
            )
        )
        return self.db.scalars(stmt).first()

    def get_detail_or_raise(self, order_id: int) -> Order:
        order = self.get_detail(order_id)
        if order is None:
            raise NotFoundError(self.not_found_detail)
        return order

    # ------------------------------------------------------------------ lists

    def list_filtered(
        self,
        *,
        statuses: Sequence[OrderStatus | str] | None = None,
        payment_status: PaymentStatus | str | None = None,
        delivery_type: DeliveryType | str | None = None,
        customer_id: int | None = None,
        delivery_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        sort: str | None = DEFAULT_ORDER_SORT,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Order], int]:
        """``GET /orders`` (04 §5). ``statuses`` is the repeated ``status`` query; ``date_from``/``date_to``
        filter ``delivery_date`` inclusively; ``search`` matches the order number (``12``/``#12``) and
        customer name/username/phone. Customer and items are eager-loaded. Unknown sort or filter
        value → 400."""
        stmt: Select[Any] = select(Order).options(selectinload(Order.customer), selectinload(Order.items))
        if statuses:
            wanted = {coerce_enum(OrderStatus, status, "Недопустимый статус заказа") for status in statuses}
            stmt = stmt.where(Order.status.in_(sorted(wanted)))
        if payment_status:
            stmt = stmt.where(
                Order.payment_status == coerce_enum(PaymentStatus, payment_status, "Недопустимый статус оплаты")
            )
        if delivery_type:
            stmt = stmt.where(
                Order.delivery_type == coerce_enum(DeliveryType, delivery_type, "Недопустимый тип доставки")
            )
        if customer_id is not None:
            stmt = stmt.where(Order.customer_id == customer_id)
        if delivery_date is not None:
            stmt = stmt.where(Order.delivery_date == delivery_date)
        if date_from is not None:
            stmt = stmt.where(Order.delivery_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Order.delivery_date <= date_to)
        if search and search.strip():
            stmt = stmt.where(self._search_condition(search))
        stmt = stmt.order_by(*self._order_by(sort))
        return self.paginate(stmt, page, page_size)

    @staticmethod
    def _search_condition(search: str) -> Any:
        """``#12``/``№12`` → order id only; ``12`` → order id, or a customer phone fragment when it has
        at least ``MIN_ORDER_PHONE_SEARCH_DIGITS`` digits; any other text → customer name/username/phone."""
        text = search.strip()
        number = text.lstrip("#№").strip()
        if not (number.isascii() and number.isdigit()):
            return Order.customer_id.in_(select(Customer.id).where(customer_search_condition(text)))
        conditions: list[Any] = []
        if len(number) <= MAX_ORDER_ID_DIGITS:
            conditions.append(Order.id == int(number))
        if not text.startswith(("#", "№")) and len(number) >= MIN_ORDER_PHONE_SEARCH_DIGITS:
            conditions.append(Order.customer_id.in_(select(Customer.id).where(customer_phone_condition(number))))
        return or_(*conditions) if conditions else false()

    @staticmethod
    def _order_by(sort: str | None) -> list[Any]:
        sort_key = (sort or DEFAULT_ORDER_SORT).strip()
        descending = sort_key.startswith("-")
        field = sort_key.lstrip("-+")
        if field not in ORDER_SORTS:
            raise BadRequestError("Недопустимая сортировка заказов")

        def direction(column: Any) -> Any:
            return (column.desc() if descending else column.asc()).nulls_last()

        if field == "delivery_date":
            return [direction(Order.delivery_date), direction(Order.delivery_time), direction(Order.id)]
        if field == "total_amount":
            return [direction(Order.total_amount), direction(Order.id)]
        return [direction(Order.created_at), direction(Order.id)]

    def valid_orders_for_period(self, date_from: date, date_to: date, date_basis: str = "delivery") -> list[Order]:
        """VALID orders of the inclusive period (03 §4) with items and customer loaded.

        ``date_basis="delivery"`` — by ``delivery_date``; ``"created"`` — by ``confirmed_at`` within the
        business-timezone days ``date_from``..``date_to``.
        """
        stmt = (
            select(Order)
            .where(Order.status.in_(sorted_statuses(VALID_ORDER_STATUSES)))
            .options(selectinload(Order.items), selectinload(Order.customer))
            .order_by(Order.id)
        )
        if date_basis == "delivery":
            stmt = stmt.where(Order.delivery_date >= date_from, Order.delivery_date <= date_to)
        elif date_basis == "created":
            start, _ = business_day_bounds_utc(date_from)
            _, end = business_day_bounds_utc(date_to)
            stmt = stmt.where(Order.confirmed_at >= start, Order.confirmed_at < end)
        else:
            raise BadRequestError("Недопустимая база даты: ожидается delivery или created")
        return list(self.db.scalars(stmt).all())

    def drafts_for_conversation(self, conversation_id: int) -> list[Order]:
        """NEW / WAITING_CONFIRMATION orders created from the conversation, newest first."""
        stmt = (
            select(Order)
            .where(
                Order.conversation_id == conversation_id,
                Order.status.in_(sorted_statuses(DRAFT_ORDER_STATUSES)),
            )
            .options(selectinload(Order.items), selectinload(Order.delivery))
            .order_by(Order.id.desc())
        )
        return list(self.db.scalars(stmt).all())

    def latest_active_for_customer(
        self, customer_id: int, statuses: frozenset[OrderStatus] = ACTIVE_ORDER_STATUSES
    ) -> Order | None:
        """Newest order of the customer that is neither COMPLETED nor CANCELLED (or in ``statuses``)."""
        stmt = (
            select(Order)
            .where(Order.customer_id == customer_id, Order.status.in_(sorted_statuses(statuses)))
            .options(selectinload(Order.items), selectinload(Order.delivery))
            .order_by(Order.id.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def active_for_customer(
        self, customer_id: int, statuses: frozenset[OrderStatus] = ACTIVE_ORDER_STATUSES, limit: int = 3
    ) -> list[Order]:
        """Newest orders of the customer in ``statuses`` (the bot's ORDER_STATUS answer)."""
        stmt = (
            select(Order)
            .where(Order.customer_id == customer_id, Order.status.in_(sorted_statuses(statuses)))
            .options(selectinload(Order.items))
            .order_by(Order.id.desc())
            .limit(max(int(limit), 1))
        )
        return list(self.db.scalars(stmt).all())

    def latest_active_ids_for_customers(
        self, customer_ids: Iterable[int], statuses: frozenset[OrderStatus] = ACTIVE_ORDER_STATUSES
    ) -> dict[int, int]:
        """``{customer_id: newest active order id}`` in one query (conversation lists)."""
        ids = sorted(set(customer_ids))
        if not ids:
            return {}
        stmt = (
            select(Order.customer_id, func.max(Order.id))
            .where(Order.customer_id.in_(ids), Order.status.in_(sorted_statuses(statuses)))
            .group_by(Order.customer_id)
        )
        return {int(customer_id): int(order_id) for customer_id, order_id in self.db.execute(stmt).all()}

    def list_for_customer(self, customer_id: int, limit: int | None = None) -> list[Order]:
        """All orders of the customer, newest first, with items and customer loaded."""
        stmt = (
            select(Order)
            .where(Order.customer_id == customer_id)
            .options(selectinload(Order.items), selectinload(Order.customer))
            .order_by(Order.created_at.desc(), Order.id.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def recent(self, limit: int = 10) -> list[Order]:
        """Newest orders (any status) with customer and items loaded."""
        stmt = (
            select(Order)
            .options(selectinload(Order.customer), selectinload(Order.items))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    # ------------------------------------------------------------------ counters

    def count_by_statuses(self, statuses: Iterable[OrderStatus], delivery_date: date | None = None) -> int:
        stmt = select(func.count()).select_from(Order).where(Order.status.in_(sorted(set(statuses))))
        if delivery_date is not None:
            stmt = stmt.where(Order.delivery_date == delivery_date)
        return int(self.db.execute(stmt).scalar_one())

    def count_unpaid_valid(self, since: date) -> int:
        """VALID orders with ``payment_status != PAID`` and ``delivery_date >= since`` (04 §7 dashboard)."""
        stmt = (
            select(func.count())
            .select_from(Order)
            .where(
                Order.status.in_(sorted_statuses(VALID_ORDER_STATUSES)),
                Order.payment_status != PaymentStatus.PAID,
                Order.delivery_date >= since,
            )
        )
        return int(self.db.execute(stmt).scalar_one())

    def customer_has_completed_orders(self, customer_id: int) -> bool:
        """03 §2: a customer is REGULAR when at least one order is COMPLETED."""
        stmt = select(Order.id).where(Order.customer_id == customer_id, Order.status == OrderStatus.COMPLETED).limit(1)
        return self.db.execute(stmt).first() is not None

    def max_confirmed_at_for_customer(self, customer_id: int) -> datetime | None:
        """03 §2: ``Customer.last_order_at`` = max ``confirmed_at`` of non-cancelled orders."""
        stmt = select(func.max(Order.confirmed_at)).where(
            Order.customer_id == customer_id,
            Order.status != OrderStatus.CANCELLED,
            Order.confirmed_at.is_not(None),
        )
        return self.db.execute(stmt).scalar_one_or_none()


class OrderEventRepository(BaseRepository[OrderEvent]):
    model = OrderEvent
    not_found_detail = "Событие заказа не найдено"

    def list_for_order(self, order_id: int) -> list[OrderEvent]:
        """Chronological journal of the order with ``actor_user`` loaded."""
        stmt = (
            select(OrderEvent)
            .where(OrderEvent.order_id == order_id)
            .options(selectinload(OrderEvent.actor_user))
            .order_by(OrderEvent.created_at, OrderEvent.id)
        )
        return list(self.db.scalars(stmt).all())


class PaymentRepository(BaseRepository[Payment]):
    model = Payment
    not_found_detail = "Платёж не найден"

    def list_for_order(self, order_id: int) -> list[Payment]:
        stmt = select(Payment).where(Payment.order_id == order_id).order_by(Payment.paid_at, Payment.id)
        return list(self.db.scalars(stmt).all())

    def sums_by_kind(self, order_id: int) -> dict[PaymentKind, Decimal]:
        """``{PAYMENT: Σ, REFUND: Σ}`` (both keys always present, 0.00 when none)."""
        stmt = (
            select(Payment.kind, func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.order_id == order_id)
            .group_by(Payment.kind)
        )
        sums = {kind: Decimal("0.00") for kind in PaymentKind}
        for kind, total in self.db.execute(stmt).all():
            sums[PaymentKind(kind)] = _money(total)
        return sums

    def has_refund(self, order_id: int) -> bool:
        stmt = select(Payment.id).where(Payment.order_id == order_id, Payment.kind == PaymentKind.REFUND).limit(1)
        return self.db.execute(stmt).first() is not None
