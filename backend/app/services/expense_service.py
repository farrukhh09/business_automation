"""Business expenses entered in the admin panel (03-business-rules.md §4 "Расходы и прибыль", 04-api.md §7a).

Rules:

- an expense has a business date, a category, a positive amount and an optional comment;
- the date may not be in the future (an expense is recorded when the money is spent — a future
  date is almost always a typo that would silently shift the profit of another period);
- the list is ordered by ``expense_date`` desc, then id desc, filterable by date range and category;
- ``DELETE`` removes the row for good; every write is logged with the acting user.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.core.logging import get_logger, log_event
from app.core.time import business_today
from app.models.enums import ExpenseCategory
from app.models.expense import Expense
from app.models.user import User
from app.repositories.expenses import ExpenseRepository
from app.schemas.common import Page
from app.schemas.expense import ExpenseCreate, ExpenseOut, ExpenseUpdate
from app.services.product_service import apply_patch, validate_payload

logger = get_logger(__name__)

EXPENSE_INVALID_DETAIL = "Некорректные данные расхода"
CLEARABLE_EXPENSE_FIELDS: frozenset[str] = frozenset({"comment"})


def _check_not_in_future(expense_date: date) -> None:
    if expense_date > business_today():
        raise ValidationError("expense_date_in_future", "Дата расхода не может быть в будущем")


class ExpenseService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = ExpenseRepository(db)

    # ------------------------------------------------------------------ reads

    def list(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        category: ExpenseCategory | str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[ExpenseOut]:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValidationError("Начало периода не может быть позже его конца")
        items, total = self.repository.list_page(
            date_from=date_from, date_to=date_to, category=category, page=page, page_size=page_size
        )
        return Page[ExpenseOut](
            items=[ExpenseOut.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ------------------------------------------------------------------ writes

    def create(self, data: ExpenseCreate | Mapping[str, Any], user: User | None = None) -> Expense:
        payload = validate_payload(ExpenseCreate, data, EXPENSE_INVALID_DETAIL)
        _check_not_in_future(payload.expense_date)
        expense = Expense(
            expense_date=payload.expense_date,
            category=payload.category,
            amount=payload.amount,
            comment=payload.comment,
            created_by_user_id=user.id if user else None,
        )
        self.repository.add(expense)
        self.db.commit()
        log_event(
            logger,
            "expense.created",
            expense_id=expense.id,
            category=expense.category.value,
            amount=str(expense.amount),
            expense_date=expense.expense_date.isoformat(),
            user_id=user.id if user else None,
        )
        return expense

    def update(self, expense_id: int, data: ExpenseUpdate | Mapping[str, Any], user: User | None = None) -> Expense:
        payload = validate_payload(ExpenseUpdate, data, EXPENSE_INVALID_DETAIL)
        expense = self.repository.get_or_raise(expense_id)
        changes = payload.model_dump(exclude_unset=True)
        if changes.get("expense_date") is not None:
            _check_not_in_future(changes["expense_date"])
        changed = apply_patch(expense, changes, clearable=CLEARABLE_EXPENSE_FIELDS)
        self.repository.flush()
        self.db.commit()
        log_event(
            logger, "expense.updated", expense_id=expense_id, fields=sorted(changed), user_id=user.id if user else None
        )
        return expense

    def delete(self, expense_id: int, user: User | None = None) -> None:
        expense = self.repository.get_or_raise(expense_id)
        self.repository.delete(expense)
        self.db.commit()
        log_event(
            logger,
            "expense.deleted",
            expense_id=expense_id,
            category=expense.category.value,
            amount=str(expense.amount),
            user_id=user.id if user else None,
        )
