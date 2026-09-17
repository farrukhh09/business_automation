"""Deterministic confirmation classifier (03-business-rules.md §5, SPEC §12-§13).

The table below is the contract: every phrase SPEC §12 ("Да", "Подтверждаю", "Всё верно") and
SPEC §13 ("ну вроде", "наверное", "может быть", "посмотрим", "думаю да") names is present,
together with the Tajik wording and the substring traps ("надо", "дату", "неверно").
"""

import pytest

from app.ai.confirmation import ConfirmationDecision, classify_confirmation

YES = ConfirmationDecision.YES
NO = ConfirmationDecision.NO
UNCERTAIN = ConfirmationDecision.UNCERTAIN
CHANGE = ConfirmationDecision.CHANGE

CASES: list[tuple[str | None, ConfirmationDecision]] = [
    # --- YES: short, explicit agreement (03 §5, SPEC §12) -------------------------------
    ("Да", YES),
    ("да", YES),
    ("ДА", YES),
    ("Да!", YES),
    ("Да.", YES),
    ("да-да", YES),
    ("Да ✅", YES),
    ("Да, всё верно", YES),
    ("да, все верно", YES),
    ("Всё верно", YES),
    ("все верно", YES),
    ("верно", YES),
    ("Да верно", YES),
    ("Подтверждаю", YES),
    ("подтверждаю 👍", YES),
    ("Подтверждаю заказ", YES),
    ("Да, подтверждаю", YES),
    ("Всё правильно", YES),
    ("правильно", YES),
    ("Согласен", YES),
    ("Согласна", YES),
    ("да, спасибо", YES),
    ("Да, все верно, спасибо", YES),
    # TG (03 §5)
    ("Ҳа", YES),
    ("ҳа", YES),
    ("ха", YES),
    ("Бале", YES),
    ("Ҳа, дуруст", YES),
    ("дуруст", YES),
    ("Ҳамааш дуруст", YES),
    ("хама дуруст", YES),
    ("Тасдиқ мекунам", YES),
    ("тасдик мекунам", YES),
    # --- UNCERTAIN: doubt, weak acknowledgements, empty (03 §5, SPEC §13) ---------------
    ("", UNCERTAIN),
    ("   ", UNCERTAIN),
    (None, UNCERTAIN),
    ("👍", UNCERTAIN),
    ("✅", UNCERTAIN),
    ("🙂🙂", UNCERTAIN),
    ("ок", UNCERTAIN),
    ("Ок", UNCERTAIN),
    ("ok", UNCERTAIN),
    ("окей", UNCERTAIN),
    ("ага", UNCERTAIN),
    ("угу", UNCERTAIN),
    ("хорошо", UNCERTAIN),
    ("ладно", UNCERTAIN),
    ("спасибо", UNCERTAIN),
    ("ну вроде", UNCERTAIN),
    ("наверное", UNCERTAIN),
    ("наверно", UNCERTAIN),
    ("может быть", UNCERTAIN),
    ("посмотрим", UNCERTAIN),
    ("думаю да", UNCERTAIN),
    ("ну да", UNCERTAIN),
    ("да наверное", UNCERTAIN),
    ("вроде да", UNCERTAIN),
    ("кажется да", UNCERTAIN),
    ("скорее всего", UNCERTAIN),
    ("возможно", UNCERTAIN),
    ("не знаю", UNCERTAIN),
    ("не уверен", UNCERTAIN),
    ("Здравствуйте", UNCERTAIN),
    # 03 §5: agreement must be short — a long message is re-checked instead of confirmed.
    ("да конечно я все внимательно проверил и все сходится", UNCERTAIN),
    # TG doubt markers
    ("шояд", UNCERTAIN),
    ("Эҳтимол", UNCERTAIN),
    ("фикр мекунам", UNCERTAIN),
    ("намедонам", UNCERTAIN),
    ("мебинем", UNCERTAIN),
    # --- NO: refusal without anything to extract (03 §5) --------------------------------
    ("нет", NO),
    ("Нет!", NO),
    ("НЕТ", NO),
    ("не", NO),
    ("неверно", NO),
    ("не верно", NO),
    ("неправильно", NO),
    ("не так", NO),
    ("не надо", NO),
    ("нет, не надо", NO),
    ("не то", NO),
    ("отмена", NO),
    ("отмените", NO),
    ("отменить заказ", NO),
    ("да, отмените", NO),  # cancellation never confirms an order (module docstring)
    ("поменяйте", NO),
    ("измените", NO),
    # TG
    ("нест", NO),
    ("хато", NO),
    ("не, хато", NO),
    ("не дуруст", NO),
    ("бекор кунед", NO),
    # --- CHANGE: agreement/refusal plus substantive details (03 §5) ---------------------
    ("да, но время 19:00", CHANGE),
    ("Да, только адрес другой", CHANGE),
    ("да, но добавьте ещё медовик", CHANGE),
    ("Да, давайте на 18:00", CHANGE),
    ("да, 2 торта", CHANGE),
    ("нет, время другое", CHANGE),
    ("нет, лучше завтра", CHANGE),
    ("поменяйте время на 19:00", CHANGE),
    ("измените адрес", CHANGE),
    ("может быть в 19:00", CHANGE),
    ("время 19:00", CHANGE),
    ("Фардо", CHANGE),
    ("вақт 19:00", CHANGE),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_classify_confirmation(text: str | None, expected: ConfirmationDecision) -> None:
    assert classify_confirmation(text) is expected


def test_table_covers_every_decision() -> None:
    assert len(CASES) >= 60
    assert {expected for _, expected in CASES} == set(ConfirmationDecision)


@pytest.mark.parametrize(
    "text",
    [
        "надо подумать",  # "да" must not match inside "надо"
        "дату поменять не могу решить",
        "когда будет готов",
        "мне надо будет уточнить",
    ],
)
def test_da_is_never_matched_inside_another_word(text: str) -> None:
    assert classify_confirmation(text) is not ConfirmationDecision.YES


@pytest.mark.parametrize(("text", "expected"), [("неверно", NO), ("не верно", NO), ("нет проблем", NO)])
def test_negation_is_whole_word(text: str, expected: ConfirmationDecision) -> None:
    assert classify_confirmation(text) is expected


@pytest.mark.parametrize("text", ["Да", "ДА", "  да  ", "Да!!!", "да…", "«да»"])
def test_yes_survives_case_spacing_and_punctuation(text: str) -> None:
    assert classify_confirmation(text) is ConfirmationDecision.YES


def test_spec_13_phrases_never_confirm() -> None:
    """SPEC §13: «ну вроде», «наверное», «может быть», «посмотрим», «думаю да»."""
    for text in ("ну вроде", "наверное", "может быть", "посмотрим", "думаю да"):
        assert classify_confirmation(text) is ConfirmationDecision.UNCERTAIN


def test_decision_is_a_str_enum() -> None:
    assert ConfirmationDecision.YES == "YES"
    assert classify_confirmation("Да").value == "YES"
