"""Phone number normalization and validation (03-business-rules.md §2).

Customers write Tajik numbers in every shape — ``92 780 91 52``, ``927809152``, ``+992 92 780 91 52``,
``992927809152``, ``(92) 780-91-52`` — and sometimes an obviously wrong one (``9277373738292``,
``9277777777``). Every number is validated with Google's libphonenumber (``phonenumbers``), so only a
number that can really exist is stored:

- a number written with ``+`` (or the ``00`` international prefix) is parsed as international and
  must be valid for its country (a customer from Russia may leave ``+7 …``);
- everything else is read as a **Tajik** number: 9 national digits (``92 780 91 52``), the country
  code without ``+`` (``992 92 780 91 52``), or a trunk prefix ``8``/``0`` before the 9 digits;
- 11 digits starting with ``7``/``8`` that are not Tajik are tried as a Russian number written
  without ``+`` (``8 917 123 45 67`` → ``+79171234567``) — the one foreign format Dushanbe
  customers actually use;
- anything libphonenumber rejects (wrong length, unknown operator prefix) → ``None``: the bot asks
  again and names the two accepted forms (SPEC §40: never store what we cannot trust).

The result is E.164 (``+992927809152``).
"""

import re

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

TAJIK_REGION = "TJ"
TAJIK_COUNTRY_CODE = "992"
NATIONAL_NUMBER_LENGTH = 9
RUSSIA_COUNTRY_CODE = "7"
#: The two forms the bot names when a number is not recognised (RU/TG templates).
PHONE_EXAMPLE_NATIONAL = "92 780 91 52"
PHONE_EXAMPLE_INTERNATIONAL = "+992 92 780 91 52"

_NON_DIGITS_RE = re.compile(r"\D")
_TRUNK_PREFIXES = ("8", "0")
_RUSSIAN_LENGTH = 11

__all__ = [
    "PHONE_EXAMPLE_INTERNATIONAL",
    "PHONE_EXAMPLE_NATIONAL",
    "is_valid_phone",
    "normalize_phone",
    "phone_digits",
]


def _written_with_plus(text: str) -> bool:
    """``+992 …`` and ``тел: +992 …`` are international; a ``+`` after the first digit is noise."""
    for char in text:
        if char.isdigit():
            return False
        if char == "+":
            return True
    return False


def _tajik_candidates(digits: str) -> list[str]:
    """E.164 readings of digits typed without ``+`` — Tajik first, then the Russian trunk form."""
    candidates: list[str] = []
    if len(digits) == NATIONAL_NUMBER_LENGTH:
        candidates.append(f"+{TAJIK_COUNTRY_CODE}{digits}")
    elif len(digits) == len(TAJIK_COUNTRY_CODE) + NATIONAL_NUMBER_LENGTH and digits.startswith(TAJIK_COUNTRY_CODE):
        candidates.append(f"+{digits}")
    elif len(digits) == NATIONAL_NUMBER_LENGTH + 1 and digits[0] in _TRUNK_PREFIXES:
        candidates.append(f"+{TAJIK_COUNTRY_CODE}{digits[1:]}")
    if len(digits) == _RUSSIAN_LENGTH and digits[0] in (RUSSIA_COUNTRY_CODE, "8"):
        candidates.append(f"+{RUSSIA_COUNTRY_CODE}{digits[1:]}")
    return candidates


def _valid_e164(candidate: str) -> str | None:
    try:
        number = phonenumbers.parse(candidate, None)
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, PhoneNumberFormat.E164)


def normalize_phone(raw: str | None) -> str | None:
    """``"92 780-91-52"`` → ``"+992927809152"``; a number that cannot exist → ``None``."""
    if raw is None:
        return None
    text = str(raw)
    digits = _NON_DIGITS_RE.sub("", text)
    if not digits:
        return None
    international = _written_with_plus(text) or digits.startswith("00")
    if digits.startswith("00"):
        digits = digits[2:]
    candidates = [f"+{digits}"] if international else _tajik_candidates(digits)
    for candidate in candidates:
        normalized = _valid_e164(candidate)
        if normalized is not None:
            return normalized
    return None


def is_valid_phone(raw: str | None) -> bool:
    return normalize_phone(raw) is not None


def phone_digits(value: str | None) -> str:
    """Digits only (for search: ``"+992 90 123"`` → ``"99290123"``)."""
    return _NON_DIGITS_RE.sub("", value or "")
