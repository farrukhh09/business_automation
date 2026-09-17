"""Russian formatting of money, quantities, dates and times (03-business-rules.md §4, SPEC §19, §22).

Shared by the production summary, the daily report and the bot reply templates (``app/ai/templates``)
so that a sum looks the same everywhere::

    format_money(Decimal("12500"))     -> "12 500 сомони"
    format_money(Decimal("12500.50"))  -> "12 500,50 сомони"
    format_date_ru(date(2026, 9, 16))  -> "16.09.2026"
    format_day_month_ru(date(2026, 9, 16)) -> "16 сентября"
    format_time(time(18, 0))           -> "18:00"

The thousands separator is a plain space (U+0020): the texts are copied into Instagram messages and
into the operator's clipboard, where a non-breaking space is silently mangled by some clients.
Kopecks are printed only when they are not zero (SPEC §22 shows "12 500 сомони").
"""

from datetime import date, time
from decimal import Decimal

from app.schemas.common import quantize_money

CURRENCY_WORD = "сомони"
THOUSANDS_SEPARATOR = " "
DECIMAL_SEPARATOR = ","
DEFAULT_UNIT = "шт."
MINUS = "-"

# Genitive ("16 сентября"), index 1..12.
MONTHS_GENITIVE_RU: tuple[str, ...] = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

Amount = Decimal | int | float | str


def format_amount(value: Amount) -> str:
    """``12500`` → ``"12 500"``; ``12500.5`` → ``"12 500,50"``; ``-2500`` → ``"-2 500"``.

    The value is rounded to 2 decimals (ROUND_HALF_UP) exactly like every stored money column.
    """
    amount = quantize_money(value)
    sign = MINUS if amount < 0 else ""
    amount = -amount if amount < 0 else amount
    whole = int(amount)
    kopecks = int((amount - whole) * 100)
    grouped = f"{whole:,}".replace(",", THOUSANDS_SEPARATOR)
    if kopecks:
        return f"{sign}{grouped}{DECIMAL_SEPARATOR}{kopecks:02d}"
    return f"{sign}{grouped}"


def format_money(value: Amount, currency_word: str = CURRENCY_WORD) -> str:
    """``12500`` → ``"12 500 сомони"`` (01-overview.md §4: the currency is written out in texts)."""
    return f"{format_amount(value)} {currency_word}"


def format_quantity(quantity: int, unit: str | None = None) -> str:
    """``(5, "шт.")`` → ``"5 шт."``; a missing unit falls back to ``"шт."`` (03 §4)."""
    return f"{quantity} {(unit or DEFAULT_UNIT).strip() or DEFAULT_UNIT}"


def format_date_ru(value: date) -> str:
    """``date(2026, 9, 16)`` → ``"16.09.2026"`` (SPEC §22 report header)."""
    return f"{value.day:02d}.{value.month:02d}.{value.year:04d}"


def format_day_month_ru(value: date) -> str:
    """``date(2026, 9, 16)`` → ``"16 сентября"`` (SPEC §19 production summary)."""
    return f"{value.day} {MONTHS_GENITIVE_RU[value.month]}"


def format_time(value: time) -> str:
    """``time(18, 0)`` → ``"18:00"`` (04-api.md §0: times are ``HH:MM``)."""
    return f"{value.hour:02d}:{value.minute:02d}"
