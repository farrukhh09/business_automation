"""Russian formatting helpers (app/services/formatting.py) — 03-business-rules.md §4, SPEC §19, §22."""

from datetime import date, time
from decimal import Decimal

import pytest

from app.services.formatting import (
    CURRENCY_WORD,
    DEFAULT_UNIT,
    MONTHS_GENITIVE_RU,
    THOUSANDS_SEPARATOR,
    format_amount,
    format_date_ru,
    format_day_month_ru,
    format_money,
    format_quantity,
    format_time,
)


class TestFormatAmount:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("0"), "0"),
            (Decimal("5"), "5"),
            (Decimal("999"), "999"),
            (Decimal("1000"), "1 000"),
            (Decimal("12500"), "12 500"),
            (Decimal("12500.00"), "12 500"),
            (Decimal("123456789"), "123 456 789"),
            (Decimal("12500.50"), "12 500,50"),
            (Decimal("0.05"), "0,05"),
            (Decimal("1000.10"), "1 000,10"),
            (Decimal("-2500"), "-2 500"),
            (Decimal("-2500.50"), "-2 500,50"),
        ],
    )
    def test_formats_decimals(self, value: Decimal, expected: str) -> None:
        assert format_amount(value) == expected

    def test_accepts_int_float_and_str(self) -> None:
        assert format_amount(12500) == "12 500"
        assert format_amount(12500.5) == "12 500,50"
        assert format_amount("12500.50") == "12 500,50"

    def test_rounds_half_up_to_two_decimals(self) -> None:
        assert format_amount(Decimal("10.005")) == "10,01"
        assert format_amount(Decimal("10.004")) == "10"
        assert format_amount(Decimal("10.994")) == "10,99"

    def test_thousands_separator_is_a_plain_space(self) -> None:
        assert THOUSANDS_SEPARATOR == " "
        assert " " not in format_amount(Decimal("12500"))

    def test_rejects_non_numeric_values(self) -> None:
        with pytest.raises(ValueError):
            format_amount("не число")


class TestFormatMoney:
    def test_spec_examples(self) -> None:
        assert format_money(Decimal("12500")) == "12 500 сомони"
        assert format_money(Decimal("12500.50")) == "12 500,50 сомони"
        assert format_money(Decimal("10000")) == "10 000 сомони"
        assert format_money(Decimal("2500")) == "2 500 сомони"

    def test_zero_keeps_the_currency_word(self) -> None:
        assert format_money(Decimal("0")) == f"0 {CURRENCY_WORD}"

    def test_currency_word_can_be_overridden(self) -> None:
        assert format_money(Decimal("100"), "сомонӣ") == "100 сомонӣ"


class TestFormatQuantity:
    def test_uses_the_product_unit(self) -> None:
        assert format_quantity(5, "шт.") == "5 шт."
        assert format_quantity(2, "кг") == "2 кг"

    @pytest.mark.parametrize("unit", [None, "", "   "])
    def test_falls_back_to_pieces(self, unit: str | None) -> None:
        assert format_quantity(3, unit) == f"3 {DEFAULT_UNIT}"


class TestFormatDates:
    def test_date_ru_is_zero_padded(self) -> None:
        assert format_date_ru(date(2026, 9, 15)) == "15.09.2026"
        assert format_date_ru(date(2026, 1, 5)) == "05.01.2026"

    def test_day_month_is_genitive_without_leading_zero(self) -> None:
        assert format_day_month_ru(date(2026, 9, 16)) == "16 сентября"
        assert format_day_month_ru(date(2026, 1, 1)) == "1 января"
        assert format_day_month_ru(date(2026, 5, 9)) == "9 мая"

    def test_every_month_has_a_genitive_name(self) -> None:
        names = [format_day_month_ru(date(2026, month, 1)) for month in range(1, 13)]
        assert names == [f"1 {name}" for name in MONTHS_GENITIVE_RU[1:]]
        assert len(set(names)) == 12

    def test_time_is_hh_mm(self) -> None:
        assert format_time(time(18, 0)) == "18:00"
        assert format_time(time(9, 5)) == "09:05"
        assert format_time(time(23, 59, 59)) == "23:59"
