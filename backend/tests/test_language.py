"""Russian / Tajik heuristic (05-ai.md §5, step 5)."""

import pytest

from app.ai.language import detect_language
from app.ai.text_normalize import contains_tajik_letters
from app.models.enums import Language

TAJIK_BY_LETTERS = [
    "Ҳа",
    "Салом, ман як торт мехоҳам",
    "Фардо соати ҳаждаҳ",
    "Суроға: кӯчаи Рӯдакӣ",
    "Ҳамааш дуруст",
]

TAJIK_BY_WORDS = [
    "Салом",
    "салом, чанд пул?",
    "Ман як торт мехохам",
    "Фардо лозим аст",
    "Нарх чанд?",
    "Шумо торт доред?",
    "Хуб, рахмат",
    "Торти асал лозим",
    "18:00 мумкин?",
]

RUSSIAN = [
    "Привет",
    "Здравствуйте, хочу заказать торт на завтра",
    "Сколько стоит медовик?",
    "Спасибо большое",
    "Ещё один торт, пожалуйста",
    "Доставка есть?",
    "Салом, сколько стоит торт?",  # a Tajik greeting inside a Russian message
]


@pytest.mark.parametrize("text", TAJIK_BY_WORDS)
def test_tajik_by_words(text: str) -> None:
    assert detect_language(text) == "tg"


@pytest.mark.parametrize("text", TAJIK_BY_LETTERS)
def test_tajik_specific_letters_are_decisive(text: str) -> None:
    # 05 §5: ӣ ӯ ҳ қ ғ ҷ → tg, whatever the customer profile or the LLM says.
    assert contains_tajik_letters(text) is True
    assert detect_language(text, llm_language="ru", customer_language="ru") == "tg"


@pytest.mark.parametrize("text", RUSSIAN)
def test_russian(text: str) -> None:
    assert detect_language(text) == "ru"


@pytest.mark.parametrize(
    ("text", "llm_language", "customer_language", "expected"),
    [
        ("ок", None, "ru", "ru"),
        ("ок", None, "tg", "tg"),
        ("ок", "tg", "ru", "tg"),  # the LLM hint wins over the customer profile
        ("ок", "ru", "tg", "ru"),
        ("", None, "tg", "tg"),
        ("   ", "tg", "ru", "tg"),
        (None, None, "ru", "ru"),
        ("18:00", None, "tg", "tg"),
        ("👍", "tg", "ru", "tg"),
    ],
)
def test_fallback_for_neutral_messages(
    text: str | None, llm_language: str | None, customer_language: str, expected: str
) -> None:
    assert detect_language(text, llm_language, customer_language) == expected


def test_invalid_hints_are_ignored() -> None:
    assert detect_language("ок", llm_language="en", customer_language="ru") == "ru"
    assert detect_language("ок", llm_language=None, customer_language="") == "ru"
    assert detect_language("ок", llm_language=42, customer_language=None) == "ru"


def test_accepts_and_matches_the_language_enum() -> None:
    assert detect_language("ок", llm_language=Language.TG) == Language.TG
    assert detect_language("Привет", customer_language=Language.TG) == Language.RU
    assert Language(detect_language("Салом")) is Language.TG


def test_word_scoring_beats_a_wrong_llm_hint() -> None:
    # A message that is clearly Russian is not re-labelled by a wrong hint (and vice versa).
    assert detect_language("Здравствуйте, сколько стоит торт?", llm_language="tg") == "ru"
    assert detect_language("Салом, ман як торт мехохам", llm_language="ru") == "tg"


CATALOG = ["Торт «Наполеон»", "наполеон", "Торт «Красный бархат»", "красный бархат", "медовик"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Наполеон ва Красный бархат", "tg"),
        ("Красный бархат и медовик", "tg"),  # only product names and "и": the dialog language stays
        ("Хочу красный бархат", "ru"),
    ],
)
def test_catalog_names_are_not_language_evidence(text: str, expected: str) -> None:
    assert detect_language(text, None, "tg", ignore_phrases=CATALOG) == expected


def test_weak_evidence_keeps_the_dialog_language() -> None:
    # "ы" in a product name is not enough to switch a Tajik customer to Russian (SWITCH_MARGIN).
    assert detect_language("Наполеон ва Красный бархат", None, "tg") == "tg"
    assert detect_language("Красный бархат", None, "tg") == "tg"
    assert detect_language("Красный бархат", None, "ru") == "ru"


@pytest.mark.parametrize(
    "text",
    [
        "ята заказ мекнам 19 сентябр да соати шашт ай самовывоз мегирм",  # colloquial, dropped vowels
        "ята навистм",
        "Салом алекм",
        "Хамин торти наполеон чан см?",
        "ин ай чи тайер меша?",
        "тортро фардо мегирам",
        "salom, tort mexoham",  # Latin transliteration
        # Khujand dialect (live dialog #8, 18.09.2026): "-даги", "манба", "якта", "числава", "-ми", "боша"
        "Салом алейкум, хамин 2 синнамон заказ мекадаги",
        "Манба якта фисташковый и якта ягодный",
        "19 числава соати 20:00",
        "доставка мешава ми?",
        "пагох мешадми?",
        "боша, нагз",
        "худам гирифта мебурдаги",
        "шоколад кати якта, ягодный кати якта",
    ],
)
def test_colloquial_and_latin_tajik(text: str) -> None:
    assert detect_language(text, None, "ru") == "tg"


@pytest.mark.parametrize("text", ["самовывоз 927809152", "доставка", "заказ"])
def test_business_loanwords_do_not_switch_a_tajik_customer(text: str) -> None:
    assert detect_language(text, None, "tg") == "tg"
    assert detect_language(text, None, "ru") == "ru"


def test_strict_mode_scores_tajik_letters_instead_of_deciding() -> None:
    mixed = "Торт «Наполеон» (Слоёные коржи с заварным кремом, 1,5 кг) стоит 200 сомонӣ за шт."
    assert detect_language(mixed, None, "tg") == "tg"  # a customer message: the letter decides
    assert detect_language(mixed, None, "tg", decisive_letters=False) == "ru"  # a generated reply: mixed
    assert detect_language("Торти «Наполеон» 200 сомонӣ арзиш дорад", None, "ru", decisive_letters=False) == "tg"


def test_russian_words_that_look_like_tajik_verbs() -> None:
    assert detect_language("торт с медовиком и менеджером", None, "tg") == "ru"
    # "мера", "Кати", "местами" are Russian words, not the Khujand "мера"/"кати" forms
    assert detect_language("это для Кати, мера обычная, местами", None, "tg") == "ru"
