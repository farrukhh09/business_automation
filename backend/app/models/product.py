"""Products (02-data-model.md: products)."""

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UTCDateTime, json_list_type, money_type


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (sa.CheckConstraint("price > 0", name="price_positive"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    price: Mapped[Decimal] = mapped_column(money_type(), nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="TJS", server_default="TJS")
    unit: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="шт.", server_default="шт.")
    aliases: Mapped[list[str]] = mapped_column(json_list_type(), nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, server_default=sa.text("true"))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sort_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
