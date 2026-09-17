"""Expenses routes — docs/architecture/04-api.md §7a "Расходы".

- ``GET    /api/expenses``       STAFF  ``date_from``, ``date_to``, ``category``, pagination → ``Page[ExpenseOut]``
- ``POST   /api/expenses``       ADMIN  ``ExpenseCreate`` → ``ExpenseOut`` 201
- ``PATCH  /api/expenses/{id}``  ADMIN  ``ExpenseUpdate`` (partial) → ``ExpenseOut``
- ``DELETE /api/expenses/{id}``  ADMIN  → 204 (hard delete)

Thin layer (01 §3): every rule lives in ``ExpenseService``.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import AdminUser, DbSession, Pagination, StaffUser
from app.models.enums import ExpenseCategory
from app.models.expense import Expense
from app.schemas.common import Page
from app.schemas.expense import ExpenseCreate, ExpenseOut, ExpenseUpdate
from app.services.expense_service import ExpenseService

router = APIRouter(prefix="/expenses", tags=["expenses"])

ExpenseId = Annotated[int, Path(ge=1, description="Идентификатор расхода")]


@router.get("", response_model=Page[ExpenseOut], summary="Список расходов")
def list_expenses(
    db: DbSession,
    user: StaffUser,
    pagination: Pagination,
    date_from: Annotated[date | None, Query(description="С даты (включительно)")] = None,
    date_to: Annotated[date | None, Query(description="По дату (включительно)")] = None,
    category: Annotated[ExpenseCategory | None, Query(description="Категория")] = None,
) -> Page[ExpenseOut]:
    return ExpenseService(db).list(
        date_from=date_from,
        date_to=date_to,
        category=category,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED, summary="Добавить расход")
def create_expense(payload: ExpenseCreate, db: DbSession, user: AdminUser) -> Expense:
    return ExpenseService(db).create(payload, user=user)


@router.patch("/{expense_id}", response_model=ExpenseOut, summary="Изменить расход")
def update_expense(expense_id: ExpenseId, payload: ExpenseUpdate, db: DbSession, user: AdminUser) -> Expense:
    return ExpenseService(db).update(expense_id, payload, user=user)


@router.delete(
    "/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Удалить расход",
)
def delete_expense(expense_id: ExpenseId, db: DbSession, user: AdminUser) -> Response:
    ExpenseService(db).delete(expense_id, user=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
