"""Scenario tests for the second round of bot fixes (16.09.2026, docs/PROGRESS.md):

small talk and off-topic routing, past dates and the earliest slot, the owner's phone examples, address flows
that never block the order (03 §1.3), Tajik stickiness, address parsing and the geocoder ladder.
Deterministic: ``ScriptedLLM`` and ``FakeGeocoder`` only.
"""

# ruff: noqa: F811 — pytest fixtures imported from test_dialog_service are reused as parameter names

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.ai.responder import ReplyKind
from app.ai.small_talk import Greeting, SmallTalk, detect_greeting, detect_small_talk
from app.core import time as app_time
from app.integrations.maps.address import candidate_matches, geocode_queries, parse_address
from app.integrations.maps.types import GeoCandidate, GeocodeResult, failed_result, rank_candidates
from app.models.conversation import Conversation
from app.models.enums import ConversationMode, DeliveryType, GeocodeStatus, Language, OrderStatus
from app.models.product import Product
from app.services.dialog_state import AWAITING_MISSING_FIELDS
from app.services.geocoding_service import GeocodingService
from app.services.location_service import LocationService
from tests.bot_fakes import (
    CITY_LAT,
    CITY_LNG,
    FakeGeocoder,
    ScriptedLLM,
    ambiguous,
    future_day,
    item,
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
    assert reply_text(outcome) == "Сколько штук нужно: Ягодные синнамоны, Фисташковые синнамоны?"


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
