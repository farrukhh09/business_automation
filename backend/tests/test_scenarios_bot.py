"""Scenario tests for the second round of bot fixes (16.09.2026, docs/PROGRESS.md):

small talk and off-topic routing, past dates and the earliest slot, the owner's phone examples, address flows
that never block the order (03 §1.3), Tajik stickiness, address parsing and the geocoder ladder.
Deterministic: ``ScriptedLLM`` and ``FakeGeocoder`` only.
"""

# ruff: noqa: F811 — pytest fixtures imported from test_dialog_service are reused as parameter names

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm_client import LLMUnavailableError
from app.ai.responder import ReplyKind
from app.ai.small_talk import Greeting, SmallTalk, detect_greeting, detect_small_talk
from app.core import time as app_time
from app.integrations.maps.address import candidate_matches, geocode_queries, parse_address
from app.integrations.maps.types import GeoCandidate, GeocodeResult, failed_result, rank_candidates
from app.models.conversation import Conversation, Message
from app.models.enums import (
    ConversationMode,
    DeliveryType,
    GeocodeStatus,
    Language,
    MessageType,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
)
from app.models.faq import FaqItem
from app.models.order import Order
from app.models.product import Product
from app.services.dialog_service import (
    ABANDON_REASON,
    AUTO_PAYMENT_NOTE,
    DRAFT_ABANDON_HOURS,
    REASON_IMAGE,
    REASON_RECEIPT_UNREAD,
)
from app.services.dialog_state import AWAITING_CONFIRMATION, AWAITING_MISSING_FIELDS
from app.services.geocoding_service import GeocodingService
from app.services.location_service import LocationService
from app.services.media_storage import MediaStorage
from app.services.settings_service import SettingsService
from tests.bot_fakes import (
    CITY_LAT,
    CITY_LNG,
    FakeGeocoder,
    ScriptedLLM,
    ambiguous,
    future_day,
    item,
    receipt_reading,
    single_house,
    understanding,
)
from tests.test_dialog_service import (  # noqa: F401 - fixtures are picked up by name
    DAY,
    Bot,
    catalog,
    make_conversation,
    pickup_settings,
    reply_text,
)

# --------------------------------------------------------------------------- helpers


def _pickup_llm(honey: Product, phone: str = "927809152") -> ScriptedLLM:
    """ "Медовик" gives a complete pickup order in one message."""
    return ScriptedLLM(
        {
            "Медовик": understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовик", 1, honey.id)],
                    "delivery_date": DAY.isoformat(),
                    "delivery_time": "18:00",
                    "delivery_type": "PICKUP",
                    "phone": phone,
                },
            )
        }
    )


def _delivery_llm(honey: Product, address: str) -> ScriptedLLM:
    return ScriptedLLM(
        {
            "Медовик": understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовик", 1, honey.id)],
                    "delivery_date": DAY.isoformat(),
                    "delivery_time": "17:00",
                    "delivery_type": "DELIVERY",
                    "phone": "901234567",
                },
            ),
            address: understanding(intent="CREATE_ORDER", entities={"address": address}),
        }
    )


# --------------------------------------------------------------------------- small talk


@pytest.mark.usefixtures("pickup_settings")
def test_thanks_after_a_confirmed_order_is_small_talk_not_a_manager(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    llm = _pickup_llm(catalog["honey"])
    bot = Bot(db, make_conversation(), llm)
    assert bot.say("Медовик").reply.kind == ReplyKind.ORDER_SUMMARY
    assert bot.say("Да").reply.kind == ReplyKind.ORDER_CONFIRMED
    calls = len(llm.json_calls)

    outcome = bot.say("спасибо")

    assert outcome.reply.kind == ReplyKind.SMALL_TALK and not outcome.handoff
    assert reply_text(outcome) == "Пожалуйста! Будем рады видеть вас снова 😊"
    assert len(llm.json_calls) == calls  # no model call for a bare "спасибо"
    db.refresh(bot.conversation)
    assert bot.conversation.mode == ConversationMode.AI and not bot.conversation.needs_attention
    assert bot.conversation.failed_ai_attempts == 0


def test_ack_while_filling_the_draft_repeats_the_next_question(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {"2 медовика": understanding(intent="CREATE_ORDER", entities={"items": [item("медовик", 2, honey.id)]})}
    )
    bot = Bot(db, make_conversation(), llm)
    first = reply_text(bot.say("2 медовика"))
    assert first == "Уточните, пожалуйста:\n1. На какую дату нужен заказ?\n2. К какому времени?"

    again = bot.say("ок")

    assert again.reply.kind == ReplyKind.ASK_MISSING and reply_text(again) == first
    db.refresh(bot.conversation)
    assert bot.conversation.failed_ai_attempts == 0
    assert bot.state.awaiting == AWAITING_MISSING_FIELDS


def test_goodbye_with_an_open_draft_reminds_about_it(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {"2 медовика": understanding(intent="CREATE_ORDER", entities={"items": [item("медовик", 2, honey.id)]})}
    )
    bot = Bot(db, make_conversation(), llm)
    bot.say("2 медовика")

    outcome = bot.say("пока")

    assert outcome.reply.kind == ReplyKind.SMALL_TALK
    assert reply_text(outcome) == (
        "Всего доброго! Пишите, если что-то понадобится.\n\n"
        "Уточните, пожалуйста:\n1. На какую дату нужен заказ?\n2. К какому времени?"
    )


def test_model_small_talk_is_answered_without_a_failed_attempt(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="OTHER", other_topic="small_talk")))

    outcome = bot.say("как дела?")

    assert outcome.reply.kind == ReplyKind.SMALL_TALK and not outcome.handoff
    assert reply_text(outcome).startswith("Мы «Синнамоны»")
    db.refresh(bot.conversation)
    assert bot.conversation.failed_ai_attempts == 0


def test_unanswerable_business_question_goes_to_the_manager_without_a_handoff(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="OTHER", other_topic="question")))

    outcome = bot.say("Работаете с юрлицами по безналу?")

    assert outcome.reply.kind == ReplyKind.NEED_MANAGER and not outcome.handoff
    assert reply_text(outcome) == "Мне нужно уточнить эту информацию у менеджера."
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention and bot.conversation.mode == ConversationMode.AI
    assert bot.conversation.failed_ai_attempts == 0


def test_unclear_messages_still_hand_over_after_two_attempts(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="OTHER", other_topic="unclear")))
    assert bot.say("эээ").reply.kind == ReplyKind.CLARIFY
    assert bot.say("ммм").handoff


def test_bare_greeting_is_answered_in_kind_without_the_model(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    llm = ScriptedLLM()
    bot = Bot(db, make_conversation(), llm)

    outcome = bot.say("Добрый день")

    assert outcome.reply.kind == ReplyKind.GREETING and not outcome.handoff
    assert reply_text(outcome) == "Добрый день! Что желаете заказать? 😊"
    db.refresh(bot.conversation)
    message = bot.conversation.messages[-1]
    assert message.ai_processed and message.intent == "GREETING"
    assert reply_text(bot.say("Ассалому алейкум!")) == "Ва алейкум ассалом! Чӣ фармоиш додан мехоҳед? 😊"
    assert reply_text(bot.say("Добрый вечер 🌸")) == "Добрый вечер! Что желаете заказать? 😊"
    assert llm.json_calls == [] and llm.text_calls == []
    db.refresh(bot.conversation)
    assert bot.conversation.failed_ai_attempts == 0 and not bot.conversation.needs_attention


@pytest.mark.usefixtures("pickup_settings")
def test_greeting_at_the_summary_reminds_to_confirm(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    bot = Bot(db, make_conversation(), _pickup_llm(catalog["honey"]))
    assert bot.say("Медовик").reply.kind == ReplyKind.ORDER_SUMMARY
    order = bot.draft()

    outcome = bot.say("Здравствуйте")

    assert reply_text(outcome) == f"Здравствуйте! Чтобы подтвердить заказ №{order.id}, напишите «Да»."
    assert bot.say("Да").reply.kind == ReplyKind.ORDER_CONFIRMED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Добрый день!", Greeting.DAY),
        ("доброе утро", Greeting.MORNING),
        ("Добрый вечер, есть синнамоны?", Greeting.EVENING),
        ("Здравствуйте, добрый день", Greeting.DAY),
        ("Ассалому алейкум", Greeting.SALAM),
        ("Шом ба хайр", Greeting.EVENING),
        ("Салом", Greeting.HELLO),
        ("Хочу коробку классики", None),
        (None, None),
    ],
)
def test_greeting_detector(text: str | None, expected: Greeting | None) -> None:
    assert detect_greeting(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("это всё", SmallTalk.DONE),
        ("Спасибо, это всё", SmallTalk.DONE),  # while ordering, "это всё" is what matters
        ("хорошо, больше ничего", SmallTalk.DONE),
        ("все", SmallTalk.NONE),  # after "какие именно?" a bare "все" may mean "all of them"
        ("Добрый день", SmallTalk.GREETING),
        ("Здравствуйте всем!", SmallTalk.GREETING),
        ("Рӯз ба хайр", SmallTalk.GREETING),
        ("Здравствуйте, спасибо", SmallTalk.THANKS),
        ("Добрый день, есть фисташковые?", SmallTalk.NONE),
        ("Субҳ ба хайр, нарх?", SmallTalk.NONE),  # "ба" belongs to the greeting, it does not excuse "нарх"
        ("Спасибо большое!", SmallTalk.THANKS),
        ("рахмат", SmallTalk.THANKS),
        ("Понял, спасибо", SmallTalk.THANKS),
        ("до свидания 👋", SmallTalk.GOODBYE),
        ("хайр", SmallTalk.GOODBYE),
        ("ок", SmallTalk.ACK),
        ("дальше", SmallTalk.ACK),
        ("ха хуб", SmallTalk.ACK),
        ("хорошо, в 18:00", SmallTalk.NONE),  # an answer, not an acknowledgement
        ("ок, доставка", SmallTalk.NONE),
        ("спасибо, а сколько стоит доставка?", SmallTalk.NONE),
        ("", SmallTalk.NONE),
    ],
)
def test_small_talk_detector(text: str, expected: SmallTalk) -> None:
    assert detect_small_talk(text) is expected


# --------------------------------------------------------------------------- quantities, repeated questions (17.09)


@pytest.fixture
def boxes(make_product: Callable[..., Product]) -> dict[str, Product]:
    return {
        "classic": make_product("Классические синнамоны", "100", unit="кор.", aliases=["классика"]),
        "berry": make_product("Ягодные синнамоны", "100", unit="кор.", aliases=["ягодные", "ягодных"]),
        "pistachio": make_product("Фисташковые синнамоны", "100", unit="кор.", aliases=["фисташковые", "фисташковых"]),
    }


def _items_of(bot: Bot) -> list[tuple[str, int]]:
    return sorted((line.product_name, line.quantity) for line in bot.draft().items)


def test_a_total_with_its_breakdown_is_not_an_extra_item(
    db: Session, boxes: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """The live dialog: "5 синамонов, 3 ягодных и 2 фисташковых" became five more rolls to pick."""
    text = "Хочу заказать 5 синамонов, 3 ягодных и 2 фисташковых"
    entities = {
        "items": [
            item("синамонов", 5),
            item("ягодных", 3, boxes["berry"].id),
            item("фисташковых", 2, boxes["pistachio"].id),
        ]
    }
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)}))

    outcome = bot.say(text)

    assert bot.state.pending_items == []
    assert _items_of(bot) == [("Фисташковые синнамоны", 2), ("Ягодные синнамоны", 3)]
    assert reply_text(outcome) == "Уточните, пожалуйста:\n1. На какую дату нужен заказ?\n2. К какому времени?"


def test_a_partial_breakdown_asks_only_for_the_rest(
    db: Session, boxes: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    text = "5 синнамонов, 3 ягодных"
    llm = ScriptedLLM(
        {
            text: understanding(
                intent="CREATE_ORDER",
                entities={"items": [item("синнамонов", 5), item("ягодных", 3, boxes["berry"].id)]},
            ),
            "классику": understanding(
                intent="CREATE_ORDER", entities={"items": [item("классику", None, boxes["classic"].id)]}
            ),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    outcome = bot.say(text)

    [pending] = bot.state.pending_items
    assert (pending["kind"], pending["quantity"]) == ("generic", 2)
    # the question is a template: what is written down, and how many more — no invented numbers
    assert reply_text(outcome) == (
        "Записали: Ягодные синнамоны — 3 кор.\n"
        "Подскажите, пожалуйста, какие ещё 2 выбрать? "
        "Сейчас есть: Классические синнамоны, Фисташковые синнамоны, Ягодные синнамоны."
    )
    assert outcome.reply.source.value == "template"

    bot.say("классику")
    assert _items_of(bot) == [("Классические синнамоны", 2), ("Ягодные синнамоны", 3)]
    assert bot.state.pending_items == []


def test_kinds_named_without_numbers_after_a_total_are_asked_how_many(
    db: Session, boxes: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    text = "4 синнамона: ягодные и фисташковые"
    entities = {
        "items": [
            item("синнамона", 4),
            item("ягодные", None, boxes["berry"].id),
            item("фисташковые", None, boxes["pistachio"].id),
        ]
    }
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)}))

    outcome = bot.say(text)

    assert [entry["kind"] for entry in bot.state.pending_items] == ["quantity", "quantity"]
    # products sold by the box are asked "сколько коробочек?", not "сколько штук?"
    assert reply_text(outcome) == "Сколько коробочек нужно: Ягодные синнамоны, Фисташковые синнамоны?"


def test_a_count_after_the_kinds_is_more_to_choose_and_eto_vsyo_ends_it(
    db: Session, boxes: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    text = "2 ягодных и ещё 3 синнамона"
    entities = {"items": [item("ягодных", 2, boxes["berry"].id), item("синнамона", 3)]}
    llm = ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)})
    bot = Bot(db, make_conversation(), llm)
    bot.say(text)
    [pending] = bot.state.pending_items
    assert (pending["kind"], pending["quantity"]) == ("generic", 3)
    calls = len(llm.json_calls)

    outcome = bot.say("Это всё")

    # the open "какие ещё?" is dropped instead of being asked again and again
    assert bot.state.pending_items == []
    assert _items_of(bot) == [("Ягодные синнамоны", 2)]
    assert reply_text(outcome) == "Уточните, пожалуйста:\n1. На какую дату нужен заказ?\n2. К какому времени?"
    assert len(llm.json_calls) == calls


def test_a_repeated_list_is_not_written_as_the_order_comment(
    db: Session, boxes: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    repeated = "Так я же сказал всего 5 синамонов, 3 ягодных и 2 фисташковых"
    llm = ScriptedLLM(
        {
            "Хочу 3 ягодных": understanding(
                intent="CREATE_ORDER", entities={"items": [item("ягодных", 3, boxes["berry"].id)]}
            ),
            repeated: understanding(
                intent="CHANGE_ORDER", entities={"comment": "Я же сказал всего 5 синамонов, 3 ягодных и 2 фисташковых"}
            ),
            "Положите открытку": understanding(intent="CHANGE_ORDER", entities={"comment": "Положите открытку"}),
        }
    )
    bot = Bot(db, make_conversation(), llm)
    bot.say("Хочу 3 ягодных")

    bot.say(repeated)
    assert bot.draft().comment is None

    bot.say("Положите открытку")  # a real wish is kept
    assert bot.draft().comment == "Положите открытку"


def test_a_too_early_hour_keeps_the_day_and_asks_only_for_the_time(
    db: Session,
    catalog: dict[str, Product],
    make_conversation: Callable[..., Conversation],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moment = datetime(2026, 9, 17, 11, 12, tzinfo=UTC)  # 16:12 in Khujand, the lead time is 24 h
    monkeypatch.setattr(app_time, "now_utc", lambda: moment)
    monkeypatch.setattr("app.services.order_validator.now_utc", lambda: moment)
    text = "Медовик на 18 сентября к 16:00"
    entities = {
        "items": [item("медовик", 1, catalog["honey"].id)],
        "delivery_date": "2026-09-18",
        "delivery_time": "16:00",
    }
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)}))

    outcome = bot.say(text)

    order = bot.draft()
    assert (order.delivery_date, order.delivery_time) == (date(2026, 9, 18), None)
    assert reply_text(outcome) == (
        "На завтра можем не раньше 17:00.\n"
        "Уточните, пожалуйста:\n1. К какому времени?\n2. Это будет доставка или самовывоз?"
    )


# --------------------------------------------------------------------------- dates and phones


def test_a_date_in_the_past_is_named_and_asked_again(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    past = future_day(0) - timedelta(days=40)
    llm = ScriptedLLM(
        {
            "Медовик 5 августа": understanding(
                intent="CREATE_ORDER",
                entities={"items": [item("медовик", 1, honey.id)], "delivery_date": past.isoformat()},
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    outcome = bot.say("Медовик 5 августа")

    text = reply_text(outcome)
    assert text.startswith(f"{past.day:02d}.{past.month:02d}.{past.year} уже прошло 🙂")
    assert "На какую дату нужен заказ?" in text
    assert bot.draft().delivery_date is None


def test_a_slot_that_is_too_soon_names_the_earliest_one(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {
            "Медовик сегодня": understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовик", 1, honey.id)],
                    "delivery_date": future_day(0).isoformat(),
                    "delivery_time": "23:30",
                },
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    text = reply_text(bot.say("Медовик сегодня"))

    assert text.startswith("Заказы принимаем не позднее чем за 24 ч. Самое раннее — ")
    assert " после " in text


@pytest.mark.usefixtures("pickup_settings")
def test_the_owners_phone_examples(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    llm = _pickup_llm(catalog["honey"], phone="9277373738292")
    llm.script["927809152"] = understanding(intent="CREATE_ORDER", entities={"phone": "927809152"})
    bot = Bot(db, make_conversation(), llm)

    rejected = reply_text(bot.say("Медовик"))
    assert rejected.startswith(
        "Номер телефона не получилось распознать. Напишите, пожалуйста, в формате 92 780 91 52 или +992 92 780 91 52."
    )

    summary = bot.say("927809152")
    assert summary.reply.kind == ReplyKind.ORDER_SUMMARY
    assert "Получатель: Алия, +992927809152" in reply_text(summary)


# --------------------------------------------------------------------------- addresses never block the order


def test_not_found_address_then_dalshe_reaches_the_summary(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    address = "18 мкр дом 7 кв 6"
    geocoder = FakeGeocoder(GeocodeResult(status=GeocodeStatus.NOT_FOUND, candidates=[], provider="fake"))
    bot = Bot(db, make_conversation(), _delivery_llm(catalog["honey"], address), geocoder)
    bot.say("Медовик")

    clarify = bot.say(address)

    assert clarify.reply.kind == ReplyKind.ADDRESS_CLARIFY
    text = reply_text(clarify)
    # the address is kept — only the map did not know it (never "no such address")
    assert text.startswith("Адрес записали, но на карте он не нашёлся 🙁 Отметьте, пожалуйста, точку на карте: http")
    assert "напишите «дальше»" in text
    assert "location" not in bot.state.missing_fields
    order = bot.draft()
    assert order.delivery.geocode_status == GeocodeStatus.NOT_FOUND
    # the customer's text was parsed for the courier
    assert (order.delivery.microdistrict, order.delivery.house, order.delivery.apartment) == ("18", "7", "6")
    assert geocoder.queries == ["Худжанд, 18 мкр, 7", "Худжанд, 18 микрорайон", "Худжанд, 18 мкр"]

    summary = bot.say("дальше")

    assert summary.reply.kind == ReplyKind.ORDER_SUMMARY
    assert "Адрес: 18 мкр дом 7 кв 6" in reply_text(summary)
    assert bot.draft().status == OrderStatus.WAITING_CONFIRMATION
    assert not bot.draft().delivery.has_coordinates


def test_street_found_but_not_the_house_centres_the_map_and_continues(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    address = "ул. Айни 12"
    geocoder = FakeGeocoder(ambiguous("улица Айни, Шохмансур, Душанбе, 734000, Таджикистан", precision="street"))
    bot = Bot(db, make_conversation(), _delivery_llm(catalog["honey"], address), geocoder)
    bot.say("Медовик")

    clarify = bot.say(address)

    text = reply_text(clarify)
    assert text.startswith("Нашли на карте «улица Айни, Шохмансур, Душанбе», но не сам дом.")
    assert bot.state.address_candidates == []  # a street is never offered as "the" point
    token = bot.draft().delivery.location_requests[0].token
    info = LocationService(db).public_info(token)
    assert info.suggested is not None and (info.suggested.lat, info.suggested.lng) == (CITY_LAT, CITY_LNG)

    assert bot.say("ок").reply.kind == ReplyKind.ORDER_SUMMARY


def test_provider_failure_alerts_the_operator_but_the_order_goes_on(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    address = "Сино, 82 мкр, дом 5"
    bot = Bot(
        db, make_conversation(), _delivery_llm(catalog["honey"], address), FakeGeocoder(failed_result("fake", "down"))
    )
    bot.say("Медовик")

    clarify = bot.say(address)

    text = reply_text(clarify)
    assert text.startswith("Сейчас не получается проверить адрес на карте — ничего страшного")
    assert "отметьте точку на карте: http" in text
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention
    assert bot.say("дальше").reply.kind == ReplyKind.ORDER_SUMMARY


def test_house_variants_may_be_skipped_with_dalshe(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    address = "Сино, 82 мкр, дом 5"
    geocoder = FakeGeocoder(ambiguous("Сино, 82 мкр, 5", "Сино, 82 мкр, 5/1"))
    bot = Bot(db, make_conversation(), _delivery_llm(catalog["honey"], address), geocoder)
    bot.say("Медовик")
    assert bot.say(address).reply.kind == ReplyKind.ADDRESS_CLARIFY

    summary = bot.say("дальше")

    assert summary.reply.kind == ReplyKind.ORDER_SUMMARY
    assert bot.state.address_candidates == [] and not bot.draft().delivery.has_coordinates


def test_candidate_names_are_shown_without_postal_code_and_country(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    address = "ул. Айни 12"
    geocoder = FakeGeocoder(
        ambiguous(
            "ТехМаркет, 12, улица Садриддина Айни, Шохмансур, Душанбе, 734042, Таджикистан",
            "12, улица Айни, Шохмансур, Душанбе, 734000, Таджикистан",
        )
    )
    bot = Bot(db, make_conversation(), _delivery_llm(catalog["honey"], address), geocoder)
    bot.say("Медовик")

    text = reply_text(bot.say(address))

    assert (
        "1. ТехМаркет, 12, улица Садриддина Айни, Шохмансур, Душанбе\n2. 12, улица Айни, Шохмансур, Душанбе\n" in text
    )
    assert "Таджикистан" not in text and "734042" not in text


def test_a_second_address_is_geocoded_and_accepted(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    geocoder = FakeGeocoder(GeocodeResult(status=GeocodeStatus.NOT_FOUND, candidates=[], provider="fake"))
    llm = _delivery_llm(catalog["honey"], "18 мкр дом 7")
    llm.script["проспект Рудаки 10"] = understanding(intent="CREATE_ORDER", entities={"address": "проспект Рудаки 10"})
    bot = Bot(db, make_conversation(), llm, geocoder)
    bot.say("Медовик")
    bot.say("18 мкр дом 7")

    geocoder.result = single_house("10, проспект Рудаки, Шохмансур, Душанбе")
    geocoder.queries.clear()
    summary = bot.say("проспект Рудаки 10")

    assert summary.reply.kind == ReplyKind.ORDER_SUMMARY
    delivery = bot.draft().delivery
    assert delivery.geocode_status == GeocodeStatus.OK and delivery.has_coordinates
    assert delivery.address_raw == "проспект Рудаки 10"
    # the parts of the first address (18 мкр, дом 7) are not geocoded instead of the new text
    assert geocoder.queries == ["Худжанд, проспект Рудаки, 10"]
    assert (delivery.microdistrict, delivery.street, delivery.house) == (None, "проспект Рудаки", "10")


# --------------------------------------------------------------------------- geocoding ladder (service level)


class _RecordingGeocoder:
    name = "fake"

    def __init__(self, results: dict[str, GeocodeResult], default: GeocodeResult) -> None:
        self.results = results
        self.default = default
        self.calls: list[str] = []

    def geocode(self, query: str, *, city: str, country_code: str = "tj") -> GeocodeResult:
        self.calls.append(query)
        return self.results.get(query, self.default)


def _order_with_address(make_order, address: str):
    return make_order(delivery_type=DeliveryType.DELIVERY, delivery={"address_raw": address})


def test_single_house_among_streets_is_accepted(db: Session, make_order) -> None:
    order = _order_with_address(make_order, "ул. Айни 12")
    nominatim_like = GeocodeResult(
        status=GeocodeStatus.AMBIGUOUS,
        candidates=[
            GeoCandidate("ТехМаркет, 12, улица Садриддина Айни, Пахтакор, Худжанд", 40.2927, 69.6188, "house", True),
            GeoCandidate("улица Айни, Пахтакор, Худжанд", 40.2835, 69.6464, "street"),
            GeoCandidate("улица Айни, Пахтакор, Худжанд", 40.2897, 69.6271, "street"),
        ],
        provider="fake",
    )
    geocoder = _RecordingGeocoder({"Худжанд, улица Айни, 12": nominatim_like}, default=nominatim_like)

    GeocodingService(db, geocoder=geocoder).geocode_delivery(order.delivery)

    assert geocoder.calls == ["Худжанд, улица Айни, 12"]
    assert order.delivery.geocode_status == GeocodeStatus.OK
    assert float(order.delivery.latitude) == pytest.approx(40.2927)


def test_ladder_falls_back_to_the_microdistrict_and_rejects_other_numbers(db: Session, make_order) -> None:
    order = _order_with_address(make_order, "18 микрорайон дом 7")
    empty = GeocodeResult(status=GeocodeStatus.NOT_FOUND, candidates=[], provider="fake")
    fuzzy = GeocodeResult(
        status=GeocodeStatus.AMBIGUOUS,
        candidates=[
            GeoCandidate("91-й микрорайон, Себзор, Худжанд", 40.3193, 69.5928, "district"),
            GeoCandidate("18-й микрорайон, Себзор, Худжанд", 40.2961, 69.5997, "district"),
        ],
        provider="fake",
    )
    geocoder = _RecordingGeocoder({"Худжанд, 18 микрорайон": fuzzy}, default=empty)

    GeocodingService(db, geocoder=geocoder).geocode_delivery(order.delivery)

    assert geocoder.calls == ["Худжанд, 18 мкр, 7", "Худжанд, 18 микрорайон"]
    assert order.delivery.geocode_status == GeocodeStatus.AMBIGUOUS
    assert [c["formatted"] for c in order.delivery.geocode_candidates] == ["18-й микрорайон, Себзор, Худжанд"]
    assert order.delivery.latitude is None


def test_rank_candidates_folds_a_poi_into_the_plain_house() -> None:
    plain = GeoCandidate("12, улица Айни", 40.29, 69.62, "house")
    poi = GeoCandidate("ТехМаркет, 12, улица Айни", 40.29, 69.62, "house", True)
    street = GeoCandidate("улица Айни", 38.55, 68.84, "street")
    assert rank_candidates([street, poi, plain]) == [plain, street]
    assert rank_candidates([street, poi]) == [poi, street]  # a POI alone still carries the house


# --------------------------------------------------------------------------- address parsing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("18 мкр дом 7 кв 6", {"microdistrict": "18", "house": "7", "apartment": "6"}),
        ("19 микрорайон, 2", {"microdistrict": "19", "house": "2"}),
        (
            "Себзор, 82-й микрорайон, 5/1, подъезд 2, этаж 3",
            {"district": "Себзор", "microdistrict": "82", "house": "5/1", "entrance": "2", "floor": "3"},
        ),
        ("ул. Айни 12, кв 4", {"street": "улица Айни", "house": "12", "apartment": "4"}),
        ("проспект Рудаки 10", {"street": "проспект Рудаки", "house": "10"}),
        ("кӯчаи Рӯдакӣ 10", {"street": "улица Рудаки", "house": "10"}),
        ("хиёбони Рӯдакӣ, хонаи 45", {"street": "проспект Рудаки", "house": "45"}),
        ("г. Худжанд, улица Бухоро, 5, кв. 12", {"street": "улица Бухоро", "house": "5", "apartment": "12"}),
        ("Пахтакор 46 мкр д. 12", {"district": "Пахтакор", "microdistrict": "46", "house": "12"}),
        ("возле рынка Корвон", {"landmark": "рынка Корвон"}),
        ("Испечак, дом 3, ориентир: школа №5", {"house": "3", "landmark": "Школа №5", "rest": "Испечак"}),
        ("", {}),
    ],
)
def test_parse_address(raw: str, expected: dict[str, str]) -> None:
    parsed = parse_address(raw)
    found = {
        key: value
        for key, value in (
            ("district", parsed.district),
            ("microdistrict", parsed.microdistrict),
            ("street", parsed.street),
            ("house", parsed.house),
            ("apartment", parsed.apartment),
            ("entrance", parsed.entrance),
            ("floor", parsed.floor),
            ("landmark", parsed.landmark),
            ("rest", parsed.rest),
        )
        if value
    }
    assert found == expected


def test_geocode_queries_ladder() -> None:
    # Khujand's OpenStreetMap names: houses on "18 мкр", places "18 микрорайон" (never "18-й микрорайон")
    assert [(q.level, q.text) for q in geocode_queries(parse_address("18 мкр дом 7 кв 6"), "Худжанд")] == [
        ("house", "Худжанд, 18 мкр, 7"),
        ("microdistrict", "Худжанд, 18 микрорайон"),
        ("microdistrict", "Худжанд, 18 мкр"),
    ]
    assert [(q.level, q.text) for q in geocode_queries(parse_address("ул. Айни 12"), "Худжанд")] == [
        ("house", "Худжанд, улица Айни, 12"),
        ("street", "Худжанд, улица Айни"),
    ]
    assert [
        (q.level, q.text) for q in geocode_queries(parse_address("Испечак, дом 3, ориентир: школа №5"), "Худжанд")
    ] == [
        ("house", "Худжанд, Испечак, 3"),
        ("raw", "Худжанд, Испечак"),
        ("landmark", "Худжанд, Школа №5"),
    ]
    assert geocode_queries(parse_address("Худжанд"), "Худжанд") == []


def test_candidate_matches_rejects_only_contradictions() -> None:
    [house_query, microdistrict_query, _] = geocode_queries(parse_address("18 мкр дом 7"), "Худжанд")
    assert not candidate_matches(microdistrict_query, "91-й микрорайон, Себзор, Худжанд")
    assert candidate_matches(microdistrict_query, "18-й микрорайон, Себзор, Худжанд")
    assert candidate_matches(house_query, "Худжанд, Таджикистан")  # coarse, graded by precision later
    # live Nominatim answers for "28 мкр": house 28 of another microdistrict is out, the "28мкр" street in
    # the 27th microdistrict's area stays
    [house_28, *_] = geocode_queries(parse_address("28 микрорайон дом 76"), "Худжанд")
    assert not candidate_matches(house_28, "28, 31 мкр, 31 микрорайон, Шейх Бурхан, г.Худжанд")
    assert candidate_matches(house_28, "28мкр, 27 микрорайон, г.Худжанд, Согдийская область")
    [street_query] = geocode_queries(parse_address("улица Айни"), "Худжанд")
    assert not candidate_matches(street_query, "улица Бухоро, Пахтакор, Худжанд")
    assert candidate_matches(street_query, "Пахтакор, Худжанд")


# --------------------------------------------------------------------------- Tajik stickiness


def test_a_tajik_customer_naming_products_stays_in_tajik(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    velvet, honey = catalog["velvet"], catalog["honey"]
    llm = ScriptedLLM(
        {
            "Салом, 2 торт мехохам": understanding(
                intent="CREATE_ORDER",
                language="tg",
                entities={"items": [item("торт", 2)], "items_mode": "add", "delivery_date": DAY.isoformat()},
            ),
            "Красный бархат ва медовик": understanding(
                intent="CREATE_ORDER",
                language="tg",
                entities={
                    "items": [item("Красный бархат", None, velvet.id), item("медовик", None, honey.id)],
                    "items_mode": "add",
                },
            ),
        }
    )
    bot = Bot(db, make_conversation(language=Language.TG), llm)

    first = bot.say("Салом, 2 торт мехохам")
    assert first.reply.language == "tg"
    second = bot.say("Красный бархат ва медовик")

    assert second.reply.language == "tg"
    assert reply_text(second).startswith("Лутфан, аниқ кунед:")


# --------------------------------------------------------------------------- review of 17.09.2026


def test_a_quantity_correction_changes_only_that_product(
    db: Session, boxes: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """ "Ягодных не 2, а 3" is items_mode "set": the other positions stay (neither doubled nor dropped)."""
    berry, classic = boxes["berry"], boxes["classic"]
    llm = ScriptedLLM(
        {
            "2 ягодных и 3 классических": understanding(
                intent="CREATE_ORDER",
                entities={"items": [item("ягодных", 2, berry.id), item("классических", 3, classic.id)]},
            ),
            "нет, ягодных 3": understanding(
                intent="CHANGE_ORDER", entities={"items": [item("ягодных", 3, berry.id)], "items_mode": "set"}
            ),
            "и ещё 1 ягодную": understanding(
                intent="CHANGE_ORDER", entities={"items": [item("ягодную", 1, berry.id)], "items_mode": "add"}
            ),
        }
    )
    bot = Bot(db, make_conversation(), llm)
    bot.say("2 ягодных и 3 классических")

    bot.say("нет, ягодных 3")
    assert _items_of(bot) == [("Классические синнамоны", 3), ("Ягодные синнамоны", 3)]

    bot.say("и ещё 1 ягодную")
    assert _items_of(bot) == [("Классические синнамоны", 3), ("Ягодные синнамоны", 4)]


def test_set_mode_answering_a_choice_still_adds(
    db: Session, boxes: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """The answer to "какие ещё 2?" adds to the order even when the model tagged it "set"."""
    classic = boxes["classic"]
    llm = ScriptedLLM(
        {
            "1 классику": understanding(intent="CREATE_ORDER", entities={"items": [item("классику", 1, classic.id)]}),
            "и ещё 2 синнамона": understanding(intent="CHANGE_ORDER", entities={"items": [item("синнамона", 2)]}),
            "классические": understanding(
                intent="CHANGE_ORDER",
                entities={"items": [item("классические", None, classic.id)], "items_mode": "set"},
            ),
        }
    )
    bot = Bot(db, make_conversation(), llm)
    bot.say("1 классику")
    bot.say("и ещё 2 синнамона")
    assert bot.state.pending_items[0]["kind"] == "generic"

    bot.say("классические")

    assert _items_of(bot) == [("Классические синнамоны", 3)]
    assert bot.state.pending_items == []


@pytest.mark.usefixtures("pickup_settings")
def test_a_question_asked_together_with_order_data_is_answered_first(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """ "2 медовика на завтра — а доставка есть?": the question is not dropped for the next question."""
    honey = catalog["honey"]
    SettingsService(db).update({"delivery_info_text": "Доставка по городу — 20 сомони."})
    fresh = FaqItem(question="Насколько свежая выпечка?", answer="Печём в день выдачи.", keywords=["свежие"])
    db.add(fresh)
    db.commit()
    llm = ScriptedLLM(
        {
            "2 медовика на завтра, а доставка есть?": understanding(
                intent="CREATE_ORDER",
                secondary_intents=["DELIVERY_QUERY"],
                entities={"items": [item("медовик", 2, honey.id)], "delivery_date": DAY.isoformat()},
            ),
            "к 18:00, а картой можно?": understanding(
                intent="CREATE_ORDER",
                secondary_intents=["PAYMENT_QUERY"],
                entities={"delivery_time": "18:00"},
            ),
            "самовывоз, 92 780 91 52, а выпечка свежая?": understanding(
                intent="CREATE_ORDER",
                secondary_intents=["FAQ"],
                faq_ids=[fresh.id],
                entities={"delivery_type": "PICKUP", "phone": "92 780 91 52"},
            ),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    first = bot.say("2 медовика на завтра, а доставка есть?")
    assert reply_text(first) == (
        "Доставка по городу — 20 сомони.\n\n"
        "Самовывоз: ул. Айни, 12\n\n"
        "Уточните, пожалуйста:\n1. К какому времени?\n2. Это будет доставка или самовывоз?"
    )
    db.refresh(bot.conversation)
    assert not bot.conversation.needs_attention

    # no payment methods in the settings: the manager is alerted, the order still goes on
    second = bot.say("к 18:00, а картой можно?")
    assert reply_text(second) == (
        "Мне нужно уточнить эту информацию у менеджера.\n\n"
        "Уточните, пожалуйста:\n"
        "1. Это будет доставка или самовывоз?\n"
        "2. Напишите, пожалуйста, номер телефона для связи."
    )
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention and bot.conversation.mode == ConversationMode.AI

    summary = bot.say("самовывоз, 92 780 91 52, а выпечка свежая?")
    assert summary.reply.kind == ReplyKind.ORDER_SUMMARY
    assert reply_text(summary).startswith("Печём в день выдачи.\n\nПроверьте, пожалуйста, заказ:\n\nМедовик — 2 шт.")


def test_a_refused_slot_replaces_the_stored_one(
    db: Session,
    catalog: dict[str, Product],
    make_conversation: Callable[..., Conversation],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete order moved to an hour or a day we cannot take: the old slot is not silently kept."""
    moment = datetime(2026, 9, 17, 11, 12, tzinfo=UTC)  # 16:12 in Khujand; the earliest slot is 18.09 17:00
    monkeypatch.setattr(app_time, "now_utc", lambda: moment)
    monkeypatch.setattr("app.services.order_validator.now_utc", lambda: moment)
    llm = ScriptedLLM(
        {
            "Медовик": understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовик", 1, catalog["honey"].id)],
                    "delivery_date": "2026-09-19",
                    "delivery_time": "12:00",
                    "delivery_type": "PICKUP",
                    "phone": "927809152",
                },
            ),
            "лучше 18 сентября к 12:00": understanding(
                intent="CHANGE_ORDER", entities={"delivery_date": "2026-09-18", "delivery_time": "12:00"}
            ),
            "к 18:00": understanding(intent="CHANGE_ORDER", entities={"delivery_time": "18:00"}),
            "нет, сегодня к 20:00": understanding(
                intent="CHANGE_ORDER", entities={"delivery_date": "2026-09-17", "delivery_time": "20:00"}
            ),
        }
    )
    bot = Bot(db, make_conversation(), llm)
    assert bot.say("Медовик").reply.kind == ReplyKind.ORDER_SUMMARY

    too_early = bot.say("лучше 18 сентября к 12:00")
    order = bot.draft()
    assert (order.delivery_date, order.delivery_time) == (date(2026, 9, 18), None)
    assert reply_text(too_early) == "На завтра можем не раньше 17:00.\nК какому времени?"

    assert bot.say("к 18:00").reply.kind == ReplyKind.ORDER_SUMMARY
    assert bot.state.awaiting == AWAITING_CONFIRMATION

    refused_day = bot.say("нет, сегодня к 20:00")
    order = bot.draft()
    assert (order.delivery_date, order.delivery_time) == (None, None)
    assert reply_text(refused_day) == (
        "Заказы принимаем не позднее чем за 24 ч. Самое раннее — завтра после 17:00. "
        "Выберите, пожалуйста, другую дату или время.\n"
        "Уточните, пожалуйста:\n1. На какую дату нужен заказ?\n2. К какому времени?"
    )


def test_a_greeting_inside_a_question_is_answered_back_by_the_model_too(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    fresh = FaqItem(question="Насколько свежая выпечка?", answer="Печём в день выдачи.", keywords=["свежие"])
    db.add(fresh)
    db.commit()
    prompts: list[str] = []

    def word(call: dict[str, Any]) -> str:
        prompts.append(call["prompt"])
        return "Добрый день! Печём в день выдачи."

    llm = ScriptedLLM({"Добрый день, выпечка свежая?": understanding(intent="FAQ", faq_ids=[fresh.id])}, reply=word)
    bot = Bot(db, make_conversation(), llm)

    outcome = bot.say("Добрый день, выпечка свежая?")

    assert outcome.reply.source.value == "llm" and reply_text(outcome) == "Добрый день! Печём в день выдачи."
    assert '"greeting": "day"' in prompts[0]  # the model is told which greeting to return
    # the template fallback greets back as well
    assert bot.service.llm is llm
    llm.reply = None
    assert reply_text(bot.say("Добрый день, выпечка свежая?")) == "Добрый день! Печём в день выдачи."


def test_boxes_answer_the_how_many_question(
    db: Session, boxes: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    classic, berry = boxes["classic"], boxes["berry"]
    llm = ScriptedLLM(
        {
            "Хочу классические": understanding(
                intent="CREATE_ORDER", entities={"items": [item("классические", None, classic.id)]}
            ),
            "Хочу ягодные": understanding(intent="CREATE_ORDER", entities={"items": [item("ягодные", None, berry.id)]}),
            "две коробки": understanding(intent="CREATE_ORDER", entities={"items": [item("коробки", 2)]}),
        }
    )
    bot = Bot(db, make_conversation(), llm)
    assert reply_text(bot.say("Хочу классические")) == "Сколько коробочек нужно: Классические синнамоны?"
    calls = len(llm.json_calls)

    bot.say("2 коробки")  # a bare number with the unit: no model call

    assert len(llm.json_calls) == calls
    assert _items_of(bot) == [("Классические синнамоны", 2)] and bot.state.pending_items == []

    bot.say("Хочу ягодные")
    bot.say("две коробки")  # through the model: a counted category word answers the open quantity

    assert _items_of(bot) == [("Классические синнамоны", 2), ("Ягодные синнамоны", 2)]
    assert bot.state.pending_items == []


def test_a_stale_draft_is_abandoned_when_a_new_order_starts(
    db: Session, boxes: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """ "2 классических" written days ago are not merged into today's "3 ягодных"."""
    classic, berry = boxes["classic"], boxes["berry"]
    llm = ScriptedLLM(
        {
            "2 классических": understanding(
                intent="CREATE_ORDER", entities={"items": [item("классических", 2, classic.id)]}
            ),
            "3 ягодных": understanding(intent="CREATE_ORDER", entities={"items": [item("ягодных", 3, berry.id)]}),
            "на завтра": understanding(intent="CHANGE_ORDER", entities={"delivery_date": DAY.isoformat()}),
        }
    )
    bot = Bot(db, make_conversation(), llm)
    bot.say("2 классических")
    old = bot.draft()
    old.updated_at = app_time.now_utc() - timedelta(hours=DRAFT_ABANDON_HOURS + 1)
    db.commit()

    bot.say("на завтра")  # a field alone continues the old draft
    assert bot.state.draft_order_id == old.id

    old.updated_at = app_time.now_utc() - timedelta(hours=DRAFT_ABANDON_HOURS + 1)
    db.commit()
    outcome = bot.say("3 ягодных")

    assert "draft_abandoned" in outcome.actions and bot.state.draft_order_id != old.id
    assert _items_of(bot) == [("Ягодные синнамоны", 3)]
    db.refresh(old)
    assert old.status == OrderStatus.CANCELLED and old.cancel_reason == ABANDON_REASON
    assert db.get(Order, bot.state.draft_order_id).status == OrderStatus.NEW


def test_recipient_is_defaulted_on_every_path_to_the_summary(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """The name arrives from staff (the admin panel) between two messages; "ок" must reach the summary,
    not crash on ``order_incomplete`` because the recipient was never defaulted."""
    text = "Медовик, доставка на улицу Рудаки 45"
    llm = ScriptedLLM(
        {
            text: understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовик", 1, catalog["honey"].id)],
                    "delivery_date": DAY.isoformat(),
                    "delivery_time": "17:00",
                    "delivery_type": "DELIVERY",
                    "phone": "901234567",
                    "address": "улица Рудаки 45",
                },
            )
        }
    )
    conversation = make_conversation(name="")  # the Instagram profile gave no name
    bot = Bot(db, conversation, llm)
    assert reply_text(bot.say(text)) == "Как к вам можно обращаться?"

    conversation.customer.name = "Алия"
    db.commit()
    outcome = bot.say("ок")

    assert outcome.reply.kind == ReplyKind.ORDER_SUMMARY and not outcome.handoff
    assert bot.draft().delivery.recipient_name == "Алия"


# --------------------------------------------------------------------------- prepayment and receipts (03 §3)

WALLET = "+992 92 757 53 33"
CARD = "5058 2703 8115 6297"  # the owner may put a card number in the settings instead of a wallet
BANKS = "Душанбе Сити, Алиф, Эсхата"
PREPAYMENT_REQUEST = (
    f"Предоплата — 750 сомони: переведите, пожалуйста, на кошелёк {WALLET} ({BANKS}) и пришлите сюда чек — "
    "скриншот перевода. Как только менеджер увидит оплату, заказ пойдёт в работу."
)


@pytest.fixture
def prepayment(db: Session) -> None:
    SettingsService(db).update(
        {"prepayment_enabled": True, "prepayment_wallet": WALLET, "prepayment_wallet_banks": BANKS}
    )


@pytest.fixture
def media(tmp_path: Path) -> MediaStorage:
    return MediaStorage(tmp_path / "media")


def _confirmed_order(db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], **deps):
    """A confirmed pickup order for one Медовик (750 сомони) and the bot that took it."""
    llm = deps.pop("llm", None) or _pickup_llm(catalog["honey"])
    bot = Bot(db, make_conversation(), llm, **deps)
    assert bot.say("Медовик").reply.kind == ReplyKind.ORDER_SUMMARY
    order = bot.draft()
    confirmed = bot.say("Да")
    assert confirmed.reply.kind == ReplyKind.ORDER_CONFIRMED
    return bot, order, confirmed


def _send_image(bot: Bot, media: MediaStorage):
    filename = media.save(b"\xff\xd8receipt", prefix="in", extension="jpg")
    return bot.say(None, message_type=MessageType.IMAGE, media_url=MediaStorage.url_path(filename))


@pytest.mark.usefixtures("pickup_settings", "prepayment")
def test_confirmation_asks_for_the_prepayment(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    bot, order, confirmed = _confirmed_order(db, catalog, make_conversation)

    assert reply_text(confirmed).endswith(f"Итого: 750 сомони.\n\n{PREPAYMENT_REQUEST}")

    # the wallet is named in the payment answer too
    llm = ScriptedLLM(default=understanding(intent="PAYMENT_QUERY"))
    answer = reply_text(Bot(db, make_conversation(), llm).say("Куда переводить?"))
    assert answer == (
        f"Предоплата: кошелёк {WALLET} ({BANKS}). После перевода пришлите, пожалуйста, чек — скриншот перевода."
    )

    # a share of the total: "Предоплата 50% — 375 сомони"
    SettingsService(db).update({"prepayment_percent": 50})
    _, _, half = _confirmed_order(db, catalog, make_conversation)
    assert "Предоплата 50% — 375 сомони: переведите" in reply_text(half)


@pytest.mark.usefixtures("pickup_settings", "prepayment")
def test_the_account_may_be_a_card_number(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], media: MediaStorage
) -> None:
    """03 §3: a card instead of a wallet phone — the texts say "карту" and the receipt still matches."""
    SettingsService(db).update({"prepayment_wallet": CARD, "prepayment_wallet_banks": ""})
    llm = _pickup_llm(catalog["honey"])
    llm.receipt = receipt_reading(amount=750, recipient=CARD)
    bot, order, confirmed = _confirmed_order(db, catalog, make_conversation, llm=llm, media=media)

    assert reply_text(confirmed).endswith(
        f"Предоплата — 750 сомони: переведите, пожалуйста, на карту {CARD} и пришлите сюда чек — "
        "скриншот перевода. Как только менеджер увидит оплату, заказ пойдёт в работу."
    )

    outcome = _send_image(bot, media)

    assert outcome.reply.kind == ReplyKind.RECEIPT_RESULT and not outcome.handoff
    assert reply_text(outcome).startswith("Чек получили, спасибо! Перевод 750 сомони.")
    comments = [event.comment for event in order.events if event.comment]
    assert comments[-1].startswith("Чек из Instagram: 750.00 сомони, Alif, получатель совпадает")


@pytest.mark.usefixtures("pickup_settings", "prepayment")
def test_a_receipt_is_read_checked_and_left_to_the_operator(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], media: MediaStorage
) -> None:
    llm = _pickup_llm(catalog["honey"])
    llm.receipt = receipt_reading(amount=750)
    bot, order, _ = _confirmed_order(db, catalog, make_conversation, llm=llm, media=media)

    outcome = _send_image(bot, media)

    assert outcome.reply.kind == ReplyKind.RECEIPT_RESULT and not outcome.handoff
    assert reply_text(outcome) == (
        "Чек получили, спасибо! Перевод 750 сомони. Менеджер сверит поступление и подтвердит оплату — "
        f"заказ №{order.id} пойдёт в работу."
    )
    # the model saw the picture, not a text
    [call] = llm.receipt_calls
    [image, _] = call[0]["content"]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    # nothing is marked paid by the bot; the operator is alerted and reads the journal line
    db.refresh(order)
    assert order.payment_status == PaymentStatus.UNPAID
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention and bot.conversation.mode == ConversationMode.AI
    comments = [event.comment for event in order.events if event.comment]
    assert comments[-1].startswith("Чек из Instagram: 750.00 сомони, Alif, получатель совпадает")
    message = db.scalars(select(Message).where(Message.message_type == MessageType.IMAGE)).one()
    assert message.ai_processed and message.ai_payload["receipt"]["ok"] is True


@pytest.mark.usefixtures("pickup_settings", "prepayment")
@pytest.mark.parametrize(
    ("overrides", "expected_text"),
    [
        (
            {"amount": 500},
            "Чек получили: 500 сомони, а предоплата по заказу №{order_id} — 750 сомони. Переведите, пожалуйста, "
            f"ещё 250 сомони на кошелёк {WALLET} и пришлите чек.",
        ),
        (
            {"amount": 750, "recipient": "+992 93 111 22 33"},
            f"На чеке получатель +992 93 111 22 33, а перевод нужен на кошелёк {WALLET}. Проверьте, пожалуйста, "
            "перевод; если деньги ушли не туда, напишите нам — менеджер поможет.",
        ),
        (
            {"amount": 750, "status": "failed"},
            "Похоже, перевод не прошёл — на чеке нет отметки об успешной оплате. Попробуйте, пожалуйста, "
            "ещё раз и пришлите новый чек.",
        ),
        (
            {"amount": None},
            "Чек получили, но сумму на нём разобрать не удалось — менеджер проверит вручную и напишет вам.",
        ),
    ],
)
def test_receipt_problems_are_named_to_the_customer(
    db: Session,
    catalog: dict[str, Product],
    make_conversation: Callable[..., Conversation],
    media: MediaStorage,
    overrides: dict[str, Any],
    expected_text: str,
) -> None:
    llm = _pickup_llm(catalog["honey"])
    llm.receipt = receipt_reading(**overrides)
    bot, order, _ = _confirmed_order(db, catalog, make_conversation, llm=llm, media=media)

    outcome = _send_image(bot, media)

    assert outcome.reply.kind == ReplyKind.RECEIPT_RESULT and not outcome.handoff
    assert reply_text(outcome) == expected_text.format(order_id=order.id)
    db.refresh(order)
    assert order.payment_status == PaymentStatus.UNPAID
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention


@pytest.mark.usefixtures("pickup_settings", "prepayment")
def test_pictures_that_are_not_receipts_still_go_to_the_operator(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], media: MediaStorage
) -> None:
    llm = _pickup_llm(catalog["honey"])
    llm.receipt = receipt_reading(is_receipt=False, amount=None, confidence=0.9)
    bot, _, _ = _confirmed_order(db, catalog, make_conversation, llm=llm, media=media)

    outcome = _send_image(bot, media)

    assert outcome.handoff
    db.refresh(bot.conversation)
    assert bot.conversation.handoff_reason == REASON_IMAGE

    # the model could not read the picture at all: the operator gets it with the reason
    llm.receipt = LLMUnavailableError(reason="timeout")
    bot2, _, _ = _confirmed_order(db, catalog, make_conversation, llm=llm, media=media)
    failed = _send_image(bot2, media)
    assert failed.handoff
    db.refresh(bot2.conversation)
    assert bot2.conversation.handoff_reason == REASON_RECEIPT_UNREAD


@pytest.mark.usefixtures("pickup_settings")
def test_without_the_prepayment_policy_or_an_unpaid_order_an_image_is_just_an_image(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], media: MediaStorage
) -> None:
    llm = _pickup_llm(catalog["honey"])
    llm.receipt = receipt_reading(amount=750)
    bot, order, _ = _confirmed_order(db, catalog, make_conversation, llm=llm, media=media)

    assert _send_image(bot, media).handoff and llm.receipt_calls == []  # policy off: no reading at all

    SettingsService(db).update({"prepayment_enabled": True, "prepayment_wallet": WALLET})
    order.payment_status = PaymentStatus.PAID
    bot.conversation.mode = ConversationMode.AI  # "Вернуть боту" after the first handoff
    db.commit()
    assert _send_image(bot, media).handoff and llm.receipt_calls == []  # already paid: nothing to check


@pytest.mark.usefixtures("pickup_settings", "prepayment")
def test_auto_confirm_marks_the_order_paid_from_a_matching_receipt(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], media: MediaStorage
) -> None:
    SettingsService(db).update({"prepayment_auto_confirm": True})
    llm = _pickup_llm(catalog["honey"])
    llm.receipt = receipt_reading(amount=750)
    bot, order, _ = _confirmed_order(db, catalog, make_conversation, llm=llm, media=media)

    outcome = _send_image(bot, media)

    assert reply_text(outcome) == f"Спасибо, оплату 750 сомони получили ✅ Заказ №{order.id} в работе."
    db.refresh(order)
    assert order.payment_status == PaymentStatus.PAID and order.paid_amount == Decimal("750.00")
    [payment] = order.payments
    assert (payment.method, payment.note) == (PaymentMethod.TRANSFER, AUTO_PAYMENT_NOTE)
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention  # still shown to the operator for a bank-app check

    # a receipt that does not fit never marks anything, even with auto-confirm on
    llm.receipt = receipt_reading(amount=500)
    bot2, order2, _ = _confirmed_order(db, catalog, make_conversation, llm=llm, media=media)
    _send_image(bot2, media)
    db.refresh(order2)
    assert order2.payment_status == PaymentStatus.UNPAID


@pytest.mark.usefixtures("pickup_settings", "prepayment")
def test_saying_paid_asks_for_the_receipt_without_the_model(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    bot, order, _ = _confirmed_order(db, catalog, make_conversation)
    calls = len(bot.llm.json_calls)

    outcome = bot.say("Оплатил, проверьте")

    assert outcome.reply.kind == ReplyKind.ASK_RECEIPT and len(bot.llm.json_calls) == calls
    assert reply_text(outcome) == (
        f"Спасибо! Пришлите, пожалуйста, чек — скриншот перевода на кошелёк {WALLET}, — и менеджер подтвердит "
        f"оплату по заказу №{order.id}."
    )

    # without an order awaiting a prepayment the words go to the model as usual
    llm = ScriptedLLM(default=understanding(intent="OTHER", other_topic="question"))
    other = Bot(db, make_conversation(), llm).say("Оплатил")
    assert other.reply.kind == ReplyKind.NEED_MANAGER and len(llm.json_calls) == 1
