"""Customer schemas (04-api.md §3).

``CustomerListItem`` carries the derived values ``orders_count`` / ``total_spent`` (VALID orders
only, 03 §4) and ``customer_type`` (``NEW``/``REGULAR`` from ``is_new``, 03 §2).
``CustomerDetail`` adds the notes, the conversation id and the full order history (newest first).
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.customer import Customer
from app.models.enums import Language
from app.schemas.common import Money, NonNegativeMoney, ORMModel
from app.schemas.order import OrderListItem

NAME_MAX = 128
PHONE_MAX = 32
USERNAME_MAX = 64
NOTES_MAX = 4000

CustomerType = Literal["NEW", "REGULAR"]


class CustomerCreate(BaseModel):
    """``POST /customers`` (STAFF). The phone is normalized by ``CustomerService`` (03 §2)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=NAME_MAX)
    phone: str | None = Field(default=None, max_length=PHONE_MAX)
    username: str | None = Field(default=None, max_length=USERNAME_MAX)
    language: Language = Language.RU
    notes: str | None = Field(default=None, max_length=NOTES_MAX)


class CustomerUpdate(BaseModel):
    """``PATCH /customers/{id}`` (STAFF). Omitted fields stay unchanged, ``null`` clears the value."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    phone: str | None = Field(default=None, max_length=PHONE_MAX)
    language: Language | None = None
    notes: str | None = Field(default=None, max_length=NOTES_MAX)
    is_blocked: bool | None = None


class CustomerListItem(ORMModel):
    id: int
    name: str | None = None
    username: str | None = None
    phone: str | None = None
    instagram_user_id: str | None = None
    language: Language
    is_new: bool
    is_blocked: bool
    customer_type: CustomerType
    orders_count: int = 0
    total_spent: NonNegativeMoney = Decimal("0.00")
    last_order_at: datetime | None = None
    created_at: datetime


class CustomerDetail(CustomerListItem):
    notes: str | None = None
    conversation_id: int | None = None
    orders: list[OrderListItem] = Field(default_factory=list)


def build_customer_list_item(
    customer: Customer,
    orders_count: int = 0,
    total_spent: Money | Decimal | int = Decimal("0.00"),
) -> CustomerListItem:
    """``orders_count``/``total_spent`` come from ``CustomerRepository`` (VALID orders only)."""
    return CustomerListItem(
        id=customer.id,
        name=customer.name,
        username=customer.username,
        phone=customer.phone,
        instagram_user_id=customer.instagram_user_id,
        language=customer.language,
        is_new=customer.is_new,
        is_blocked=customer.is_blocked,
        customer_type=customer.customer_type,  # type: ignore[arg-type]
        orders_count=orders_count,
        total_spent=Decimal(str(total_spent)),
        last_order_at=customer.last_order_at,
        created_at=customer.created_at,
    )


def build_customer_detail(
    customer: Customer,
    orders_count: int = 0,
    total_spent: Money | Decimal | int = Decimal("0.00"),
    conversation_id: int | None = None,
    orders: list[OrderListItem] | None = None,
) -> CustomerDetail:
    base = build_customer_list_item(customer, orders_count, total_spent)
    return CustomerDetail(
        **base.model_dump(),
        notes=customer.notes,
        conversation_id=conversation_id,
        orders=list(orders or []),
    )
