"""Payment receipts read by the bot (02-data-model.md: payment_receipts; 03-business-rules.md §3).

One row per screenshot the bot recognised as a transfer receipt. The row is what makes a reused
receipt visible: the same file (``file_sha256``), the same transaction number (``reference``) or the
same transfer (``fingerprint`` = amount + time + sender) sent again — for this order or another one.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UTCDateTime, json_list_type, money_type

if TYPE_CHECKING:
    from app.models.order import Order


class PaymentReceipt(CreatedAtMixin, Base):
    __tablename__ = "payment_receipts"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(
        sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[int] = mapped_column(
        sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    file_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    reference: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    fingerprint: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    amount: Mapped[Decimal | None] = mapped_column(money_type())
    currency: Mapped[str | None] = mapped_column(sa.String(16))
    recipient: Mapped[str | None] = mapped_column(sa.String(64))
    sender: Mapped[str | None] = mapped_column(sa.String(64))
    provider: Mapped[str | None] = mapped_column(sa.String(64))
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    ok: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    problems: Mapped[list[Any]] = mapped_column(json_list_type(), nullable=False, default=list)

    order: Mapped["Order"] = relationship()
