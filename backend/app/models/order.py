"""Orders aggregate (02-data-model.md: orders, order_items, order_events, payments)."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import now_utc
from app.models.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UTCDateTime,
    coordinate_type,
    enum_type,
    json_dict_type,
    money_type,
)
from app.models.enums import (
    ActorType,
    DeliveryType,
    OrderSource,
    OrderStatus,
    PaymentKind,
    PaymentMethod,
    PaymentStatus,
)

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.customer import Customer
    from app.models.delivery import Delivery
    from app.models.product import Product
    from app.models.user import User


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        sa.Index("ix_orders_delivery_date_status", "delivery_date", "status"),
        sa.Index("ix_orders_customer_id_status", "customer_id", "status"),
    )

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(sa.ForeignKey("customers.id"), nullable=False, index=True)
    conversation_id: Mapped[int | None] = mapped_column(sa.ForeignKey("conversations.id", ondelete="SET NULL"))
    source: Mapped[OrderSource] = mapped_column(enum_type(OrderSource, "source"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        enum_type(OrderStatus, "status"),
        nullable=False,
        default=OrderStatus.NEW,
        server_default=OrderStatus.NEW.value,
        index=True,
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        enum_type(PaymentStatus, "payment_status"),
        nullable=False,
        default=PaymentStatus.UNPAID,
        server_default=PaymentStatus.UNPAID.value,
    )
    payment_method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod, "payment_method"))
    delivery_type: Mapped[DeliveryType | None] = mapped_column(enum_type(DeliveryType, "delivery_type"))
    delivery_address: Mapped[str | None] = mapped_column(sa.Text)
    delivery_latitude: Mapped[Decimal | None] = mapped_column(coordinate_type())
    delivery_longitude: Mapped[Decimal | None] = mapped_column(coordinate_type())
    delivery_date: Mapped[date | None] = mapped_column(sa.Date, index=True)
    delivery_time: Mapped[time | None] = mapped_column(sa.Time)
    total_amount: Mapped[Decimal] = mapped_column(
        money_type(), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    paid_amount: Mapped[Decimal] = mapped_column(
        money_type(), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    comment: Mapped[str | None] = mapped_column(sa.Text)
    is_repeat_customer: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancel_reason: Mapped[str | None] = mapped_column(sa.Text)

    customer: Mapped["Customer"] = relationship(back_populates="orders")
    conversation: Mapped[Optional["Conversation"]] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.id",
    )
    delivery: Mapped[Optional["Delivery"]] = relationship(
        back_populates="order",
        uselist=False,
        cascade="all, delete-orphan",
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="Payment.id",
    )
    events: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderEvent.id",
    )


class OrderItem(TimestampMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (sa.CheckConstraint("quantity > 0", name="quantity_positive"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int | None] = mapped_column(sa.ForeignKey("products.id", ondelete="SET NULL"))
    product_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(money_type(), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(money_type(), nullable=False)
    comment: Mapped[str | None] = mapped_column(sa.Text)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Optional["Product"]] = relationship()


class OrderEvent(CreatedAtMixin, Base):
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_type: Mapped[ActorType] = mapped_column(enum_type(ActorType, "actor_type"), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    # CREATED, UPDATED, ITEMS_CHANGED, STATUS_CHANGED, PAYMENT_CHANGED, DELIVERY_CHANGED, CONFIRMED, CANCELLED
    event_type: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    changes: Mapped[dict[str, Any]] = mapped_column(json_dict_type(), nullable=False, default=dict)
    comment: Mapped[str | None] = mapped_column(sa.Text)

    order: Mapped[Order] = relationship(back_populates="events")
    actor_user: Mapped[Optional["User"]] = relationship()


class Payment(CreatedAtMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (sa.CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[PaymentKind] = mapped_column(enum_type(PaymentKind, "kind"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(money_type(), nullable=False)
    method: Mapped[PaymentMethod | None] = mapped_column(enum_type(PaymentMethod, "method"))
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_by_user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    paid_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=now_utc, server_default=sa.func.now()
    )

    order: Mapped[Order] = relationship(back_populates="payments")
    created_by_user: Mapped[Optional["User"]] = relationship()
