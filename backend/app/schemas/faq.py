"""FAQ schemas (04-api.md §10 "FAQ", SPEC §14).

``FaqCreate = {question, answer, question_tg?, answer_tg?, keywords?: string[], is_active?,
sort_order?}`` and ``FaqOut = FaqCreate + {id, created_at, updated_at}``.

Conventions mirror the products schemas: strings are trimmed, blank optional strings become
``null``, ``keywords`` are normalized by ``FaqService`` (trimmed, lowercased, de-duplicated) and
``FaqUpdate`` leaves absent fields unchanged while ``question_tg: null`` clears the Tajik text.
"""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.common import ORMModel
from app.schemas.product import SORT_ORDER_ABS_MAX, blank_to_none

TEXT_MAX = 4000
KEYWORD_MAX = 128
MAX_KEYWORDS = 50

Keyword = Annotated[str, StringConstraints(max_length=KEYWORD_MAX)]


class FaqCreate(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=TEXT_MAX)
    answer: str = Field(min_length=1, max_length=TEXT_MAX)
    question_tg: str | None = Field(default=None, max_length=TEXT_MAX)
    answer_tg: str | None = Field(default=None, max_length=TEXT_MAX)
    keywords: list[Keyword] = Field(default_factory=list, max_length=MAX_KEYWORDS)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=-SORT_ORDER_ABS_MAX, le=SORT_ORDER_ABS_MAX)

    @field_validator("question_tg", "answer_tg", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        return blank_to_none(value)


class FaqUpdate(BaseModel):
    """Every field optional. Absent → unchanged; ``question_tg``/``answer_tg`` ``null`` clears."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    question: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX)
    answer: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX)
    question_tg: str | None = Field(default=None, max_length=TEXT_MAX)
    answer_tg: str | None = Field(default=None, max_length=TEXT_MAX)
    keywords: list[Keyword] | None = Field(default=None, max_length=MAX_KEYWORDS)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=-SORT_ORDER_ABS_MAX, le=SORT_ORDER_ABS_MAX)

    @field_validator("question_tg", "answer_tg", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        return blank_to_none(value)


class FaqOut(ORMModel):
    id: int
    question: str
    answer: str
    question_tg: str | None = None
    answer_tg: str | None = None
    keywords: list[str] = Field(default_factory=list)
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime
