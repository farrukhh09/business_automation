"""Common API types (docs/architecture/04-api.md §0).

- ``Money``: ``Decimal`` inside the app (quantized to 2 places, ROUND_HALF_UP); JSON number out.
- ``HHMM``: ``datetime.time``; accepts ``"HH:MM"`` or ``"HH:MM:SS"``, serializes ``"HH:MM"``.
- ``Page[T]``: ``{"items", "total", "page", "page_size"}``.
- ``ErrorOut``: ``{"detail", "code"}`` (+ optional extra keys).
"""

import re
from datetime import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, Generic, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

T = TypeVar("T")

# --------------------------------------------------------------------------- money

MONEY_QUANT = Decimal("0.01")
MONEY_MAX = Decimal("9999999999.99")  # Numeric(12, 2)


def quantize_money(value: Decimal | int | float | str) -> Decimal:
    """Round to 2 decimal places with ROUND_HALF_UP (03-business-rules.md §1.4).

    Always raises ``ValueError`` for unusable input: ``decimal.InvalidOperation`` (e.g. ``1e30``
    quantized to 0.01 needs more digits than the decimal context allows) is not a ``ValueError``
    and would otherwise escape pydantic validation as a 500.
    """
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Некорректная сумма") from exc
    if not decimal_value.is_finite():
        raise ValueError("Некорректная сумма")
    try:
        return decimal_value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Сумма слишком большая") from exc


def _validate_money(value: Decimal) -> Decimal:
    if value.is_finite() and abs(value) > MONEY_MAX:
        raise ValueError("Сумма слишком большая")
    quantized = quantize_money(value)
    if abs(quantized) > MONEY_MAX:
        raise ValueError("Сумма слишком большая")
    return quantized


def _validate_positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    return value


def _validate_non_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("Сумма не может быть отрицательной")
    return value


def _serialize_money(value: Decimal) -> float:
    return float(quantize_money(value))


_MONEY_SERIALIZER = PlainSerializer(_serialize_money, return_type=float, when_used="json")

Money = Annotated[
    Decimal,
    AfterValidator(_validate_money),
    _MONEY_SERIALIZER,
    WithJsonSchema({"type": "number", "examples": [1500.0]}),
]
PositiveMoney = Annotated[
    Decimal,
    AfterValidator(_validate_money),
    AfterValidator(_validate_positive),
    _MONEY_SERIALIZER,
    WithJsonSchema({"type": "number", "exclusiveMinimum": 0, "examples": [1500.0]}),
]
NonNegativeMoney = Annotated[
    Decimal,
    AfterValidator(_validate_money),
    AfterValidator(_validate_non_negative),
    _MONEY_SERIALIZER,
    WithJsonSchema({"type": "number", "minimum": 0, "examples": [0.0]}),
]

# --------------------------------------------------------------------------- time

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")
HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$"


def _parse_hhmm(value: Any) -> time:
    if isinstance(value, time):
        if value.tzinfo is not None:
            raise ValueError("Время указывается без часового пояса")
        return value.replace(second=0, microsecond=0)
    if isinstance(value, str):
        match = _HHMM_RE.match(value.strip())
        if match:
            return time(int(match.group(1)), int(match.group(2)))
    raise ValueError("Время должно быть в формате ЧЧ:ММ")


def _serialize_hhmm(value: time) -> str:
    return value.strftime("%H:%M")


HHMM = Annotated[
    time,
    BeforeValidator(_parse_hhmm),
    PlainSerializer(_serialize_hhmm, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": HHMM_PATTERN, "examples": ["18:00"]}),
]

# --------------------------------------------------------------------------- models


class ORMModel(BaseModel):
    """Base for response schemas built from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class ErrorOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    detail: str
    code: str
