"""Expense schemas (04-api.md §7a "Расходы").

``ExpenseCreate = {expense_date, category, amount (> 0), comment?}``,
``ExpenseOut = ExpenseCreate + {id, created_by_user_id, created_at, updated_at}``.
``ExpenseUpdate`` leaves absent fields unchanged; ``comment: null`` (or blank) clears the comment.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ExpenseCategory
from app.schemas.common import Money, ORMModel, PositiveMoney
from app.schemas.product import blank_to_none

COMMENT_MAX = 2000


class ExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expense_date: date
    category: ExpenseCategory
    amount: PositiveMoney
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)

    @field_validator("comment", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        return blank_to_none(value)


class ExpenseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expense_date: date | None = None
    category: ExpenseCategory | None = None
    amount: PositiveMoney | None = None
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)

    @field_validator("comment", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        return blank_to_none(value)


class ExpenseOut(ORMModel):
    id: int
    expense_date: date
    category: ExpenseCategory
    amount: Money
    comment: str | None = None
    created_by_user_id: int | None = None
    created_at: datetime
    updated_at: datetime
