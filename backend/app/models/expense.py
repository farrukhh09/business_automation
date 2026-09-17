"""Business expenses (02-data-model.md: expenses; 03-business-rules.md §4 "Расходы и прибыль")."""

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_type, money_type
from app.models.enums import ExpenseCategory

if TYPE_CHECKING:
    from app.models.user import User


class Expense(TimestampMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (sa.CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    expense_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    category: Mapped[ExpenseCategory] = mapped_column(enum_type(ExpenseCategory, "category"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(money_type(), nullable=False)
    comment: Mapped[str | None] = mapped_column(sa.Text)
    created_by_user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    created_by_user: Mapped[Optional["User"]] = relationship()
