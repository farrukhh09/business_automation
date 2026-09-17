"""Deterministic operator-request detector (03-business-rules.md §6, SPEC §30).

A false positive silently switches a paying customer to ``HUMAN_HANDOFF``, so the negative
table (head counts, "мой менеджер", negated needs) matters as much as the positive one.
"""

import pytest

from app.ai.handoff import detect_operator_request

REQUESTS = [
    # bare role words (03 §6)
    "оператор",
    "Оператор!",
    "менеджер",
    "Менеджер?",
    "администратор",
    "админ",
    "оператора позовите",
    # explicit asks (SPEC §30)
    "Позовите оператора",
    "позвать оператора",
    "Мне нужен оператор",
    "Мне нужен менеджер",
    "нужен администратор",
    "Хочу поговорить с человеком",
    "хочу поговорить с оператором",
    "Соедините с оператором",
    "соедините с менеджером",
    "Свяжите меня с оператором",
    "Переключите на менеджера",
    "дайте оператора",
    "передайте менеджеру",
    "можно оператора?",
    "Мне нужна помощь оператора",
    "позовите вашего менеджера",  # a request verb beats the possessive rule
    "оператор пожалуйста",
    # "человек" only inside a request
    "живой человек",
    "мне нужен живой человек",
    "Я хочу поговорить с живым человеком",
    "с человеком",
    "позовите человека",
    "позвать человека",
    "пусть ответит человек",
    # Tajik (03 §6)
    "оператор лозим",
    "менеҷер лозим",
    "менеҷер",
    "оператор даъват кунед",
    "бо одам гап мезанам",
    "одами зинда",
]

NOT_REQUESTS = [
    "",
    "   ",
    None,
    # head counts must never trigger a handoff
    "торт на 10 человек",
    "для 5 человек",
    "человек 20 будет",
    "хочу торт на 10 человек",
    "нужен торт для 12 человек",
    "торт на 30 человек с доставкой",
    "заказ для человека",
    "барои 10 одам торт",
    # talking about somebody, not asking for one
    "мой менеджер заказывал в прошлый раз",
    "ваш менеджер вчера обещал скидку",
    "я не оператор",
    # negated needs
    "не надо оператора",
    "не нужен менеджер",
    # ordinary messages
    "Здравствуйте, сколько стоит медовик?",
    "Медовик и наполеон, пожалуйста",
    "Доставка завтра в 18:00",
    "спасибо, вы очень хороший человек",
    "я человек занятой",
    "это человек?",
    "человеческий фактор",
    "Салом, як торт лозим",
]


@pytest.mark.parametrize("text", REQUESTS)
def test_operator_request_detected(text: str) -> None:
    assert detect_operator_request(text) is True


@pytest.mark.parametrize("text", NOT_REQUESTS)
def test_not_an_operator_request(text: str | None) -> None:
    assert detect_operator_request(text) is False


def test_table_size() -> None:
    assert len(REQUESTS) + len(NOT_REQUESTS) >= 40


def test_spec_30_examples() -> None:
    """SPEC §30: «Позовите оператора», «Хочу поговорить с человеком», «Мне нужен менеджер»."""
    for text in ("Позовите оператора", "Хочу поговорить с человеком", "Мне нужен менеджер"):
        assert detect_operator_request(text) is True


def test_quantity_context_wins_over_request_verb() -> None:
    """The documented trap: a request verb in the sentence must not make a head count a handoff."""
    assert detect_operator_request("Хочу заказать торт на 10 человек") is False
    assert detect_operator_request("Нужен торт для 8 человек на завтра") is False
    assert detect_operator_request("хочу заказать торт человек на 15") is False
