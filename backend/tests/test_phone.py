"""Phone normalization (03-business-rules.md §2)."""

import pytest

from app.services.phone import normalize_phone, phone_digits


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 9 digits → Tajik national number
        ("901234567", "+992901234567"),
        ("90 123 45 67", "+992901234567"),
        ("90-123-45-67", "+992901234567"),
        ("(90) 123-45-67", "+992901234567"),
        ("(90)1234567", "+992901234567"),
        ("90.123.45.67", "+992901234567"),
        ("  90 123 4567  ", "+992901234567"),
        ("88 123 45 67", "+992881234567"),  # 8 inside a national number is just a digit
        # with the country code
        ("992901234567", "+992901234567"),
        ("+992901234567", "+992901234567"),
        ("+992 90 123 45 67", "+992901234567"),
        ("+992 (93) 555-66-77", "+992935556677"),
        ("+992-90-123-45-67", "+992901234567"),
        ("00992 90 123 4567", "+992901234567"),
        # the customer's own examples (03 §2)
        ("992 92 780 91 52", "+992927809152"),
        ("927809152", "+992927809152"),
        ("0927809152", "+992927809152"),  # a trunk 0/8 before the 9 digits is dropped
        ("8 927809152", "+992927809152"),
        # other international numbers must be valid for their country; the Russian trunk 8 is understood
        ("8 917 123 45 67", "+79171234567"),
        ("8(917)123-45-67", "+79171234567"),
        ("+7 (917) 123-45-67", "+79171234567"),
        ("79171234567", "+79171234567"),
        ("+1 202 555 0143", "+12025550143"),
        ("тел: +992 90 123 45 67", "+992901234567"),
    ],
)
def test_normalize_phone_valid(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "нет телефона",
        "12345678",  # 8 digits: too short
        "90 123 45",
        "+992",
        "+992 90 123 45",  # Tajik code with too few digits
        "99290123456",  # 11 digits starting with 992
        "9929012345678",  # 13 digits starting with 992
        "1234567890123456",  # longer than E.164 allows
        "9277373738292",  # typed by a real customer: 13 digits, no such number
        "9277777777",  # 10 digits: one digit too many for a Tajik number
        "+992 1 234",  # far too short for a Tajik number
        "+7 123 456 78 90",  # not a valid Russian number
    ],
)
def test_normalize_phone_invalid(raw: str | None) -> None:
    assert normalize_phone(raw) is None


def test_normalized_phone_is_idempotent() -> None:
    normalized = normalize_phone("90 123 45 67")
    assert normalized is not None
    assert normalize_phone(normalized) == normalized


def test_phone_digits() -> None:
    assert phone_digits("+992 (90) 123-45-67") == "992901234567"
    assert phone_digits(None) == ""
