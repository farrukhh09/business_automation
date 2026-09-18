"""Receipt reading and checking (05-ai.md §9, 03-business-rules.md §3): the pure functions, no model."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.ai.receipt import (
    EarlierReceipt,
    ReceiptInspection,
    ReceiptReading,
    check_receipt,
    credited_amount,
    expected_prepayment,
    inspection_json_schema,
    is_card_number,
    mentions_payment_done,
    normalize_reference,
    paid_at_bounds,
    parse_inspection,
    parse_reading,
    receipt_json_schema,
    receipt_note,
    transfer_fingerprint,
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
        "alerts": [],
        "amount": "300.00",
        "expected": "300.00",
        "shortfall": None,
        "wallet_ok": True,
        "earlier_order_id": None,
        "earlier_match": None,
        "edited_where": None,
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
        "Чек из Instagram: 300.00 сомони, Alif, получатель совпадает, 17.09.2026 12:31, операция A1. "
        "Сверьте поступление в приложении банка и отметьте оплату."
    )
    short = parse_reading(receipt_reading(amount=250, recipient="+992 93 111 22 33"))
    note = receipt_note(short, check_receipt(short, EXPECTED, WALLET), auto_paid=False)
    assert "получатель НЕ совпадает: +992 93 111 22 33" in note and "не хватает 50.00 сомони" in note
    auto = receipt_note(reading, check_receipt(reading, EXPECTED, WALLET), auto_paid=True)
    assert auto.endswith("Оплата отмечена автоматически — сверьте поступление в приложении банка.")


# --------------------------------------------------------------------------- authenticity (03 §3)

NOW = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)  # 15:00 in Dushanbe
ORDERED_AT = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)  # 13:00 in Dushanbe


def test_parse_reading_takes_the_iso_date() -> None:
    assert parse_reading(receipt_reading(paid_at_iso=" 2026-09-18T14:05 ")).paid_at_iso == "2026-09-18T14:05"
    assert "looks_edited" not in receipt_json_schema()["properties"]  # a separate call, see inspect_receipt


def test_parse_inspection() -> None:
    edited = parse_inspection({"lines": ["a: serif", "", 5], "mismatch": True, "where": " 1500,00  TJS "})
    assert edited.mismatch and edited.where == "1500,00 TJS" and edited.lines == ["a: serif", "5"]
    clean = parse_inspection({"lines": [], "mismatch": False, "where": "ignored when there is no mismatch"})
    assert not clean.mismatch and clean.where is None
    assert parse_inspection({"mismatch": "yes"}).mismatch is False  # only a real true counts
    assert parse_inspection(None) == ReceiptInspection()
    assert set(inspection_json_schema()["required"]) == {"lines", "mismatch", "where"}


def test_normalize_reference() -> None:
    assert normalize_reference("AF-2026-0918-884512") == "AF20260918884512"
    assert normalize_reference(" № 12 34 56 ") == "123456"
    assert normalize_reference("A1") is None  # too short to identify a transfer
    assert normalize_reference(None) is None


def test_paid_at_bounds_are_business_local() -> None:
    earliest, latest, precise = paid_at_bounds("2026-09-18T14:05")
    assert precise and earliest == datetime(2026, 9, 18, 9, 5, tzinfo=UTC)  # Dushanbe is UTC+5
    assert latest - earliest == timedelta(seconds=59)
    day_start, day_end, day_precise = paid_at_bounds("2026-09-18")
    assert not day_precise and day_start == datetime(2026, 9, 17, 19, 0, tzinfo=UTC)
    assert day_end == datetime(2026, 9, 18, 18, 59, 59, tzinfo=UTC)
    assert paid_at_bounds("18.09.2026") is None  # not the ISO the model is asked for
    assert paid_at_bounds("2026-02-30T10:00") is None
    assert paid_at_bounds(None) is None


def test_transfer_fingerprint_needs_amount_minute_and_sender() -> None:
    full = parse_reading(receipt_reading(paid_at_iso="2026-09-18T14:05", sender="+992 93 *** 11 22"))
    same = parse_reading(receipt_reading(paid_at_iso="2026-09-18T14:05", sender="992 93 ** 11 22", provider="DC"))
    assert transfer_fingerprint(full) is not None and transfer_fingerprint(full) != transfer_fingerprint(same)
    again = parse_reading(receipt_reading(paid_at_iso="2026-09-18 14:05", sender="+992 93 *** 11 22", reference="x"))
    assert transfer_fingerprint(again) == transfer_fingerprint(full)
    assert transfer_fingerprint(parse_reading(receipt_reading(paid_at_iso="2026-09-18T14:05"))) is None  # no sender
    no_time = receipt_reading(paid_at_iso="2026-09-18", sender="+992 93 *** 11 22")
    assert transfer_fingerprint(parse_reading(no_time)) is None


def test_credited_amount_counts_clean_receipts_and_payments() -> None:
    earlier = [(Decimal("500"), ["amount_short"]), (Decimal("100"), ["looks_edited"]), (None, [])]
    assert credited_amount("0", earlier) == Decimal("500.00")  # a doubtful receipt does not count
    assert credited_amount("600", earlier) == Decimal("600.00")  # the operator registered more
    assert credited_amount("0", []) == Decimal("0.00")


@pytest.mark.parametrize(
    ("earlier", "problem"),
    [
        (EarlierReceipt(order_id=7, same_order=False, match="file"), "duplicate"),
        (EarlierReceipt(order_id=7, same_order=True, match="reference"), "resent"),
    ],
)
def test_a_receipt_sent_before_is_a_problem(earlier: EarlierReceipt, problem: str) -> None:
    verdict = check_receipt(parse_reading(receipt_reading()), EXPECTED, WALLET, earlier=earlier)
    assert not verdict.ok and verdict.problems == [problem] and verdict.earlier == earlier


@pytest.mark.parametrize(
    ("overrides", "alerts"),
    [
        ({"paid_at_iso": "2026-09-18T15:30"}, ["date_future"]),  # 10:30 UTC, half an hour ahead
        ({"paid_at_iso": "2026-09-18T15:10"}, []),  # within the clock skew
        ({"paid_at_iso": "2026-09-18T11:30"}, ["date_before_order"]),  # 06:30 UTC, the order came at 08:00
        ({"paid_at_iso": "2026-09-18T12:30"}, []),  # half an hour early is tolerated
        ({"paid_at_iso": "2026-09-18"}, []),  # a date alone covers the whole day
        ({"paid_at_iso": "2026-09-17"}, ["date_before_order"]),
    ],
)
def test_doubtful_dates_are_operator_alerts_not_customer_problems(overrides: dict, alerts: list[str]) -> None:
    verdict = check_receipt(
        parse_reading(receipt_reading(**overrides)), EXPECTED, WALLET, ordered_at=ORDERED_AT, now=NOW
    )
    assert verdict.alerts == alerts and verdict.problems == []
    assert verdict.ok is (not alerts)  # any doubt blocks the automatic "paid" mark
    assert verdict.codes == alerts


def test_traces_of_editing_are_an_alert_too() -> None:
    reading = parse_reading(receipt_reading())
    edited = check_receipt(reading, EXPECTED, WALLET, inspection=ReceiptInspection(mismatch=True, where="300,00 TJS"))
    assert edited.alerts == ["looks_edited"] and edited.problems == [] and not edited.ok
    assert edited.edited_where == "300,00 TJS" and edited.as_dict()["edited_where"] == "300,00 TJS"
    clean = check_receipt(reading, EXPECTED, WALLET, inspection=ReceiptInspection())
    assert clean.ok and clean.alerts == [] and clean.edited_where is None


def test_receipt_note_names_every_doubt() -> None:
    reading = parse_reading(receipt_reading(paid_at="17.09.2026 12:31", paid_at_iso="2026-09-17T12:31"))
    earlier = EarlierReceipt(order_id=3, same_order=False, match="reference")
    verdict = check_receipt(
        reading,
        EXPECTED,
        WALLET,
        earlier=earlier,
        inspection=ReceiptInspection(mismatch=True, where="300,00 TJS"),
        ordered_at=ORDERED_AT,
        now=NOW,
    )
    note = receipt_note(reading, verdict, auto_paid=False)
    assert "ВНИМАНИЕ: этот чек уже присылали к заказу №3 (тот же номер операции)" in note
    assert "перевод сделан раньше, чем оформлен заказ (17.09.2026 12:31)" in note
    assert "признаки правки (строка «300,00 TJS» нарисована не так, как остальные)." in note
    resent = check_receipt(
        parse_reading(receipt_reading()), EXPECTED, WALLET, earlier=EarlierReceipt(3, same_order=True, match="file")
    )
    resent_note = receipt_note(parse_reading(receipt_reading()), resent, auto_paid=False)
    assert "клиент прислал этот чек повторно" in resent_note and "ВНИМАНИЕ" not in resent_note


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
