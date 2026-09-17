"""ResponseGuard: anti-hallucination check for LLM replies (05-ai.md §6, SPEC §40)."""

from decimal import Decimal

import pytest

from app.ai.guard import (
    INSTAGRAM_TEXT_LIMIT_BYTES,
    GuardResult,
    ResponseGuard,
    exceeds_message_limit,
    utf8_len,
)

ALLOWED = {Decimal("1500.00"), Decimal("450")}


@pytest.fixture
def guard() -> ResponseGuard:
    return ResponseGuard()


def check(guard: ResponseGuard, text: str, kind: str = "FAQ_ANSWER", **kwargs: object) -> GuardResult:
    params: dict[str, object] = {"allowed_amounts": ALLOWED, "allowed_product_names": set(), "kind": kind}
    params.update(kwargs)
    return guard.check(text, **params)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "Итого: 1 500 сомони.",
        "Итого: 1500 сомони",
        "Итого: 1500.00 сомони",
        "Это будет 1,5 тыс сомони",
        "Медовик стоит 450 смн",
        "Цена 450 сом",
        "Нархаш 450 сомонӣ",
        "Всего TJS 1500",
        "Narx 1500 somoni",
        "1 500 сомони и 450 сомони",
        "Доставка завтра в 18:00, заказ №42",  # numbers without a currency are not prices
        "Позвоните по номеру 900123456",
        "",
        "   ",
    ],
)
def test_allowed_or_irrelevant_numbers(guard: ResponseGuard, text: str) -> None:
    result = check(guard, text)
    assert result.ok, result.violations
    assert bool(result) is True


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("Итого: 2500 сомони", "2500.00"),
        ("Итого: 2 500 сомони", "2500.00"),
        ("Медовик стоит 500 сомони", "500.00"),
        ("Это 2,5 тыс сомони", "2500.00"),
        ("Всего 1499.99 сомони", "1499.99"),
        ("Нархаш 300 сомонӣ", "300.00"),
        ("Доставка 20 смн", "20.00"),
    ],
)
def test_invented_amount_is_rejected(guard: ResponseGuard, text: str, amount: str) -> None:
    result = check(guard, text)
    assert not result.ok
    assert f"amount_not_allowed:{amount}" in result.violations


def test_non_breaking_thousand_separator(guard: ResponseGuard) -> None:
    nbsp, narrow = chr(0x00A0), chr(0x202F)  # thousand separators produced by formatters
    assert check(guard, f"Итого: 1{nbsp}500 сомони").ok
    assert check(guard, f"Итого: 1{narrow}500 сомони").ok
    assert check(guard, f"Итого: 2{nbsp}500 сомони").violations == ["amount_not_allowed:2500.00"]


def test_empty_allowed_amounts_rejects_any_price(guard: ResponseGuard) -> None:
    result = check(guard, "Торт стоит 1500 сомони", allowed_amounts=set())
    assert result.violations == ["amount_not_allowed:1500.00"]


@pytest.mark.parametrize(
    "text",
    [
        "Ваш заказ подтверждён!",
        "заказ подтвержден",
        "Мы подтвердили ваш заказ",
        "Подтверждаем ваш заказ",
        "Заказ принят",
        "Ваш заказ уже оформлен",
        "Фармоиши шумо тасдиқ шуд",
        "тасдик шуд",
    ],
)
def test_confirmation_claims_need_the_confirmed_kind(guard: ResponseGuard, text: str) -> None:
    rejected = check(guard, text, kind="FAQ_ANSWER")
    assert not rejected.ok
    assert any(violation.startswith("confirmation_claim:") for violation in rejected.violations)

    allowed = check(guard, text, kind="ORDER_CONFIRMED")
    assert allowed.ok, allowed.violations


def test_confirmation_kind_is_case_insensitive(guard: ResponseGuard) -> None:
    assert check(guard, "Заказ принят", kind="order_confirmed").ok


def test_confirmation_like_wording_without_a_claim_is_fine(guard: ResponseGuard) -> None:
    # The summary question itself must pass: it asks for a confirmation, it does not state one.
    assert check(guard, "Всё верно? Напишите «Да», чтобы подтвердить.").ok
    assert check(guard, "Подтвердите, пожалуйста, заказ").ok


@pytest.mark.parametrize(
    ("text", "phrase"),
    [
        ("Медовик есть в наличии", "в наличии"),
        ("Всё в наличие, приходите", "в наличие"),
        ("Дадим скидку 10%", "скидк"),
        ("Доставка бесплатная", "бесплатн"),
        ("Гарантирую свежесть", "гарантиру"),
    ],
)
def test_forbidden_phrases(guard: ResponseGuard, text: str, phrase: str) -> None:
    result = check(guard, text)
    assert f"forbidden_phrase:{phrase}" in result.violations


def test_forbidden_phrase_can_be_allowed_explicitly(guard: ResponseGuard) -> None:
    assert check(guard, "Медовик есть в наличии", allowed_phrases={"в наличии"}).ok
    assert check(guard, "Доставка бесплатная от 450 сомони", allowed_phrases={"бесплатная доставка"}).ok


def test_forbidden_list_is_configurable() -> None:
    guard = ResponseGuard(forbidden_phrases=())
    assert check(guard, "Медовик есть в наличии").ok


def test_catalog_products_outside_the_facts_are_reported() -> None:
    guard = ResponseGuard(catalog_names=["Медовик", "Красный бархат"])
    result = check(guard, "У нас есть Медовик", allowed_product_names={"Красный бархат"})
    assert result.violations == ["product_not_allowed:Медовик"]

    allowed = check(guard, "Красный бархат — отличный выбор", allowed_product_names={"Красный бархат"})
    assert allowed.ok, allowed.violations


def test_products_are_not_checked_without_a_catalog(guard: ResponseGuard) -> None:
    assert check(guard, "У нас есть Пражский торт", allowed_product_names={"Медовик"}).ok


def test_several_violations_are_reported_together(guard: ResponseGuard) -> None:
    result = check(guard, "Ваш заказ подтверждён, итого 2500 сомони, медовик в наличии")
    assert not result.ok
    assert len(result.violations) >= 3


def test_length_is_not_a_violation(guard: ResponseGuard) -> None:
    # 06 §1: splitting a long reply is the Instagram client's job, not a guard failure.
    text = "Медовик очень вкусный. " * 60
    assert utf8_len(text) > INSTAGRAM_TEXT_LIMIT_BYTES
    assert check(guard, text).ok
    assert exceeds_message_limit(text) is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0), (None, 0), ("abc", 3), ("да", 4), ("Привет", 12), ("🙂", 4)],
)
def test_utf8_len(text: str | None, expected: int) -> None:
    assert utf8_len(text) == expected


def test_message_limit_boundary() -> None:
    assert exceeds_message_limit("a" * INSTAGRAM_TEXT_LIMIT_BYTES) is False
    assert exceeds_message_limit("a" * (INSTAGRAM_TEXT_LIMIT_BYTES + 1)) is True


def test_guard_result_shape() -> None:
    ok = GuardResult.from_violations([])
    assert ok.ok is True
    assert ok.violations == []
    assert bool(ok) is True

    failed = GuardResult.from_violations(["amount_not_allowed:1.00"])
    assert failed.ok is False
    assert bool(failed) is False


def test_violations_never_contain_the_reply_text(guard: ResponseGuard, caplog: pytest.LogCaptureFixture) -> None:
    secret = "Адрес клиента: улица Рудаки 15, квартира 3"
    with caplog.at_level("WARNING"):
        result = check(guard, f"{secret}. Итого 9999 сомони")
    assert not result.ok
    assert all("Рудаки" not in violation for violation in result.violations)
    assert "Рудаки" not in caplog.text
