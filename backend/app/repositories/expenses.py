"""Expenses (02-data-model.md: expenses; 04-api.md §7a)."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import Select, func, select

from app.models.enums import ExpenseCategory
from app.models.expense import Expense
from app.repositories.base import BaseRepository, coerce_enum

MONEY_QUANT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    # SQLite sums NUMERIC as float; the 2-place quantize absorbs the binary noise.
    return Decimal(str(value if value is not None else 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


class ExpenseRepository(BaseRepository[Expense]):
    model = Expense
    not_found_detail = "Расход не найден"

    def list_page(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        category: ExpenseCategory | str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Expense], int]:
        """Newest expense date first, then the most recently added."""
        stmt: Select[Any] = select(Expense)
        if date_from is not None:
            stmt = stmt.where(Expense.expense_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Expense.expense_date <= date_to)
        if category:
            stmt = stmt.where(
                Expense.category == coerce_enum(ExpenseCategory, category, "Недопустимая категория расхода")
            )
        stmt = stmt.order_by(Expense.expense_date.desc(), Expense.id.desc())
        return self.paginate(stmt, page, page_size)

    def totals_by_category(self, date_from: date, date_to: date) -> dict[ExpenseCategory, Decimal]:
        stmt = (
            select(Expense.category, func.coalesce(func.sum(Expense.amount), 0))
            .where(Expense.expense_date >= date_from, Expense.expense_date <= date_to)
            .group_by(Expense.category)
        )
        return {ExpenseCategory(category): _money(total) for category, total in self.db.execute(stmt).all()}

    def totals_by_day(self, date_from: date, date_to: date) -> dict[date, Decimal]:
        stmt = (
            select(Expense.expense_date, func.coalesce(func.sum(Expense.amount), 0))
            .where(Expense.expense_date >= date_from, Expense.expense_date <= date_to)
            .group_by(Expense.expense_date)
        )
        return {day: _money(total) for day, total in self.db.execute(stmt).all()}
