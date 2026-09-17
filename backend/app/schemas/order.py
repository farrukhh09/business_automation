"""Order schemas (04-api.md §5).

Shared pieces other domains need (customers, statistics, deliveries, dashboard): ``CustomerBrief``,
``OrderListItem`` and the helpers that build them. The rest is the Orders API itself:
``OrderCreate``/``OrderUpdate``/``OrderDetail``, payments and the change journal.

Request schemas use ``extra="forbid"``: amounts are never accepted from the client (03 §1.4), so
``unit_price``/``total_price``/``total_amount`` in a body are a 422, not a silently ignored field.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActorType,
    DeliveryType,
    OrderSource,
    OrderStatus,
    PaymentKind,
    PaymentMethod,
    PaymentStatus,
)
from app.models.order import Order, OrderEvent, OrderItem, Payment
from app.schemas.common import HHMM, Money, NonNegativeMoney, ORMModel, PositiveMoney
from app.schemas.delivery import DeliveryIn, DeliveryOut

ITEMS_SUMMARY_SEPARATOR = ", "
COMMENT_MAX = 1000
CANCEL_REASON_MAX = 1000
MAX_ITEMS = 100


class CustomerBrief(ORMModel):
    """``{id, name, username, phone}`` — customer reference inside orders and conversations."""

    id: int
    name: str | None = None
    username: str | None = None
    phone: str | None = None


class OrderListItem(ORMModel):
    id: int
    customer: CustomerBrief
    status: OrderStatus
    payment_status: PaymentStatus
    delivery_type: DeliveryType | None = None
    delivery_date: date | None = None
    delivery_time: HHMM | None = None
    delivery_address: str | None = None
    total_amount: Money
    paid_amount: Money
    items_summary: str
    items_count: int
    comment: str | None = None
    created_at: datetime


def items_summary(order: Order) -> str:
    """``"Медовик ×2, Чизкейк ×1"`` — items in order of addition."""
    return ITEMS_SUMMARY_SEPARATOR.join(f"{item.product_name} ×{item.quantity}" for item in order.items)


def items_count(order: Order) -> int:
    """Σ quantity."""
    return sum(item.quantity for item in order.items)


def build_order_list_item(order: Order) -> OrderListItem:
    """Needs ``order.customer`` and ``order.items`` (eager-load them to avoid N+1)."""
    return OrderListItem(
        id=order.id,
        customer=CustomerBrief.model_validate(order.customer),
        status=order.status,
        payment_status=order.payment_status,
        delivery_type=order.delivery_type,
        delivery_date=order.delivery_date,
        delivery_time=order.delivery_time,
        delivery_address=order.delivery_address,
        total_amount=order.total_amount,
        paid_amount=order.paid_amount,
        items_summary=items_summary(order),
        items_count=items_count(order),
        comment=order.comment,
        created_at=order.created_at,
    )


# --------------------------------------------------------------------------- requests


class OrderItemIn(BaseModel):
    """``{product_id, quantity ≥ 1, comment?}`` — prices are never accepted (03 §1.4)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=10_000)
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)


class OrderCreate(BaseModel):
    """``POST /orders`` (ADMIN). ``confirm=true`` → CONFIRMED right away when the order is complete."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer_id: int = Field(ge=1)
    items: list[OrderItemIn] = Field(min_length=1, max_length=MAX_ITEMS)
    delivery_type: DeliveryType
    delivery_date: date
    delivery_time: HHMM
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)
    payment_method: PaymentMethod | None = None
    delivery: DeliveryIn | None = None
    confirm: bool = True


class OrderUpdate(BaseModel):
    """``PATCH /orders/{id}``. ``items`` replaces the whole set; OPERATOR may only send the fields
    listed in 04 §5 (others → 403 ``forbidden``)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    items: list[OrderItemIn] | None = Field(default=None, min_length=1, max_length=MAX_ITEMS)
    delivery_type: DeliveryType | None = None
    delivery_date: date | None = None
    delivery_time: HHMM | None = None
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)
    status: OrderStatus | None = None
    payment_status: PaymentStatus | None = None
    paid_amount: NonNegativeMoney | None = None
    payment_method: PaymentMethod | None = None
    delivery: DeliveryIn | None = None


class OrderStatusChangeRequest(BaseModel):
    """``POST /orders/{id}/status``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: OrderStatus
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)


class PaymentCreate(BaseModel):
    """``POST /orders/{id}/payments`` — one ledger row (03 §3)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: PaymentKind
    amount: PositiveMoney
    method: PaymentMethod | None = None
    note: str | None = Field(default=None, max_length=COMMENT_MAX)


class OrderCancelRequest(BaseModel):
    """``POST /orders/{id}/cancel``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=CANCEL_REASON_MAX)


# --------------------------------------------------------------------------- responses


class OrderItemOut(ORMModel):
    id: int
    product_id: int | None = None
    product_name: str
    quantity: int
    unit_price: Money
    total_price: Money
    comment: str | None = None


class PaymentOut(ORMModel):
    id: int
    kind: PaymentKind
    amount: Money
    method: PaymentMethod | None = None
    note: str | None = None
    paid_at: datetime
    created_by_user_id: int | None = None


class OrderEventActor(ORMModel):
    """``{id, username}`` of the staff member who caused the event."""

    id: int
    username: str


class OrderEventOut(ORMModel):
    id: int
    actor_type: ActorType
    actor_user: OrderEventActor | None = None
    event_type: str
    changes: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = None
    created_at: datetime


class OrderDetail(OrderListItem):
    items: list[OrderItemOut] = Field(default_factory=list)
    payment_method: PaymentMethod | None = None
    delivery_latitude: float | None = None
    delivery_longitude: float | None = None
    source: OrderSource
    conversation_id: int | None = None
    is_repeat_customer: bool = False
    confirmed_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    updated_at: datetime
    delivery: DeliveryOut | None = None
    payments: list[PaymentOut] = Field(default_factory=list)
    allowed_transitions: list[OrderStatus] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


ItemsMode = Literal["add", "replace", "remove"]


def build_order_item_out(item: OrderItem) -> OrderItemOut:
    return OrderItemOut.model_validate(item)


def build_payment_out(payment: Payment) -> PaymentOut:
    return PaymentOut.model_validate(payment)


def build_order_event_out(event: OrderEvent) -> OrderEventOut:
    return OrderEventOut.model_validate(event)


def build_order_detail(
    order: Order,
    allowed_transitions: list[OrderStatus] | None = None,
    missing_fields: list[str] | None = None,
) -> OrderDetail:
    """``OrderDetail`` (04 §5). Needs customer, items, payments and delivery loaded.

    ``allowed_transitions`` is filtered by the caller (role + delivery type, ``order_rules``) and
    ``missing_fields`` comes from ``OrderValidator``; both default to empty lists.
    """
    return OrderDetail(
        id=order.id,
        customer=CustomerBrief.model_validate(order.customer),
        status=order.status,
        payment_status=order.payment_status,
        delivery_type=order.delivery_type,
        delivery_date=order.delivery_date,
        delivery_time=order.delivery_time,
        delivery_address=order.delivery_address,
        total_amount=order.total_amount,
        paid_amount=order.paid_amount,
        items_summary=items_summary(order),
        items_count=items_count(order),
        comment=order.comment,
        created_at=order.created_at,
        items=[build_order_item_out(item) for item in order.items],
        payment_method=order.payment_method,
        delivery_latitude=float(order.delivery_latitude) if order.delivery_latitude is not None else None,
        delivery_longitude=float(order.delivery_longitude) if order.delivery_longitude is not None else None,
        source=order.source,
        conversation_id=order.conversation_id,
        is_repeat_customer=order.is_repeat_customer,
        confirmed_at=order.confirmed_at,
        completed_at=order.completed_at,
        cancelled_at=order.cancelled_at,
        cancel_reason=order.cancel_reason,
        updated_at=order.updated_at,
        delivery=DeliveryOut.model_validate(order.delivery) if order.delivery is not None else None,
        payments=[build_payment_out(payment) for payment in order.payments],
        allowed_transitions=list(allowed_transitions or []),
        missing_fields=list(missing_fields or []),
    )
