"""The audit of 23.09.2026: every live dialog and the whole Direct archive replayed through the bot.

Each test pins a general rule, not one sentence:

- a question about a food we do not make is answered "такого у нас нет" + what we do make — never the
  price list nobody asked for, never "уточню у менеджера" (dialog #155: «а пирожки вы печёте?»);
- the customer clarifying their own question ("я спрашиваю про пирожки") is that question again;
- every question of a message is answered, not only the first ("сколько стоит и есть доставка?");
- a price question gets the prices even when an FAQ keyword matches too ("цена за шт?");
- a second "уточню у менеджера" in a row is said in other words and the dialog stays with the bot —
  it used to hand the whole dialog over and fall silent (dialog #3);
- after a handoff the bot caused itself, plain questions are still answered from the data until a
  person writes (dialog #3: six questions went unanswered for an hour);
- "алло" / "вы тут?" / "?" get "Да, мы здесь" and the open question again;
- "потом напишу" is a "later", not a "no": the draft stays.
"""

# ruff: noqa: F811 — pytest fixtures imported from test_dialog_service are reused as parameter names

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.ai.catalog import sized_products
from app.ai.guard import ResponseGuard
from app.ai.product_matcher import ProductMatcher, is_general_mention, off_catalog_mentions
from app.ai.responder import ReplyKind
from app.ai.small_talk import SmallTalk, detect_small_talk
from app.ai.understanding import UnderstandingContext, parse_understanding
from app.core.time import now_utc
from app.models import Message, Product
from app.models.conversation import Conversation
from app.models.enums import ConversationMode, MessageDirection, MessageSender, MessageType
from app.models.faq import FaqItem
from app.services.dialog_service import (
    REASON_COMPLAINT,
    REASON_OPERATOR_REQUEST,
    REASON_REPEATED_REPLY,
)
from app.services.settings_service import SettingsService
from tests.bot_fakes import ScriptedLLM, item, understanding
from tests.test_dialog_service import (  # noqa: F401 - fixtures are picked up by name
    Bot,
    catalog,
    make_conversation,
    outgoing,
    reply_text,
)

# --------------------------------------------------------------------------- deterministic helpers


def test_off_catalog_foods_are_found_only_when_the_catalog_lacks_them(catalog: dict[str, Product]) -> None:
    matcher = ProductMatcher(catalog.values())

    assert off_catalog_mentions("здравствуйте а пирожки вы печете ?", matcher) == ["пирожки"]
    assert off_catalog_mentions("Круассаны и трайфл есть?", matcher) == ["круассаны", "трайфл"]
    # the cake shop of the fixtures sells a medovik and eclairs: the catalog answers those
    assert off_catalog_mentions("а медовик и эклеры есть?", matcher) == []
    # "блин" is a word people swear with, not a question about pancakes
    assert off_catalog_mentions("блин, а доставка есть?", matcher) == []


@pytest.mark.parametrize("text", ["десерты", "что из выпечки", "вкусняшки", "коробка", "микс", "ассорти"])
def test_words_for_the_whole_assortment_are_never_unknown(text: str) -> None:
    assert is_general_mention(text)


def test_size_words_find_the_large_rolls(make_product: Callable[..., Product]) -> None:
    classic = make_product("Классический синнамон", "10")
    classic_large = make_product("Классический большой", "15")
    chocolate = make_product("Шоколадный синнамон", "18")
    chocolate_large = make_product("Шоколадный большой", "25")
    products = [classic, classic_large, chocolate, chocolate_large]

    assert sized_products(products, "а большие есть?") == [classic_large, chocolate_large]
    assert sized_products(products, "большие шоколадные") == [chocolate_large]
    assert sized_products(products, "шоколадные") == []
    assert sized_products([classic, chocolate], "большие") == []  # no sizes in the catalog


@pytest.mark.parametrize(
    ("text", "talk"),
    [
        ("алло", SmallTalk.PING),
        ("Ау, вы тут?", SmallTalk.PING),
        ("?", SmallTalk.PING),
        ("??", SmallTalk.PING),
        ("Здравствуйте, вы тут?", SmallTalk.PING),
        ("хастед ми?", SmallTalk.PING),
        ("потом напишу", SmallTalk.NONE),  # a "later", answered by the FAQ — not a refusal
        ("тогда не надо", SmallTalk.DECLINE),
    ],
)
def test_pings_and_later(text: str, talk: SmallTalk) -> None:
    assert detect_small_talk(text) is talk


def test_the_asked_words_are_kept_clean() -> None:
    context = UnderstandingContext(now_business=now_utc())
    result = parse_understanding(
        {"intent": "PRODUCT_QUERY", "products_asked_text": ["  пирожки ", "Пирожки", "", None, 5, "торт"]}, context
    )
    assert result.products_asked_text == ["пирожки", "5", "торт"]


def test_the_owner_s_own_words_are_not_a_same_day_promise() -> None:
    guard = ResponseGuard()
    faq = "Напишете сегодня до 18:00 — завтра всё будет готово."

    assert guard.check("Хорошо, ждём! Напишете сегодня до 18:00 — завтра всё будет готово.", allowed_phrases=[faq]).ok
    assert guard.check("Сегодня привезём к вечеру!", allowed_phrases=[faq]).violations


# --------------------------------------------------------------------------- products we do not make


def _unknown_llm(text: str, **overrides) -> ScriptedLLM:
    return ScriptedLLM({text: understanding(**overrides)})


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"intent": "PRODUCT_QUERY", "products_asked_text": ["пирожки"]}, id="model-recorded-it"),
        pytest.param({"intent": "PRODUCT_QUERY"}, id="model-left-it-out"),
        pytest.param({"intent": "OTHER", "other_topic": "question"}, id="model-said-other"),
        pytest.param({"intent": "GREETING"}, id="model-said-greeting"),
    ],
)
def test_a_food_we_do_not_make_is_answered_honestly(
    db: Session,
    catalog: dict[str, Product],
    make_conversation: Callable[..., Conversation],
    overrides: dict,
) -> None:
    """Dialog #155: «а пирожки вы печёте?» got the price list, then «уточню у менеджера»."""
    text = "здравствуйте а пирожки вы печете ?"
    bot = Bot(db, make_conversation(), _unknown_llm(text, **overrides))

    outcome = bot.say(text)

    assert outcome.reply.kind == ReplyKind.UNKNOWN_PRODUCT
    assert outcome.image is None  # nobody asked for the prices
    assert reply_text(outcome) == (
        "Здравствуйте! К сожалению, «пирожки» у нас нет.\nСейчас можно заказать: Красный бархат, Медовик, Эклер."
    )
    db.refresh(bot.conversation)
    assert bot.conversation.mode == ConversationMode.AI and not bot.conversation.needs_attention


def test_the_customer_clarifying_the_question_gets_it_answered(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """«я спрашиваю про пирожки» — the model reads it as the question again (prompt rule 11a)."""
    llm = ScriptedLLM(
        {
            "а пирожки есть?": understanding(intent="PRODUCT_QUERY"),
            "я спрашиваю про пирожки": understanding(intent="PRODUCT_QUERY", products_asked_text=["пирожки"]),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    assert "«пирожки» у нас нет" in reply_text(bot.say("а пирожки есть?"))
    assert "«пирожки» у нас нет" in reply_text(bot.say("я спрашиваю про пирожки"))


def test_a_category_the_catalog_does_have_lists_those_products(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    llm = ScriptedLLM({"А торты у вас есть?": understanding(intent="PRODUCT_QUERY", products_asked_text=["торты"])})
    bot = Bot(db, make_conversation(), llm)

    text = reply_text(bot.say("А торты у вас есть?"))

    assert "у нас нет" not in text
    assert "Красный бархат — 750 сомони / шт." in text and "Медовик — 750 сомони / шт." in text
    assert "Эклер" not in text  # a cake question, not the whole list


def test_known_and_unknown_in_one_question(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    llm = ScriptedLLM(
        {
            "Круассаны есть? А медовик сколько?": understanding(
                intent="PRODUCT_QUERY",
                products_asked_text=["круассаны", "медовик"],
                product_ids_asked=[catalog["honey"].id],
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    text = reply_text(bot.say("Круассаны есть? А медовик сколько?"))

    assert text.startswith("К сожалению, «круассаны» у нас нет.\nМедовик — 750 сомони / шт.")


def test_a_food_asked_about_while_ordering_is_answered_and_the_order_goes_on(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {
            "2 медовика, а пирожки есть?": understanding(
                intent="CREATE_ORDER",
                secondary_intents=["PRODUCT_QUERY"],
                products_asked_text=["пирожки"],
                entities={"items": [item("медовика", 2, honey.id)], "items_mode": "add"},
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    text = reply_text(bot.say("2 медовика, а пирожки есть?"))

    assert text.startswith("К сожалению, «пирожки» у нас нет.")
    assert "На какую дату нужен заказ?" in text
    assert [(line.product_id, line.quantity) for line in bot.draft().items] == [(honey.id, 2)]


# --------------------------------------------------------------------------- every question of a message


def test_two_questions_in_one_message_get_two_answers(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    SettingsService(db).update({"delivery_info_text": "Доставка по Худжанду — 14 сомони."})
    llm = ScriptedLLM(
        {
            "Сколько стоит эклер и есть ли доставка?": understanding(
                intent="PRODUCT_QUERY",
                secondary_intents=["DELIVERY_QUERY"],
                product_ids_asked=[catalog["eclair"].id],
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    text = reply_text(bot.say("Сколько стоит эклер и есть ли доставка?"))

    assert text.startswith("Эклер — 50 сомони / шт.")
    assert "Доставка по Худжанду — 14 сомони." in text


def test_the_price_leads_when_asked_next_to_an_faq_question(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    fresh = FaqItem(question="Свежие?", answer="Печём в день выдачи.", keywords=["свежие"])
    db.add(fresh)
    db.commit()
    llm = ScriptedLLM(
        {
            "Они свежие? И сколько стоят?": understanding(
                intent="FAQ", secondary_intents=["PRODUCT_QUERY"], faq_ids=[fresh.id]
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    text = reply_text(bot.say("Они свежие? И сколько стоят?"))

    assert "Медовик — 750 сомони" in text  # the prices used to be lost
    assert "Печём в день выдачи." in text


def test_an_faq_keyword_does_not_take_the_price_away(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """Archive: «Асалом цена за шт?» got «по одной штуке не продаём» and no price at all."""
    piece = FaqItem(question="Поштучно?", answer="По одной не продаём, от 4 штук.", keywords=["за шт"])
    db.add(piece)
    db.commit()
    llm = ScriptedLLM({"Асалом цена за шт?": understanding(intent="PRODUCT_QUERY", faq_ids=[piece.id])})
    bot = Bot(db, make_conversation(), llm)

    text = reply_text(bot.say("Асалом цена за шт?"))

    assert "Медовик — 750 сомони" in text
    assert "По одной не продаём, от 4 штук." in text


def test_an_faq_answer_carries_the_food_we_do_not_make(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    cafe = FaqItem(question="Есть кафе?", answer="Кафе у нас нет — это домашняя выпечка.", keywords=["посидеть"])
    db.add(cafe)
    db.commit()
    text = "Понятно, а трайфл и круассаны есть и можно у вас посидеть покушать"
    llm = ScriptedLLM(
        {text: understanding(intent="FAQ", faq_ids=[cafe.id], products_asked_text=["трайфл", "круассаны"])}
    )
    bot = Bot(db, make_conversation(), llm)

    reply = reply_text(bot.say(text))

    assert "Кафе у нас нет" in reply
    assert "«трайфл», «круассаны» у нас нет" in reply


# --------------------------------------------------------------------------- "уточню у менеджера" twice


def test_a_second_unanswerable_question_keeps_the_dialog_with_the_bot(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    """Dialog #3: two payment questions while payment is switched off silenced the whole dialog."""
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="PAYMENT_QUERY")))

    first = bot.say("Предоплата нужна для оформления?")
    assert reply_text(first) == "Мне нужно уточнить эту информацию у менеджера."
    outgoing(db, bot.conversation, reply_text(first))

    second = bot.say("Можно оплату сделать после получения")
    assert not second.handoff
    assert reply_text(second).startswith("Этот вопрос тоже передали менеджеру — он ответит здесь.")
    db.refresh(bot.conversation)
    assert bot.conversation.mode == ConversationMode.AI and bot.conversation.needs_attention
    outgoing(db, bot.conversation, reply_text(second))

    # a third time in a row the bot really has nothing to add: then a person takes over
    third = bot.say("Так можно наличными или нет?")
    assert third.handoff


# --------------------------------------------------------------------------- while the manager is awaited


def _handed_over(db: Session, conversation: Conversation, reason: str = REASON_REPEATED_REPLY) -> None:
    conversation.mode = ConversationMode.HUMAN_HANDOFF
    conversation.handoff_reason = reason
    conversation.handoff_at = now_utc() - timedelta(minutes=5)
    conversation.needs_attention = True
    db.commit()


@pytest.fixture
def waiting_bot(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> Callable[..., Bot]:
    cafe = FaqItem(question="Есть кафе?", answer="Кафе у нас нет — это домашняя выпечка.", keywords=["кафе"])
    db.add(cafe)
    db.commit()
    llm = ScriptedLLM(
        {
            "Кафе доред": understanding(intent="FAQ", language="tg", faq_ids=[cafe.id]),
            "Какие есть?": understanding(intent="PRODUCT_QUERY"),
            "4 медовика на завтра": understanding(
                intent="CREATE_ORDER", entities={"items": [item("медовика", 4, catalog["honey"].id)]}
            ),
            "Бонус будет?": understanding(intent="OTHER", other_topic="question"),
            "Позовите человека уже": understanding(intent="OPERATOR_REQUEST"),
        }
    )

    def _make(reason: str = REASON_REPEATED_REPLY) -> Bot:
        conversation = make_conversation()
        _handed_over(db, conversation, reason)
        return Bot(db, conversation, llm)

    return _make


def test_plain_questions_are_answered_while_the_manager_is_awaited(
    db: Session, waiting_bot: Callable[..., Bot]
) -> None:
    bot = waiting_bot()

    cafe = bot.say("Кафе доред")
    assert cafe.reply is not None and "Кафе у нас нет" in reply_text(cafe)
    assert "assist" in cafe.actions and not cafe.handoff

    listing = bot.say("Какие есть?")
    assert "Медовик — 750 сомони" in reply_text(listing)

    db.refresh(bot.conversation)
    assert bot.conversation.mode == ConversationMode.HUMAN_HANDOFF  # still the manager's dialog
    assert bot.conversation.needs_attention


@pytest.mark.parametrize("text", ["4 медовика на завтра", "Бонус будет?", "Здравствуйте", "спасибо", "алло"])
def test_nothing_else_is_said_while_the_manager_is_awaited(waiting_bot: Callable[..., Bot], text: str) -> None:
    bot = waiting_bot()

    outcome = bot.say(text)

    assert outcome.reply is None and "assist_silent" in outcome.actions
    assert bot.state.draft_order_id is None  # no order is started behind the manager's back


def test_a_request_for_a_person_ends_the_answers(db: Session, waiting_bot: Callable[..., Bot]) -> None:
    bot = waiting_bot()

    asked = bot.say("Позовите человека уже")
    assert asked.reply is None  # "передаю менеджеру" is not said twice
    db.refresh(bot.conversation)
    assert bot.conversation.handoff_reason == REASON_OPERATOR_REQUEST

    assert bot.say("Кафе доред").reply is None  # the customer wants a person now


def test_the_bot_steps_back_as_soon_as_a_person_writes(db: Session, waiting_bot: Callable[..., Bot]) -> None:
    bot = waiting_bot()
    db.add(
        Message(
            conversation_id=bot.conversation.id,
            direction=MessageDirection.OUTGOING,
            message_type=MessageType.TEXT,
            sender=MessageSender.OPERATOR,
            text="Здравствуйте, сейчас подскажу",
        )
    )
    db.commit()

    assert bot.say("Кафе доред").reply is None


@pytest.mark.parametrize("reason", [REASON_OPERATOR_REQUEST, REASON_COMPLAINT, "Оператор взял диалог на себя"])
def test_a_handoff_the_customer_or_staff_chose_stays_silent(waiting_bot: Callable[..., Bot], reason: str) -> None:
    bot = waiting_bot(reason)

    outcome = bot.say("Кафе доред")

    assert outcome.reply is None and outcome.actions == ["human_handoff"]


# --------------------------------------------------------------------------- "алло", "?", "потом напишу"


def test_a_ping_is_answered_with_the_open_question(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {
            "2 медовика": understanding(
                intent="CREATE_ORDER", entities={"items": [item("медовика", 2, honey.id)], "items_mode": "add"}
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    assert reply_text(bot.say("алло")) == "Да, мы здесь 😊 Подскажите, что вас интересует?"
    bot.say("2 медовика")
    assert reply_text(bot.say("?")) == (
        "Да, мы здесь 😊 Уточните, пожалуйста:\n1. На какую дату нужен заказ?\n2. К какому времени?"
    )
    assert llm.json_calls and all(call["text"] == "2 медовика" for call in llm.json_calls)  # no model for a ping


def test_later_keeps_the_draft(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    later = FaqItem(question="Напишу позже", answer="Хорошо, ждём!", keywords=["потом напишу"])
    db.add(later)
    db.commit()
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {
            "2 медовика": understanding(
                intent="CREATE_ORDER", entities={"items": [item("медовика", 2, honey.id)], "items_mode": "add"}
            ),
            "потом напишу": understanding(intent="FAQ", faq_ids=[later.id]),
        }
    )
    bot = Bot(db, make_conversation(), llm)
    bot.say("2 медовика")
    draft = bot.state.draft_order_id

    text = reply_text(bot.say("потом напишу"))

    assert text.startswith("Хорошо, ждём!")
    assert bot.state.draft_order_id == draft  # it used to be dropped as a refusal


def test_the_same_small_talk_twice_never_goes_to_the_manager(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    """Audit replay: "алло" → "Да, мы здесь", "вы тут?" → the same words → the dialog was handed over."""
    bot = Bot(db, make_conversation(), ScriptedLLM())

    first = reply_text(bot.say("алло"))
    outgoing(db, bot.conversation, first)
    second = bot.say("вы тут?")

    assert not second.handoff
    assert reply_text(second) != first and reply_text(second).startswith("Мы на связи")
    outgoing(db, bot.conversation, reply_text(second))
    assert not bot.say("ау").handoff  # and it can go on like this


def test_the_food_we_do_not_make_asked_again_is_said_in_other_words(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    llm = ScriptedLLM(default=understanding(intent="PRODUCT_QUERY", products_asked_text=["пирожки"]))
    bot = Bot(db, make_conversation(), llm)

    first = reply_text(bot.say("а пирожки есть?"))
    outgoing(db, bot.conversation, first)
    second = bot.say("я спрашиваю про пирожки")

    assert not second.handoff
    assert reply_text(second) == "Да, к сожалению, «пирожки» мы не делаем. Могу подсказать цены на то, что есть 🙂"


# --------------------------------------------------------------------------- the model does not answer


def test_a_plain_question_is_answered_when_the_model_times_out(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """Archive replay: "Где находится ваш кафе" got "передаю менеджеру" only because the model timed out."""
    from app.ai.llm_client import LLMUnavailableError  # noqa: PLC0415

    cafe = FaqItem(question="Есть кафе?", answer="Кафе у нас нет — это домашняя выпечка.", keywords=["кафе"])
    db.add(cafe)
    db.commit()
    llm = ScriptedLLM(default=LLMUnavailableError(reason="timeout"))
    bot = Bot(db, make_conversation(), llm)

    assert reply_text(bot.say("Где находится ваш кафе")) == "Кафе у нас нет — это домашняя выпечка."
    assert "Медовик — 750 сомони" in reply_text(bot.say("Сколько стоит?"))
    assert "«пирожки» у нас нет" in reply_text(bot.say("а пирожки есть?"))
    assert llm.text_calls == []  # the reply is a template: a second call would only time out again
    db.refresh(bot.conversation)
    assert bot.conversation.mode == ConversationMode.AI

    # nothing in the data answers this: the manager takes the dialog, as before
    assert bot.say("Расскажите про вашу историю").handoff


# --------------------------------------------------------------------------- "все разные", "на сегодня"


@pytest.fixture
def rolls(make_product: Callable[..., Product]) -> list[Product]:
    return [
        make_product(name, "10")
        for name in ("Классический синнамон", "Шоколадный синнамон", "Ягодный синнамон", "Яблочный синнамон")
    ] + [make_product(name, "18") for name in ("Банановый синнамон", "Фисташковый синнамон")]


def test_all_different_answers_which_ones_without_going_round(
    db: Session, rolls: list[Product], make_conversation: Callable[..., Conversation]
) -> None:
    """Audit replay: "Хочу 4 синнамона" → "какие именно?" → "все разные" → the same question → handoff."""
    llm = ScriptedLLM(
        {
            "Хочу 4 синнамона": understanding(
                intent="CREATE_ORDER", entities={"items": [item("синнамона", 4)], "items_mode": "add"}
            ),
            "все разные": understanding(intent="CREATE_ORDER"),  # the model found no item in it
        }
    )
    bot = Bot(db, make_conversation(), llm)
    first = reply_text(bot.say("Хочу 4 синнамона"))
    assert first.startswith("Подскажите, пожалуйста, какие именно?")
    outgoing(db, bot.conversation, first)

    second = bot.say("все разные")

    assert not second.handoff
    assert reply_text(second).startswith("4 шт. на 6 вкусов поровну не делятся — напишите, какие выбрать:")
    assert "Или сделаем 6 шт. — по одному каждого?" in reply_text(second)

    third = bot.say("давайте 6")  # no model needed for a number

    assert sorted(line.quantity for line in bot.draft().items) == [1] * 6
    assert "На какую дату нужен заказ?" in reply_text(third)


def test_one_of_each_assembles_the_mix(
    db: Session, rolls: list[Product], make_conversation: Callable[..., Conversation]
) -> None:
    llm = ScriptedLLM(
        {
            "по одному каждого вкуса": understanding(
                intent="CREATE_ORDER", entities={"items": [item("по одному каждого вкуса")], "items_mode": "add"}
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    bot.say("по одному каждого вкуса")

    assert sorted(line.quantity for line in bot.draft().items) == [1] * 6


@pytest.mark.parametrize(
    ("text", "expected"),
    [("все разные", True), ("пусть будут любые", True), ("разные дни", False), ("по одному каждого", True)],
)
def test_all_different_is_a_mix(text: str, expected: bool) -> None:
    from app.ai.product_matcher import is_mix_mention  # noqa: PLC0415

    assert is_mix_mention(text) is expected


def test_the_owner_s_exact_phrase_adds_its_answer_to_the_model_s_choice(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    """Archive replay: "Во сколько сможете сегодня доставить" never heard that today is impossible."""
    today = FaqItem(question="На сегодня?", answer="На сегодня не получится.", keywords=["сможете сегодня", "кафе"])
    delivery = FaqItem(question="Во сколько привезут?", answer="Привозим к договорённому времени.")
    db.add_all([today, delivery])
    db.commit()
    llm = ScriptedLLM(default=understanding(intent="FAQ", faq_ids=[delivery.id]))
    bot = Bot(db, make_conversation(), llm)

    text = reply_text(bot.say("Во сколько сможете сегодня доставить"))
    assert "Привозим к договорённому времени." in text and "На сегодня не получится." in text

    # a single keyword is too broad to add an answer the model did not choose
    assert "На сегодня" not in reply_text(bot.say("А кафе у вас где?"))


def test_the_owner_s_phrase_is_answered_inside_an_order(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """Audit replay: "Можно подписать открытку? Хочу 6 шт…" — the card was lost behind "какие именно?"."""
    wishes = FaqItem(
        question="Пожелания?", answer="Пожелание напишите вместе с заказом.", keywords=["подписать открытку"]
    )
    db.add(wishes)
    db.commit()
    text = "Можно подписать открытку? Хочу 2 медовика"
    llm = ScriptedLLM(
        {
            text: understanding(
                intent="CREATE_ORDER",
                entities={"items": [item("медовика", 2, catalog["honey"].id)], "items_mode": "add"},
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    assert reply_text(bot.say(text)).startswith("Пожелание напишите вместе с заказом.")


def test_the_offered_slot_waits_while_the_time_is_open(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """Audit replay: the phone came between the offer and "да", the offer was forgotten, "да" → handoff."""
    from app.services.dialog_state import DialogState  # noqa: PLC0415
    from tests.bot_fakes import future_day  # noqa: PLC0415

    day = future_day(2)
    llm = ScriptedLLM(
        {
            "2 медовика": understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовика", 2, catalog["honey"].id)],
                    "delivery_date": day.isoformat(),
                    "delivery_type": "PICKUP",
                },
            ),
            "927001122": understanding(intent="CHANGE_ORDER", entities={"phone": "927001122"}),
        }
    )
    bot = Bot(db, make_conversation(), llm)
    bot.say("2 медовика")
    state = DialogState.from_json(bot.conversation.state)
    state.offered_slot = {"date": day.isoformat(), "time": "18:00"}  # "на этот день можем не раньше 18:00"
    bot.conversation.state = state.to_json()
    db.commit()

    bot.say("927001122")
    assert bot.state.offered_slot == {"date": day.isoformat(), "time": "18:00"}  # still open
    bot.say("да")

    assert bot.draft().delivery_time is not None and bot.draft().delivery_time.strftime("%H:%M") == "18:00"
    assert bot.state.offered_slot is None


# --------------------------------------------------------------------------- another town


@pytest.mark.parametrize(
    ("address", "town"),
    [
        ("Душанбе, Рудаки 45", "Душанбе"),
        ("г. Душанбе, ул. Айни 12", "Душанбе"),
        ("Гафуров, 5 дом", "Гафуров"),
        ("ул. Б. Гафурова 12", None),  # a street of Khujand
        ("улица Исфара 3", None),
        ("19 мкр, дом 2", None),
    ],
)
def test_another_town_in_the_address(address: str, town: str | None) -> None:
    from app.services.dialog_service import _other_town  # noqa: PLC0415

    assert _other_town(address) == town


def test_an_address_in_another_town_is_not_looked_up_in_ours(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    from tests.bot_fakes import FakeGeocoder, future_day  # noqa: PLC0415

    honey = catalog["honey"]
    text = "2 медовика, доставка в Душанбе, Рудаки 45"
    llm = ScriptedLLM(
        {
            text: understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовика", 2, honey.id)],
                    "items_mode": "add",
                    "delivery_date": future_day().isoformat(),
                    "delivery_time": "14:00",
                    "delivery_type": "DELIVERY",
                    "address": "Душанбе, Рудаки 45",
                },
            )
        }
    )
    geocoder = FakeGeocoder()
    bot = Bot(db, make_conversation(), llm, geocoder=geocoder)

    reply = reply_text(bot.say(text))

    assert geocoder.queries == []  # a Rudaki 45 exists in Khujand too
    assert reply.startswith("Доставка в Душанбе — по договорённости: возможность и стоимость подтвердит менеджер.")
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention


# --------------------------------------------------------------------------- payment switched off


def test_a_payment_question_is_the_manager_s_whatever_faq_the_model_picked(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    """Audit replay: «Предоплата нужна для оформления?» got the FAQ about trust — the nearest entry."""
    trust = FaqItem(question="А вдруг обманете?", answer="Понимаем, в первый раз всегда так.", keywords=["обманете"])
    db.add(trust)
    db.commit()
    llm = ScriptedLLM(default=understanding(intent="FAQ", faq_ids=[trust.id]))
    bot = Bot(db, make_conversation(), llm)

    assert reply_text(bot.say("Предоплата нужна для оформления?")) == "Мне нужно уточнить эту информацию у менеджера."


def test_the_payment_part_of_a_price_question_names_the_topic(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    text = "Здравствуйте, цены какие и можно ли оплатить наличными?"
    llm = ScriptedLLM({text: understanding(intent="PRODUCT_QUERY", secondary_intents=["PAYMENT_QUERY"])})
    bot = Bot(db, make_conversation(), llm)

    reply = reply_text(bot.say(text))

    assert "Медовик — 750 сомони" in reply
    assert reply.endswith("Про оплату уточню у менеджера — он напишет здесь.")
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention


def test_prices_in_side_answers_pass_the_guard() -> None:
    """``fact_amounts`` sees amounts inside ``answers``: a side answer never breaks the reply."""
    from app.ai.responder import fact_amounts  # noqa: PLC0415

    facts = {"answers": {"delivery_info": "Доставка по Худжанду — 14 сомони."}}
    assert Decimal("14") in fact_amounts(facts)
