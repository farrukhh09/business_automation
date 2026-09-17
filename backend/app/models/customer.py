"""Customers (02-data-model.md: customers).

Derived values ``orders_count``, ``total_spent`` are computed by queries (not stored);
``customer_type`` is derived from ``is_new`` (03-business-rules.md §2).
"""

from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime, enum_type
from app.models.enums import Language

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.order import Order


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    instagram_user_id: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    name: Mapped[str | None] = mapped_column(sa.String(128))
    phone: Mapped[str | None] = mapped_column(sa.String(32), index=True)
    username: Mapped[str | None] = mapped_column(sa.String(64))
    language: Mapped[Language] = mapped_column(
        enum_type(Language, "language"), nullable=False, default=Language.RU, server_default=Language.RU.value
    )
    is_new: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, server_default=sa.text("true"))
    is_blocked: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.text("false"))
    last_order_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    notes: Mapped[str | None] = mapped_column(sa.Text)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer", order_by="Order.id")
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="customer", order_by="Conversation.id"
    )

    @property
    def customer_type(self) -> str:
        """``"NEW"`` or ``"REGULAR"`` (04-api.md CustomerListItem)."""
        return "NEW" if self.is_new else "REGULAR"
