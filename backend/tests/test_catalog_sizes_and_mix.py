"""Two sizes of one flavour and the mix box (03-business-rules.md §1.3–§1.4, 23.09.2026).

All three rules here come from one live dialog (#3, order №49), read by the owner:

* the catalog was listed as twelve rows with a description each — «чтоб не было большого текста»,
  so it is listed by flavour now, both prices on one line and the sizes said once underneath;
* «Обычная стандартная коробка из 6 шт» was answered with «такого нет в каталоге» *and* the whole
  catalog twice in one message — a box is not a missing product, and one list per message is enough;
* «Можно микс со всеми вкусами» was answered the same way — a mix is assembled, not refused.

Deterministic throughout: ``ScriptedLLM`` only, no live model.
"""

# ruff: noqa: F811 — pytest fixtures imported from test_dialog_service are reused as parameter names

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai.catalog import flavour_bases, flavour_groups, flavour_names
from app.ai.product_matcher import MatchStatus, ProductMatcher, is_generic_mention, is_mix_mention
from app.ai.responder import ReplyKind, ReplyPlan, uses_template
from app.ai.templates import render
from app.models.conversation import Conversation
from app.models.product import Product
from app.services.settings_service import SettingsService
from tests.bot_fakes import ScriptedLLM, item, understanding
from tests.test_dialog_service import (  # noqa: F401 - fixtures are picked up by name
    Bot,
    make_conversation,
    reply_text,
)

#: The owner's price list of 23.09.2026: every flavour standard and large, each with its own price.
PRICE_LIST: tuple[tuple[str, str, str, str], ...] = (
    ("Классический синнамон", "10", "Классический большой", "15"),
    ("Шоколадный синнамон", "18", "Шоколадный большой", "26"),
    ("Фисташковый синнамон", "23", "Фисташковый большой", "34"),
)


@pytest.fixture
def sized_catalog(make_product: Callable[..., Product]) -> list[Product]:
    """Three flavours in two sizes, in the order the catalog lists them (standard first)."""
    standard = [make_product(name, price, aliases=[name.split()[0].lower()]) for name, price, _, _ in PRICE_LIST]
    large = [make_product(name, price, aliases=[name.lower()]) for _, _, name, price in PRICE_LIST]
    return standard + large


def _facts(products: list[Product]) -> list[dict[str, Any]]:
    return [{"name": p.name, "price": str(p.price), "unit": p.unit, "description": p.description} for p in products]


# --------------------------------------------------------------------------- grouping


def test_a_flavour_carries_its_large_size(sized_catalog: list[Product]) -> None:
    groups = flavour_groups(sized_catalog)

    assert [group.base.name for group in groups] == [name for name, _, _, _ in PRICE_LIST]
    assert [[(size, p.name) for size, p in group.variants] for group in groups] == [
        [("большой", large)] for _, _, large, _ in PRICE_LIST
    ]
    assert [product.name for product in flavour_bases(sized_catalog)] == [name for name, _, _, _ in PRICE_LIST]


def test_a_catalog_without_sizes_is_left_as_it_is(make_product: Callable[..., Product]) -> None:
    products = [make_product("Медовик", "750"), make_product("Эклер", "50")]

    assert [group.base.name for group in flavour_groups(products)] == ["Медовик", "Эклер"]
    assert all(group.variants == () for group in flavour_groups(products))


def test_a_shared_first_word_that_is_not_a_size_stays_its_own_product(
    make_product: Callable[..., Product],
) -> None:
    products = [make_product("Шоколадный синнамон", "18"), make_product("Шоколадный торт", "300")]

    assert [group.base.name for group in flavour_groups(products)] == ["Шоколадный синнамон", "Шоколадный торт"]


def test_names_fold_into_flavours() -> None:
    names = ["Классический синнамон", "Классический большой", "Медовик"]

    assert flavour_names(names) == ["Классический синнамон", "Медовик"]


# --------------------------------------------------------------------------- matching


@pytest.mark.parametrize(
    "text",
    [
        "стандартная коробка",
        "обычная стандартная коробка из 6 шт",
        "микс со всеми вкусами",
        "ассорти",
        "коробка разных вкусов",
    ],
)
def test_a_description_of_the_order_asks_which_flavours(sized_catalog: list[Product], text: str) -> None:
    """None of these names a product, so none of them may be answered "такого нет в каталоге"."""
    match = ProductMatcher(sized_catalog).match(text)

    assert match.status is MatchStatus.MULTIPLE
    assert len(match.candidates) == len(sized_catalog)
    # a group, not an ambiguity: the bot asks "какие именно?" and lists the flavours
    assert match.generic is True


@pytest.mark.parametrize("text", ["медовик", "круассаны", "синнамон с малиной"])
def test_a_flavour_we_do_not_have_is_still_answered_honestly(make_product: Callable[..., Product], text: str) -> None:
    products = [make_product("Классический синнамон", "10", aliases=["классический"])]

    assert ProductMatcher(products).match(text).status is MatchStatus.NONE


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("микс со всеми вкусами", True),
        ("ассорти", True),
        ("можно разные вкусы", True),
        ("омехта кунед", True),
        ("хочу все вкусы", True),
        ("коробка из 6 шт", False),
        ("шоколадный синнамон", False),
        ("разные дни", False),
    ],
)
def test_the_mix_detector(text: str, expected: bool) -> None:
    assert is_mix_mention(text) is expected


def test_a_description_counts_as_a_category_word() -> None:
    assert is_generic_mention("стандартная коробка") is True
    assert is_generic_mention("шоколадный синнамон") is False


# --------------------------------------------------------------------------- texts


def test_the_price_list_is_one_line_per_flavour(sized_catalog: list[Product]) -> None:
    facts = {"products": _facts(sized_catalog), "asked_specific": False}

    text = render(ReplyKind.PRODUCT_INFO, "ru", facts, [])

    assert text.splitlines() == [
        "Вот что у нас есть:",
        "Классический синнамон — 10 сомони / шт, большой — 15 сомони",
        "Шоколадный синнамон — 18 сомони / шт, большой — 26 сомони",
        "Фисташковый синнамон — 23 сомони / шт, большой — 34 сомони",
        "Первая цена — стандартный размер, вторая — большой.",
    ]
    # the whole price list is a table: the model does not reword it (05 §6)
    assert uses_template(ReplyPlan(ReplyKind.PRODUCT_INFO, "ru", facts)) is True


def test_a_question_about_one_product_still_gets_its_description(sized_catalog: list[Product]) -> None:
    facts = {"products": _facts(sized_catalog[:1]), "asked_specific": True}

    assert render(ReplyKind.PRODUCT_INFO, "ru", facts, []) == "Классический синнамон — 10 сомони / шт."
    # a catalog answer is the catalog's own words, never the model's (audit 23.09.2026, dialog #3)
    assert uses_template(ReplyPlan(ReplyKind.PRODUCT_INFO, "ru", facts)) is True


def test_the_tajik_price_list_says_the_size_in_tajik(sized_catalog: list[Product]) -> None:
    text = render(ReplyKind.PRODUCT_INFO, "tg", {"products": _facts(sized_catalog)}, [])

    assert "калон — 15 сомонӣ" in text
    assert "большой" not in text


def test_the_catalog_is_listed_once_per_message(sized_catalog: list[Product]) -> None:
    """Dialog #3: the customer got all twelve names twice in one reply (unknown + "какие именно?")."""
    names = [product.name for product in sized_catalog]
    facts = {
        "unknown_products": ["стандартная коробка"],
        "available_products": _facts(sized_catalog),
        "pending_items": [{"kind": "generic", "product_text": "стандартная коробка", "options": names}],
    }

    text = render(ReplyKind.ASK_MISSING, "ru", facts, ["items"])

    assert text.count("Классический синнамон") == 1
    assert "Сейчас можно заказать" not in text
    # "какие именно?" is a question about the flavour: the large sizes are not separate options
    assert "большой" not in text


def test_the_flavours_are_offered_without_their_sizes(sized_catalog: list[Product]) -> None:
    facts = {"unknown_products": ["круассаны"], "available_products": _facts(sized_catalog)}

    text = render(ReplyKind.UNKNOWN_PRODUCT, "ru", facts, [])

    assert text.endswith("Сейчас можно заказать: Классический синнамон, Шоколадный синнамон, Фисташковый синнамон.")


# --------------------------------------------------------------------------- the mix in a dialog


def test_a_mix_answers_an_open_count_with_one_of_every_flavour(
    db: Session, sized_catalog: list[Product], make_conversation: Callable[..., Conversation]
) -> None:
    SettingsService(db).update({"min_order_quantity": 3, "order_quantity_step": 3})
    llm = ScriptedLLM(
        {
            # the wording the live model returned on 23.09.2026 — "обычная" is a flavour word
            # ("обычные" = классические), so the text as a whole is neither a product nor a category
            "Обычная стандартная коробка из 6 шт": understanding(
                intent="CREATE_ORDER",
                entities={"items": [item("Обычная стандартная коробка", 6)], "items_mode": "add"},
            ),
            "Можно микс со всеми вкусами": understanding(
                intent="CREATE_ORDER",
                entities={"items": [item("микс со всеми вкусами")], "items_mode": "add"},
            ),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    asked = reply_text(bot.say("Обычная стандартная коробка из 6 шт"))
    assert "нет в нашем каталоге" not in asked
    assert "Подскажите, пожалуйста, какие именно?" in asked
    # the flavours to pick from, each named once — the large sizes are not separate options
    assert all(name in asked for name, _, _, _ in PRICE_LIST)
    assert "большой" not in asked

    reply_text(bot.say("Можно микс со всеми вкусами"))
    items = {line.product.name: line.quantity for line in bot.draft().items}
    assert items == {name: 2 for name, _, _, _ in PRICE_LIST}
    assert bot.state.pending_items == []


def test_a_mix_without_a_count_asks_which_flavours(
    db: Session, sized_catalog: list[Product], make_conversation: Callable[..., Conversation]
) -> None:
    """ "6 на 4 вкуса" is the customer's choice to make: the bot never splits a count it does not have."""
    llm = ScriptedLLM(
        {"Можно микс": understanding(intent="CREATE_ORDER", entities={"items": [item("микс")], "items_mode": "add"})}
    )
    bot = Bot(db, make_conversation(), llm)

    text = reply_text(bot.say("Можно микс"))

    # the flavours are settled — what is missing is the count, so that is what the bot asks
    assert text.startswith("Микс соберём из любых вкусов: Классический синнамон")
    assert "Сколько штук нужно?" in text
    assert bot.draft().items == []
