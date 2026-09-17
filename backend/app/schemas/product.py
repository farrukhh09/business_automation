"""Product schemas (04-api.md §4 "Products").

``ProductCreate = {name, description?, price: Money (>0), currency? = "TJS", unit? = "шт.",
aliases?: string[], is_active? = true, sort_order? = 0}`` and
``ProductOut = ProductCreate + {id, created_at, updated_at}``.

Conventions:

- strings are trimmed (``str_strip_whitespace``); a blank ``description`` means "no description";
- ``price`` is ``Money`` and must be > 0 (02-data-model.md: CHECK ``products.price > 0``);
- ``aliases`` are normalized by ``ProductService`` (trimmed, lowercased, de-duplicated, blanks
  removed) — the schema only bounds their size;
- ``ProductUpdate`` is the partial body of ``PATCH /products/{id}``: a field that is absent stays
  unchanged, ``description: null`` (or ``""``) clears the description.
"""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.common import Money, ORMModel, PositiveMoney

NAME_MAX = 128  # products.name String(128)
UNIT_MAX = 16  # products.unit String(16)
CURRENCY_LENGTH = 3  # products.currency String(3)
DESCRIPTION_MAX = 4000
ALIAS_MAX = 128
MAX_ALIASES = 50
SORT_ORDER_ABS_MAX = 1_000_000

DEFAULT_CURRENCY = "TJS"
DEFAULT_UNIT = "шт."

CURRENCY_ERROR = "Код валюты состоит из трёх латинских букв, например TJS"

Alias = Annotated[str, StringConstraints(max_length=ALIAS_MAX)]


def normalize_currency(value: str) -> str:
    """``"tjs"`` → ``"TJS"``; anything that is not a 3-letter ISO-4217 code is rejected."""
    code = value.strip().upper()
    if len(code) != CURRENCY_LENGTH or not (code.isascii() and code.isalpha()):
        raise ValueError(CURRENCY_ERROR)
    return code


def blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    price: PositiveMoney
    currency: str = Field(default=DEFAULT_CURRENCY, max_length=CURRENCY_LENGTH)
    unit: str = Field(default=DEFAULT_UNIT, min_length=1, max_length=UNIT_MAX)
    aliases: list[Alias] = Field(default_factory=list, max_length=MAX_ALIASES)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=-SORT_ORDER_ABS_MAX, le=SORT_ORDER_ABS_MAX)

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: Any) -> Any:
        return blank_to_none(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: Any) -> Any:
        return normalize_currency(value) if isinstance(value, str) else value


class ProductUpdate(BaseModel):
    """Every field optional. Absent → unchanged; ``description: null`` clears the description."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    price: PositiveMoney | None = None
    currency: str | None = Field(default=None, max_length=CURRENCY_LENGTH)
    unit: str | None = Field(default=None, min_length=1, max_length=UNIT_MAX)
    aliases: list[Alias] | None = Field(default=None, max_length=MAX_ALIASES)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=-SORT_ORDER_ABS_MAX, le=SORT_ORDER_ABS_MAX)

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: Any) -> Any:
        return blank_to_none(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: Any) -> Any:
        return normalize_currency(value) if isinstance(value, str) else value


class ProductOut(ORMModel):
    id: int
    name: str
    description: str | None = None
    price: Money
    currency: str
    unit: str
    aliases: list[str] = Field(default_factory=list)
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime
