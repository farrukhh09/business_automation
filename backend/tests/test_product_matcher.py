"""Catalog matching without the LLM (05-ai.md §1, §5 step 7.2; SPEC §11)."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.ai.product_matcher import (
    MatchResult,
    MatchStatus,
    ProductMatcher,
    is_generic_mention,
    split_item_mentions,
)

CATALOG: list[dict[str, Any]] = [
    {"id": 1, "name": "Медовик", "aliases": ["торт медовик", "асал"], "is_active": True},
    {"id": 2, "name": "Красный бархат", "aliases": ["бархат", "торти сурх"], "is_active": True},
    {"id": 3, "name": "Наполеон", "aliases": [], "is_active": True},
    {"id": 4, "name": "Чизкейк Нью-Йорк", "aliases": ["чизкейк"], "is_active": True},
    {"id": 5, "name": "Эклер", "aliases": ["пирожное эклер"], "is_active": False},
]


@dataclass
class FakeProduct:
    """Stands in for the ORM ``Product`` (id / name / aliases / is_active)."""

    id: int
    name: str
    aliases: list[str] = field(default_factory=list)
    is_active: bool = True
    deleted_at: Any = None
    unit: str = "шт."


@pytest.fixture
def matcher() -> ProductMatcher:
    return ProductMatcher(CATALOG)


@pytest.mark.parametrize(
    ("text", "expected_id"),
    [
        ("Медовик", 1),
        ("медовик", 1),
        ("Медовик!", 1),
        ("  МЕДОВИК  ", 1),
        ("торт медовик", 1),
        ("асал", 1),  # Tajik alias
        ("Красный бархат", 2),
        ("бархат", 2),
        ("торти сурх", 2),  # Tajik alias
        ("Наполеон", 3),
        ("чизкейк", 4),
        ("Чизкейк Нью-Йорк", 4),
    ],
)
def test_exact_name_and_alias(matcher: ProductMatcher, text: str, expected_id: int) -> None:
    result = matcher.match(text)
    assert result.status is MatchStatus.EXACT
    assert result.candidates == [expected_id]
    assert result.product_id == expected_id


@pytest.mark.parametrize(
    ("text", "expected_id"),
    [
        ("медовек", 1),  # typo
        ("мидовик", 1),
        ("красный бархот", 2),  # typo
        ("Красный Бархат, пожалуйста", 2),
        ("красный", 2),  # part of the name
        ("хочу медовик на завтра", 1),
        ("наполеончик", 3),
        ("чизкек", 4),
    ],
)
def test_single_fuzzy_match(matcher: ProductMatcher, text: str, expected_id: int) -> None:
    result = matcher.match(text)
    assert result.status is MatchStatus.SINGLE, result
    assert result.candidates == [expected_id]


@pytest.mark.parametrize(
    "text",
    ["торт", "торты", "2 торта", "Хочу 2 торта", "торти", "2 торта на завтра", "хочу 2 торта на завтра"],
)
def test_generic_category_word_returns_every_cake(matcher: ProductMatcher, text: str) -> None:
    # SPEC §11: "хочу 2 торта" → the bot asks which ones exactly.
    result = matcher.match(text)
    assert result.status is MatchStatus.MULTIPLE
    assert result.candidates == [1, 2]


@pytest.mark.parametrize("text", ["десерт", "десерты", "пирожное"])
def test_generic_category_without_products(matcher: ProductMatcher, text: str) -> None:
    # "пирожное" only exists on the inactive Эклер, so nothing is offered.
    assert matcher.match(text) == MatchResult(MatchStatus.NONE, [])


@pytest.mark.parametrize("text", ["эклер", "пицца", "круассан", "", "   ", None, "ок"])
def test_no_match(matcher: ProductMatcher, text: str | None) -> None:
    result = matcher.match(text)
    assert result.status is MatchStatus.NONE
    assert result.candidates == []
    assert result.product_id is None
    assert not result


def test_inactive_products_are_never_matched(matcher: ProductMatcher) -> None:
    for text in ("Эклер", "эклер", "пирожное эклер"):
        assert matcher.match(text).status is MatchStatus.NONE


def test_soft_deleted_products_are_never_matched() -> None:
    matcher = ProductMatcher([FakeProduct(id=7, name="Медовик", deleted_at="2026-09-01T00:00:00Z")])
    assert len(matcher) == 0
    assert matcher.match("медовик").status is MatchStatus.NONE


def test_ambiguous_text_returns_every_candidate() -> None:
    matcher = ProductMatcher(
        [
            FakeProduct(id=1, name="Красный бархат"),
            FakeProduct(id=2, name="Красный велюр"),
            FakeProduct(id=3, name="Медовик"),
        ]
    )
    result = matcher.match("красный")
    assert result.status is MatchStatus.MULTIPLE
    assert sorted(result.candidates) == [1, 2]
    assert result.product_id is None


def test_shared_alias_is_ambiguous() -> None:
    matcher = ProductMatcher(
        [
            FakeProduct(id=1, name="Медовик классический", aliases=["медовик"]),
            FakeProduct(id=2, name="Медовик шоколадный", aliases=["медовик"]),
        ]
    )
    result = matcher.match("медовик")
    assert result.status is MatchStatus.MULTIPLE
    assert result.candidates == [1, 2]


def test_two_products_in_one_message(matcher: ProductMatcher) -> None:
    # A mention that names two products at once is ambiguous; split it first.
    assert matcher.match("медовик и наполеон").status is MatchStatus.MULTIPLE
    mentions = split_item_mentions("Красный бархат и медовик")
    assert mentions == ["Красный бархат", "медовик"]
    assert [matcher.match(mention).product_id for mention in mentions] == [2, 1]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Красный бархат и медовик", ["Красный бархат", "медовик"]),
        ("медовик, наполеон + эклер", ["медовик", "наполеон", "эклер"]),
        ("Медовик ва Наполеон", ["Медовик", "Наполеон"]),
        ("медовик/наполеон", ["медовик", "наполеон"]),
        ("Медовик", ["Медовик"]),
        ("  Красный бархат  ", ["Красный бархат"]),
        ("", []),
        (None, []),
        ("Или медовик", ["Или медовик"]),  # "или" must not be split on "и"
    ],
)
def test_split_item_mentions(text: str | None, expected: list[str]) -> None:
    assert split_item_mentions(text) == expected


@pytest.mark.parametrize("text", ["торт", "торты", "2 торта", "хочу 2 торта", "десерт", "торти", "пирожное"])
def test_is_generic_mention(text: str) -> None:
    assert is_generic_mention(text) is True


@pytest.mark.parametrize(
    "text",
    ["медовик", "красный бархат", "торт медовик", "", "наполеон", "пражский торт", "торт с клубникой"],
)
def test_is_not_generic_mention(text: str) -> None:
    # A describing word makes the mention specific: "пражский торт" is a product we do not have,
    # not a request to pick one of ours.
    assert is_generic_mention(text) is False


def test_unknown_named_cake_is_not_offered_as_a_category(matcher: ProductMatcher) -> None:
    assert matcher.match("пражский торт").status is MatchStatus.NONE


# --------------------------------------------------------------------------- the cinnamon roll boxes (17.09.2026)


@pytest.fixture
def boxes() -> ProductMatcher:
    return ProductMatcher(
        [
            FakeProduct(id=1, name="Классические синнамоны", aliases=["классика"], unit="кор."),
            FakeProduct(id=2, name="Фисташковые синнамоны", aliases=["фисташка", "фисташковые"], unit="кор."),
            FakeProduct(id=3, name="Ассорти «Палитра вкуса»", aliases=["ассорти", "ассорти синнамонов"], unit="кор."),
            FakeProduct(id=4, name="Лимонад", aliases=["напиток"]),  # sold by the piece, not a box
        ]
    )


@pytest.mark.parametrize(
    "text", ["синнамоны", "2 синнамона", "синабоны", "синамоны", "булочки с корицей", "хочу синнамоны на завтра"]
)
def test_cinnamon_rolls_as_a_category(boxes: ProductMatcher, text: str) -> None:
    result = boxes.match(text)
    assert result.status is MatchStatus.MULTIPLE, result
    assert result.candidates == [1, 2, 3]
    assert is_generic_mention(text) is True


@pytest.mark.parametrize("text", ["коробка", "2 коробки", "коробочку", "3 коробочки на завтра"])
def test_a_box_means_the_products_sold_by_the_box(boxes: ProductMatcher, text: str) -> None:
    # "2 коробки" is a question "which ones?", never "we do not have «коробки»"
    result = boxes.match(text)
    assert result.status is MatchStatus.MULTIPLE, result
    assert result.candidates == [1, 2, 3]


@pytest.mark.parametrize(
    ("text", "expected_id"),
    [("коробочку фисташковых", 2), ("фисташковых", 2), ("классику", 1), ("ассорти", 3), ("синнамоны классика", 1)],
)
def test_a_named_flavour_is_one_product(boxes: ProductMatcher, text: str, expected_id: int) -> None:
    result = boxes.match(text)
    assert result.product_id == expected_id, result


def test_cakes_are_not_offered_from_a_cinnamon_catalog(boxes: ProductMatcher) -> None:
    assert boxes.match("2 торта").status is MatchStatus.NONE


def test_works_with_orm_like_objects() -> None:
    matcher = ProductMatcher(
        [
            FakeProduct(id=10, name="Медовик", aliases=["асал"]),
            FakeProduct(id=11, name="Эклер", is_active=False),
        ]
    )
    assert len(matcher) == 1
    assert matcher.match("асал").candidates == [10]
    assert matcher.match("эклер").status is MatchStatus.NONE


def test_empty_catalog() -> None:
    matcher = ProductMatcher([])
    assert matcher.match("медовик").status is MatchStatus.NONE
    assert matcher.match("торт").status is MatchStatus.NONE


def test_tajik_folding_in_names_and_queries() -> None:
    matcher = ProductMatcher([FakeProduct(id=1, name="Торти асал", aliases=["ширинии асал"])])
    for text in ("торти асал", "Торти асал", "ТОРТИ АСАЛ", "торти асал!"):
        assert matcher.match(text).product_id == 1
    assert matcher.match("ширинии асал").product_id == 1
