"""Receipt reading and checking (05-ai.md §9, 03-business-rules.md §3): the pure functions, no model."""

from decimal import Decimal

import pytest

from app.ai.receipt import (
    ReceiptReading,
    check_receipt,
    expected_prepayment,
    is_card_number,
    mentions_payment_done,
    parse_reading,
    receipt_json_schema,
    receipt_note,
    wallet_matches,
)
from tests.bot_fakes import receipt_reading

WALLET = "+992 92 757 53 33"
CARD = "5058 2703 8115 6297"
EXPECTED = Decimal("300.00")


@pytest.mark.parametrize(
    ("recipient", "expected"),
    [
        ("+992 92 757 53 33", True),
        ("992927575333", True),
        ("92 757 53 33", True),
        ("927575333", True),
        ("+992 92 *** 53 33", True),  # apps mask the middle digits
        ("92 *** 53 33", True),
        ("+992 93 757 53 33", False),  # another number
        ("**** 1234", False),  # a card, not the wallet
        ("5058 27** **** 1234", False),
        ("Фаррух М.", None),  # a name only: nothing to compare
        ("*** 33", None),  # too few visible digits
        (None, None),
        ("", None),
    ],
)
def test_wallet_matches(recipient: str | None, expected: bool | None) -> None:
    assert wallet_matches(recipient, WALLET) is expected


def test_wallet_matches_needs_a_configured_wallet() -> None:
    assert wallet_matches("927575333", "") is None


@pytest.mark.parametrize(
    ("recipient", "expected"),
    [
        ("5058 2703 8115 6297", True),
        ("5058270381156297", True),
        ("5058 27** **** 6297", True),  # the app masks the middle digits
        ("**** 6297", True),  # only the last four are printed
        ("5058 2703 8115 1234", False),  # another card
        ("+992 92 757 53 33", False),  # a wallet, not our card
        ("Фаррух М.", None),
    ],
)
def test_wallet_matches_when_the_account_is_a_card(recipient: str, expected: bool | None) -> None:
    """The owner may put a card number in the settings; then a long recipient is compared, not rejected."""
    assert wallet_matches(recipient, CARD) is expected


def test_is_card_number() -> None:
    assert is_card_number(CARD) and is_card_number("5058270381156297")
    assert not is_card_number(WALLET) and not is_card_number("992927575333")
    assert not is_card_number("") and not is_card_number(None)


def test_expected_prepayment() -> None:
    assert expected_prepayment("300.00", "0", 100) == Decimal("300.00")
    assert expected_prepayment("300.00", "0", 50) == Decimal("150.00")
    assert expected_prepayment("333.33", "0", 50) == Decimal("166.67")  # 166.665 → half up
    assert expected_prepayment("300.00", "200.00", 100) == Decimal("100.00")
    assert expected_prepayment("300.00", "400.00", 100) == Decimal("0.00")


def test_parse_reading_sanitizes_the_model_answer() -> None:
    reading = parse_reading(
        {"is_receipt": True, "status": "Success", "amount": "12 000,50", "currency": " TJS ", "confidence": 1.7}
    )
    assert reading.amount == Decimal("12000.50") and reading.status == "success" and reading.confidence == 1.0
    assert reading.currency == "TJS"
    odd = parse_reading({"amount": "-5", "status": "weird", "confidence": "high"})
    assert odd.amount is None and odd.status == "unknown" and odd.confidence == 0.0
    assert parse_reading("garbage").is_receipt is False


def test_check_receipt_ok() -> None:
    verdict = check_receipt(parse_reading(receipt_reading()), EXPECTED, WALLET)
    assert verdict.ok and verdict.problems == [] and verdict.wallet_ok is True
    assert verdict.amount == Decimal("300.00") and verdict.shortfall is None
    assert verdict.as_dict() == {
        "ok": True,
        "problems": [],
        "amount": "300.00",
        "expected": "300.00",
        "shortfall": None,
        "wallet_ok": True,
    }


@pytest.mark.parametrize(
    ("overrides", "problems"),
    [
        ({"is_receipt": False}, ["not_receipt"]),
        ({"confidence": 0.3}, ["not_receipt"]),
        ({"status": "failed"}, ["status"]),
        ({"status": "pending"}, ["status"]),
        ({"currency": "RUB"}, ["currency"]),
        ({"recipient": "+992 93 111 22 33"}, ["wallet_mismatch"]),
        ({"amount": None}, ["amount_unknown"]),
        ({"amount": 250}, ["amount_short"]),
        ({"amount": 250, "status": "failed"}, ["status", "amount_short"]),
    ],
)
def test_check_receipt_problems(overrides: dict, problems: list[str]) -> None:
    verdict = check_receipt(parse_reading(receipt_reading(**overrides)), EXPECTED, WALLET)
    assert not verdict.ok and verdict.problems == problems


def test_shortfall_and_an_unreadable_recipient() -> None:
    short = check_receipt(parse_reading(receipt_reading(amount=250, recipient="Фаррух М.")), EXPECTED, WALLET)
    assert short.shortfall == Decimal("50.00") and short.wallet_ok is None
    # an unreadable recipient alone does not block: the operator checks the bank app anyway
    assert check_receipt(parse_reading(receipt_reading(recipient=None)), EXPECTED, WALLET).ok


@pytest.mark.parametrize("currency", ["сомони", "сомонӣ", "смн", "с.", "TJS", "somoni", None])
def test_somoni_spellings_pass(currency: str | None) -> None:
    assert check_receipt(parse_reading(receipt_reading(currency=currency)), EXPECTED, WALLET).ok


def test_receipt_note_for_the_operator() -> None:
    reading = parse_reading(receipt_reading())
    note = receipt_note(reading, check_receipt(reading, EXPECTED, WALLET), auto_paid=False)
    assert note == (
        "Чек из Instagram: 300.00 сомони, Alif, получатель совпадает, 17.09.2026 12:31. "
        "Сверьте поступление в приложении банка и отметьте оплату."
    )
    short = parse_reading(receipt_reading(amount=250, recipient="+992 93 111 22 33"))
    note = receipt_note(short, check_receipt(short, EXPECTED, WALLET), auto_paid=False)
    assert "получатель НЕ совпадает: +992 93 111 22 33" in note and "не хватает 50.00 сомони" in note
    auto = receipt_note(reading, check_receipt(reading, EXPECTED, WALLET), auto_paid=True)
    assert auto.endswith("Оплата отмечена автоматически — сверьте поступление в приложении банка.")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("оплатил", True),
        ("Перевёл, проверьте", True),
        ("Я оплатила только что", True),
        ("пардохт кардам", True),
        ("скинул деньги", True),
        ("хочу оплатить картой", False),
        ("перевести на другой адрес", False),
        ("", False),
        (None, False),
    ],
)
def test_mentions_payment_done(text: str | None, expected: bool) -> None:
    assert mentions_payment_done(text) is expected


def test_schema_matches_the_reading_model() -> None:
    schema = receipt_json_schema()
    assert list(schema["properties"]) == list(ReceiptReading.model_fields)
    assert schema["required"] == list(schema["properties"]) and schema["additionalProperties"] is False
