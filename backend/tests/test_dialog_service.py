"""DialogService (05-ai.md §5, §8): deterministic decisions around a fake LLM.

Replies are template texts (``ScriptedLLM`` refuses to word them), so the assertions pin the exact
behaviour: which question is asked, when the summary is shown, when "Да" confirms.
"""

from collections.abc import Callable
from datetime import time
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai.llm_client import LLMRefusalError, LLMUnavailableError
from app.ai.responder import ReplyKind
from app.core.time import now_utc
from app.models import Customer, Message, Order, Product
from app.models.conversation import Conversation
from app.models.enums import (
    ConversationMode,
    DeliveryType,
    GeocodeStatus,
    LocationSource,
    MessageDirection,
    MessageSender,
    MessageType,
    OrderStatus,
)
from app.models.faq import FaqItem
from app.services.conversation_service import ConversationService
from app.services.dialog_service import DialogOutcome, DialogService
from app.services.dialog_state import (
    AWAITING_ADDRESS_CHOICE,
    AWAITING_CANCEL_CONFIRMATION,
    AWAITING_CONFIRMATION,
    AWAITING_MISSING_FIELDS,
    DialogState,
)
from app.services.location_service import LocationService
from app.services.order_validator import TOO_SOON
from app.services.settings_service import SettingsService
from tests.bot_fakes import (
    CITY_LAT,
    CITY_LNG,
    FakeGeocoder,
    ScriptedLLM,
    ambiguous,
    future_day,
    item,
    understanding,
)

DAY = future_day(3)
DAY_TEXT = f"{DAY.day:02d}.{DAY.month:02d}.{DAY.year}"


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def catalog(make_product: Callable[..., Product]) -> dict[str, Product]:
    return {
        "velvet": make_product("Красный бархат", "750", aliases=["торт красный бархат"]),
        "honey": make_product("Медовик", "750", aliases=["торт медовик", "торти асал"]),
        "eclair": make_product("Эклер", "50"),
    }


@pytest.fixture
def pickup_settings(db: Session) -> None:
    SettingsService(db).update({"pickup_address": "ул. Айни, 12"})


@pytest.fixture
def make_conversation(db: Session, make_customer: Callable[..., Customer]) -> Callable[..., Conversation]:
    counter = iter(range(1, 1000))

    def _make(name: str | None = "Алия", **customer_fields: Any) -> Conversation:
        index = next(counter)
        customer = make_customer(name, instagram_user_id=f"igsid-{index}", **customer_fields)
        conversation = ConversationService(db).get_or_create_instagram(customer, f"acc:igsid-{index}")
        conversation.last_customer_message_at = now_utc()
        db.commit()
        return conversation

    return _make


def incoming(db: Session, conversation: Conversation, text: str | None, **fields: Any) -> Message:
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.INCOMING,
        message_type=fields.pop("message_type", MessageType.TEXT),
        sender=MessageSender.CUSTOMER,
        text=text,
        **fields,
    )
    db.add(message)
    db.commit()
    return message


class Bot:
    """One conversation driven message by message."""

    def __init__(self, db: Session, conversation: Conversation, llm: ScriptedLLM, geocoder: FakeGeocoder | None = None):
        self.db = db
        self.conversation = conversation
        self.llm = llm
        self.service = DialogService(db, llm=llm, geocoder=geocoder or FakeGeocoder())

    def say(self, text: str | None, **fields: Any) -> DialogOutcome:
        message = incoming(self.db, self.conversation, text, **fields)
        return self.service.handle_incoming(self.conversation, message)

    @property
    def state(self) -> DialogState:
        self.db.refresh(self.conversation)
        return DialogState.from_json(self.conversation.state)

    def draft(self) -> Order:
        order = self.db.get(Order, self.state.draft_order_id)
        assert order is not None
        return order


def reply_text(outcome: DialogOutcome) -> str:
    assert outcome.reply is not None, outcome.actions
    return outcome.reply.text


# --------------------------------------------------------------------------- SPEC §11–§13 scenario


def test_spec_order_scenario_from_greeting_to_confirmation(
    db: Session, catalog: dict[str, Product], pickup_settings: None, make_conversation: Callable[..., Conversation]
) -> None:
    velvet, honey = catalog["velvet"], catalog["honey"]
    llm = ScriptedLLM(
        {
            "Здравствуйте, хочу 2 торта": understanding(
                intent="CREATE_ORDER",
                secondary_intents=["GREETING"],
                entities={"items": [item("торт", 2)], "items_mode": "add", "delivery_date": DAY.isoformat()},
            ),
            "Красный бархат и медовик": understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("Красный бархат", None, velvet.id), item("медовик", None, honey.id)],
                    "items_mode": "add",
                },
            ),
            "В 18:00, самовывоз": understanding(
                intent="CREATE_ORDER", entities={"delivery_time": "18:00", "delivery_type": "PICKUP"}
            ),
            "90 123 45 67": understanding(intent="CREATE_ORDER", entities={"phone": "90 123 45 67"}),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    first = bot.say("Здравствуйте, хочу 2 торта")
    assert first.reply.kind == ReplyKind.ASK_MISSING
    assert reply_text(first) == "Подскажите, пожалуйста, какие именно? Сейчас есть: Красный бархат, Медовик."
    order = bot.draft()
    assert order.status == OrderStatus.NEW and order.items == [] and order.delivery_date == DAY
    assert bot.state.pending_items[0]["kind"] == "generic" and bot.state.pending_items[0]["quantity"] == 2

    second = bot.say("Красный бархат и медовик")
    assert reply_text(second) == "Уточните, пожалуйста:\n1. К какому времени?\n2. Это будет доставка или самовывоз?"
    order = bot.draft()
    assert sorted((line.product_name, line.quantity) for line in order.items) == [("Красный бархат", 1), ("Медовик", 1)]
    assert order.total_amount == Decimal("1500.00")
    assert bot.state.pending_items == [] and bot.state.awaiting == AWAITING_MISSING_FIELDS

    third = bot.say("В 18:00, самовывоз")
    assert reply_text(third) == "Напишите, пожалуйста, номер телефона для связи."

    summary = bot.say("90 123 45 67")
    assert summary.reply.kind == ReplyKind.ORDER_SUMMARY
    assert reply_text(summary) == (
        "Проверьте, пожалуйста, заказ:\n\n"
        "Красный бархат — 1 шт.\nМедовик — 1 шт.\n\n"
        f"Дата: {DAY_TEXT}\nВремя: 18:00\nСамовывоз: ул. Айни, 12\nПолучатель: Алия, +992901234567\n\n"
        "Итого: 1 500 сомони.\n\n"
        "Всё верно? Напишите «Да», чтобы подтвердить."
    )
    order = bot.draft()
    assert order.status == OrderStatus.WAITING_CONFIRMATION
    assert bot.state.awaiting == AWAITING_CONFIRMATION and bot.state.summary_hash

    calls_before = len(llm.json_calls)
    hesitant = bot.say("ну вроде")
    assert hesitant.reply.kind == ReplyKind.CONFIRMATION_REPEAT
    assert bot.draft().status == OrderStatus.WAITING_CONFIRMATION
    assert len(llm.json_calls) == calls_before  # the classifier decides, not the model

    confirmed = bot.say("Да")
    assert confirmed.reply.kind == ReplyKind.ORDER_CONFIRMED
    db.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and order.confirmed_at is not None
    assert reply_text(confirmed) == (
        f"Спасибо! Заказ №{order.id} подтверждён ✅\n"
        f"Заказ можно будет забрать {DAY_TEXT} в 18:00.\nИтого: 1 500 сомони."
    )
    assert bot.state.draft_order_id is None and bot.state.awaiting is None
    assert len(llm.json_calls) == calls_before
    assert "order_confirmed" in confirmed.actions


@pytest.mark.parametrize("text", ["ну вроде", "наверное", "может быть", "посмотрим", "думаю да", "ок", "👍"])
def test_hedged_answers_never_confirm(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], text: str
) -> None:
    bot, order = _bot_at_summary(db, catalog, make_conversation)
    outcome = bot.say(text)
    assert outcome.reply.kind == ReplyKind.CONFIRMATION_REPEAT
    db.refresh(order)
    assert order.status == OrderStatus.WAITING_CONFIRMATION


def test_yes_after_the_order_changed_shows_the_summary_again(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    bot, order = _bot_at_summary(db, catalog, make_conversation)
    order.comment = "Надпись: С днём рождения"  # staff edited the draft after the summary was sent
    db.commit()

    outcome = bot.say("Да")

    assert outcome.reply.kind == ReplyKind.ORDER_SUMMARY
    db.refresh(order)
    assert order.status == OrderStatus.WAITING_CONFIRMATION
    assert "Комментарий: Надпись: С днём рождения" in reply_text(outcome)
    assert bot.say("Да").reply.kind == ReplyKind.ORDER_CONFIRMED


def test_no_asks_what_to_change_and_a_change_is_applied_with_a_new_summary(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    bot, order = _bot_at_summary(db, catalog, make_conversation)
    assert bot.say("Нет").reply.kind == ReplyKind.ASK_WHAT_TO_CHANGE
    bot.llm.script["Время другое, в 19:00"] = understanding(intent="CHANGE_ORDER", entities={"delivery_time": "19:00"})

    outcome = bot.say("Время другое, в 19:00")

    assert outcome.reply.kind == ReplyKind.ORDER_SUMMARY
    assert "Время: 19:00" in reply_text(outcome)
    db.refresh(order)
    assert order.delivery_time == time(19, 0) and order.status == OrderStatus.WAITING_CONFIRMATION


def test_cancel_word_at_the_summary_asks_to_confirm_the_cancellation(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    bot, order = _bot_at_summary(db, catalog, make_conversation)
    bot.llm.script["Отмените заказ"] = understanding(intent="CANCEL_ORDER")

    ask = bot.say("Отмените заказ")
    assert ask.reply.kind == ReplyKind.CANCEL_CONFIRM
    assert reply_text(ask) == f"Отменить заказ №{order.id}? Напишите «Да», чтобы отменить."
    assert bot.state.awaiting == AWAITING_CANCEL_CONFIRMATION

    done = bot.say("Да, отмените")
    assert done.reply.kind == ReplyKind.ORDER_CANCELLED
    db.refresh(order)
    assert order.status == OrderStatus.CANCELLED
    assert bot.state.draft_order_id is None and bot.state.awaiting is None


def test_cancellation_can_be_declined(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], make_order
) -> None:
    conversation = make_conversation()
    placed = make_order(customer=conversation.customer, status=OrderStatus.CONFIRMED)
    llm = ScriptedLLM({"Хочу отменить заказ": understanding(intent="CANCEL_ORDER")})
    bot = Bot(db, conversation, llm)

    assert bot.say("Хочу отменить заказ").reply.kind == ReplyKind.CANCEL_CONFIRM
    kept = bot.say("Нет, не надо")
    assert kept.reply.kind == ReplyKind.CANCEL_KEPT
    db.refresh(placed)
    assert placed.status == OrderStatus.CONFIRMED and bot.state.awaiting is None


def test_order_in_production_is_not_cancelled_by_the_bot(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], make_order
) -> None:
    conversation = make_conversation()
    placed = make_order(customer=conversation.customer, status=OrderStatus.PREPARING)
    bot = Bot(db, conversation, ScriptedLLM({"Отмените мой заказ": understanding(intent="CANCEL_ORDER")}))

    outcome = bot.say("Отмените мой заказ")

    assert outcome.handoff and outcome.reply.kind == ReplyKind.HANDOFF
    assert reply_text(outcome).startswith(f"Заказ №{placed.id} уже в работе")
    db.refresh(conversation)
    db.refresh(placed)
    assert conversation.mode == ConversationMode.HUMAN_HANDOFF and conversation.needs_attention
    assert placed.status == OrderStatus.PREPARING
    assert any(event.comment and event.comment.startswith("Клиент просит изменить") for event in placed.events)


def test_change_of_a_confirmed_order_is_journaled_and_handed_over(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], make_order
) -> None:
    conversation = make_conversation()
    placed = make_order(customer=conversation.customer, status=OrderStatus.CONFIRMED)
    text = "Перенесите мой заказ на 20:00"
    bot = Bot(
        db, conversation, ScriptedLLM({text: understanding(intent="CHANGE_ORDER", entities={"delivery_time": "20:00"})})
    )

    outcome = bot.say(text)

    assert outcome.handoff
    db.refresh(placed)
    assert placed.delivery_time == time(12, 0)  # unchanged: staff decide
    assert [event.comment for event in placed.events if event.comment] == [f"Клиент просит изменить: {text}"]


# --------------------------------------------------------------------------- items


def test_quantity_is_asked_and_a_bare_number_answers_it_without_the_llm(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {"Хочу медовик": understanding(intent="CREATE_ORDER", entities={"items": [item("медовик", None, honey.id)]})}
    )
    bot = Bot(db, make_conversation(), llm)

    ask = bot.say("Хочу медовик")
    assert reply_text(ask) == "Сколько штук нужно: Медовик?"

    answered = bot.say("2")
    assert len(llm.json_calls) == 1
    assert [(line.product_name, line.quantity) for line in bot.draft().items] == [("Медовик", 2)]
    assert reply_text(answered) == "Уточните, пожалуйста:\n1. На какую дату нужен заказ?\n2. К какому времени?"


def test_unknown_product_is_reported_with_the_active_catalog(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    llm = ScriptedLLM(
        {"Хочу наполеон": understanding(intent="CREATE_ORDER", entities={"items": [item("наполеон", 1)]})}
    )
    bot = Bot(db, make_conversation(), llm)

    outcome = bot.say("Хочу наполеон")

    assert outcome.reply.kind == ReplyKind.ASK_MISSING
    assert reply_text(outcome).startswith(
        "К сожалению, «наполеон» нет в нашем каталоге.\nСейчас можно заказать: Красный бархат, Медовик, Эклер."
    )
    assert bot.draft().items == []


def test_invented_product_id_is_ignored_and_the_text_is_matched(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {"2 медовика": understanding(intent="CREATE_ORDER", entities={"items": [item("медовик", 2, 99_999)]})}
    )
    bot = Bot(db, make_conversation(), llm)

    bot.say("2 медовика")

    assert [(line.product_id, line.quantity) for line in bot.draft().items] == [(honey.id, 2)]


def test_inactive_product_cannot_be_ordered(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], make_product
) -> None:
    hidden = make_product("Чизкейк", "300", is_active=False)
    llm = ScriptedLLM(
        {"Чизкейк": understanding(intent="CREATE_ORDER", entities={"items": [item("Чизкейк", 1, hidden.id)]})}
    )
    bot = Bot(db, make_conversation(), llm)

    outcome = bot.say("Чизкейк")

    assert "«Чизкейк» нет в нашем каталоге" in reply_text(outcome)
    assert bot.draft().items == []


# --------------------------------------------------------------------------- date and time


def test_slot_earlier_than_the_lead_time_is_not_written(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    today = future_day(0).isoformat()
    llm = ScriptedLLM(
        {
            "Медовик на сегодня": understanding(
                intent="CREATE_ORDER",
                entities={"items": [item("медовик", 1, honey.id)], "delivery_date": today, "delivery_time": "23:30"},
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)

    outcome = bot.say("Медовик на сегодня")

    order = bot.draft()
    assert order.delivery_date is None and order.delivery_time is None
    assert reply_text(outcome).startswith("Заказы принимаем не позднее чем за 24 ч.")
    assert outcome.reply.kind == ReplyKind.ASK_MISSING
    assert TOO_SOON == "delivery_too_soon"


# --------------------------------------------------------------------------- delivery address


def _delivery_llm(honey: Product, address: str = "Сино, 82 мкр, дом 5") -> ScriptedLLM:
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


def test_delivery_address_geocoded_leads_to_the_summary_with_the_customer_as_recipient(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    geocoder = FakeGeocoder()
    bot = Bot(db, make_conversation(), _delivery_llm(catalog["honey"]), geocoder)

    ask = bot.say("Медовик")
    assert reply_text(ask) == ("Напишите, пожалуйста, адрес доставки: район, улица, дом, квартира и ориентир.")

    summary = bot.say("Сино, 82 мкр, дом 5")

    assert geocoder.queries, "the address must be geocoded synchronously"
    assert summary.reply.kind == ReplyKind.ORDER_SUMMARY
    text = reply_text(summary)
    assert "Доставка: да\nАдрес: Сино, 82 мкр, дом 5\nПолучатель: Алия, +992901234567" in text
    order = bot.draft()
    assert order.delivery.geocode_status == GeocodeStatus.OK
    assert order.delivery.recipient_name == "Алия"
    assert order.delivery.location_source == LocationSource.GEOCODER


def test_ambiguous_address_offers_variants_and_a_map_link_and_a_number_picks_one(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    geocoder = FakeGeocoder(ambiguous("Сино, 82 мкр, 5", "Сино, 82 мкр, 5/1"))
    bot = Bot(db, make_conversation(), _delivery_llm(catalog["honey"]), geocoder)
    bot.say("Медовик")

    clarify = bot.say("Сино, 82 мкр, дом 5")

    assert clarify.reply.kind == ReplyKind.ADDRESS_CLARIFY
    order = bot.draft()
    link = LocationService(db).link_url(order.delivery.location_requests[0])
    assert reply_text(clarify) == (
        "Уточните, пожалуйста, адрес. Возможно, это один из вариантов:\n"
        "1. Сино, 82 мкр, 5\n2. Сино, 82 мкр, 5/1\n"
        f"Напишите номер варианта или отметьте точку на карте: {link}\n\n"
        "Если неудобно открывать карту, напишите «дальше» — оформим заказ по адресу как есть, "
        "а курьер уточнит по телефону."
    )
    assert bot.state.awaiting == AWAITING_ADDRESS_CHOICE
    assert "location" not in bot.state.missing_fields  # 03 §1.3: the point is asked for, not a blocker

    chosen = bot.say("2")

    assert chosen.reply.kind == ReplyKind.ORDER_SUMMARY
    order = bot.draft()
    assert order.delivery.address_formatted == "Сино, 82 мкр, 5/1"
    assert float(order.delivery.latitude) == pytest.approx(CITY_LAT + 0.001)
    assert bot.state.address_candidates == []


def test_map_pin_continues_the_dialog_with_the_summary(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    geocoder = FakeGeocoder(ambiguous())  # nothing usable: NOT really found
    bot = Bot(db, make_conversation(), _delivery_llm(catalog["honey"]), geocoder)
    bot.say("Медовик")
    clarify = bot.say("Сино, 82 мкр, дом 5")
    assert clarify.reply.kind == ReplyKind.ADDRESS_CLARIFY
    order = bot.draft()
    token = order.delivery.location_requests[0].token

    LocationService(db).submit(token, CITY_LAT, CITY_LNG)  # the customer placed the pin
    db.refresh(order)
    outcome = bot.service.continue_after_location(order)

    assert outcome.reply is not None and outcome.reply.kind == ReplyKind.ORDER_SUMMARY
    assert order.delivery.location_source == LocationSource.CUSTOMER_PIN


def test_failed_geocoding_alerts_the_operator(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    from app.integrations.maps.types import failed_result

    bot = Bot(db, make_conversation(), _delivery_llm(catalog["honey"]), FakeGeocoder(failed_result("fake", "down")))
    bot.say("Медовик")

    outcome = bot.say("Сино, 82 мкр, дом 5")

    assert outcome.reply.kind == ReplyKind.ADDRESS_CLARIFY
    assert "отметьте точку на карте: http" in reply_text(outcome)
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention


# --------------------------------------------------------------------------- handoff and failures


@pytest.mark.parametrize("text", ["Позовите оператора", "Хочу поговорить с человеком", "Мне нужен менеджер"])
def test_operator_request_hands_over_without_the_llm(
    db: Session, make_conversation: Callable[..., Conversation], text: str
) -> None:
    llm = ScriptedLLM()
    bot = Bot(db, make_conversation(), llm)

    outcome = bot.say(text)

    assert outcome.handoff and reply_text(outcome) == "Передаю диалог менеджеру, он скоро ответит."
    assert llm.json_calls == []
    db.refresh(bot.conversation)
    assert bot.conversation.mode == ConversationMode.HUMAN_HANDOFF
    assert bot.conversation.needs_attention and bot.conversation.handoff_at is not None


@pytest.mark.parametrize("error", [LLMUnavailableError(reason="timeout"), LLMRefusalError()])
def test_llm_failure_hands_over(db: Session, make_conversation: Callable[..., Conversation], error: Exception) -> None:
    bot = Bot(db, make_conversation(), ScriptedLLM(default=error))

    outcome = bot.say("Здравствуйте")

    assert outcome.handoff
    # No "уточнить эту информацию": an AI outage says nothing about the customer's question.
    assert reply_text(outcome) == "Передаю диалог менеджеру, он скоро ответит."
    db.refresh(bot.conversation)
    assert bot.conversation.failed_ai_attempts == 1
    assert bot.conversation.handoff_reason == "AI-ассистент недоступен"


def test_llm_not_configured_hands_over(db: Session, make_conversation: Callable[..., Conversation]) -> None:
    from app.core.exceptions import IntegrationNotConfiguredError

    def factory(settings: Any) -> Any:
        raise IntegrationNotConfiguredError("no key")

    conversation = make_conversation()
    service = DialogService(db, llm_factory=factory)

    outcome = service.handle_incoming(conversation, incoming(db, conversation, "Здравствуйте"))

    assert outcome.handoff


def test_two_misunderstood_messages_hand_over(db: Session, make_conversation: Callable[..., Conversation]) -> None:
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="OTHER")))

    first = bot.say("ывап")
    assert first.reply.kind == ReplyKind.CLARIFY and not first.handoff
    second = bot.say("фыва")

    assert second.handoff
    db.refresh(bot.conversation)
    assert bot.conversation.mode == ConversationMode.HUMAN_HANDOFF


def test_complaint_hands_over_with_an_apology(db: Session, make_conversation: Callable[..., Conversation]) -> None:
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="COMPLAINT")))

    outcome = bot.say("Торт был чёрствый")

    assert reply_text(outcome) == "Извините за неудобства. Передаю диалог менеджеру, он скоро ответит."


def test_bot_is_silent_in_human_handoff_and_when_ai_is_off(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    llm = ScriptedLLM(default=understanding(intent="GREETING"))
    conversation = make_conversation()
    conversation.mode = ConversationMode.HUMAN_HANDOFF
    db.commit()
    bot = Bot(db, conversation, llm)

    silent = bot.say("Алло?")
    assert silent.reply is None and silent.actions == ["human_handoff"]
    db.refresh(conversation)
    assert conversation.needs_attention

    conversation.mode, conversation.needs_attention = ConversationMode.AI, False
    db.commit()
    SettingsService(db).update({"ai_enabled": False})
    off = bot.say("Алло?")
    assert off.reply is None and off.actions == ["ai_disabled"]
    assert llm.json_calls == []


def test_image_without_text_goes_to_the_operator(db: Session, make_conversation: Callable[..., Conversation]) -> None:
    bot = Bot(db, make_conversation(), ScriptedLLM())

    outcome = bot.say(None, message_type=MessageType.IMAGE)

    assert outcome.handoff
    db.refresh(bot.conversation)
    assert bot.conversation.handoff_reason == "Клиент прислал изображение"


# --------------------------------------------------------------------------- informational intents


def test_faq_answer_comes_from_the_database(db: Session, make_conversation: Callable[..., Conversation]) -> None:
    faq = FaqItem(
        question="Есть ли доставка?", answer="Да, доставляем по Душанбе.", answer_tg="Ҳа, дар Душанбе мерасонем."
    )
    db.add(faq)
    db.commit()
    llm = ScriptedLLM(
        {
            "Есть доставка?": understanding(intent="FAQ", faq_ids=[faq.id]),
            "Доставка ҳаст?": understanding(intent="FAQ", language="tg", faq_ids=[faq.id]),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    assert reply_text(bot.say("Есть доставка?")) == "Да, доставляем по Душанбе."
    assert reply_text(bot.say("Доставка ҳаст?")) == "Ҳа, дар Душанбе мерасонем."


def test_faq_found_by_admin_keywords_when_the_model_gives_no_id(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    db.add(FaqItem(question="Можно ли индивидуальный дизайн?", answer="Да, по фото.", keywords=["дизайн"]))
    db.commit()
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="FAQ")))

    assert reply_text(bot.say("А дизайн свой можно?")) == "Да, по фото."


def test_faq_is_answered_whatever_intent_the_model_chose(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    fresh = FaqItem(question="Насколько свежая выпечка?", answer="Печём в день выдачи.", keywords=["свежие"])
    db.add(fresh)
    db.commit()
    llm = ScriptedLLM(
        {
            # the model picked PRODUCT_QUERY but also the FAQ id: the catalog must not be listed
            "Торты свежие?": understanding(intent="PRODUCT_QUERY", faq_ids=[fresh.id]),
            # OTHER without an id: the admin's keyword still finds the entry (no failed attempt)
            "А у вас всё свежие продукты?": understanding(intent="OTHER"),
            "Здравствуйте, выпечка свежая?": understanding(intent="GREETING", faq_ids=[fresh.id]),
            # a named product keeps the product answer
            "Эклер свежие?": understanding(intent="PRODUCT_QUERY", product_ids_asked=[catalog["eclair"].id]),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    assert reply_text(bot.say("Торты свежие?")) == "Печём в день выдачи."
    assert reply_text(bot.say("А у вас всё свежие продукты?")) == "Печём в день выдачи."
    db.refresh(bot.conversation)
    assert bot.conversation.failed_ai_attempts == 0
    assert reply_text(bot.say("Здравствуйте, выпечка свежая?")) == "Печём в день выдачи."
    assert reply_text(bot.say("Эклер свежие?")) == "Эклер — 50 сомони / шт."


def test_order_message_asking_the_price_gets_prices_and_the_total(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey, eclair = catalog["honey"], catalog["eclair"]
    llm = ScriptedLLM(
        {
            "Хочу медовик и 5 эклеров, сколько будет стоить?": understanding(
                intent="PRODUCT_QUERY",
                secondary_intents=["CREATE_ORDER"],
                product_ids_asked=[honey.id, eclair.id],
                entities={
                    "items": [item("медовик", None, honey.id), item("эклеров", 5, eclair.id)],
                    "items_mode": "add",
                },
            ),
            "1 медовик, сколько всего?": understanding(
                intent="CREATE_ORDER",
                secondary_intents=["PRODUCT_QUERY"],
                entities={"items": [item("медовик", 1, honey.id)], "items_mode": "add"},
            ),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    first = reply_text(bot.say("Хочу медовик и 5 эклеров, сколько будет стоить?"))
    assert first.startswith("Медовик — 750 сомони / шт.\nЭклер — 50 сомони / шт.\n\n")
    assert "Итого" not in first  # the quantity of the cake is still unknown
    assert "Сколько штук нужно: Медовик?" in first

    second = reply_text(bot.say("1 медовик, сколько всего?"))
    assert "Итого: 1 000 сомони." in second  # 750 + 5 × 50, from the database


def test_greeting_does_not_use_faq_keywords(db: Session, make_conversation: Callable[..., Conversation]) -> None:
    db.add(FaqItem(question="Режим работы?", answer="С 9 до 20.", keywords=["здравствуйте"]))
    db.commit()
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="GREETING")))

    assert reply_text(bot.say("Здравствуйте")) != "С 9 до 20."


def test_unanswerable_question_needs_a_manager(db: Session, make_conversation: Callable[..., Conversation]) -> None:
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="FAQ")))

    outcome = bot.say("Вы работаете в Рамадан?")

    assert reply_text(outcome) == "Мне нужно уточнить эту информацию у менеджера."
    db.refresh(bot.conversation)
    assert bot.conversation.needs_attention and bot.conversation.mode == ConversationMode.AI


def test_product_query_lists_prices_from_the_database(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    bot = Bot(
        db,
        make_conversation(),
        ScriptedLLM(default=understanding(intent="PRODUCT_QUERY", product_ids_asked=[catalog["eclair"].id])),
    )

    assert reply_text(bot.say("Сколько стоит эклер?")) == "Эклер — 50 сомони / шт."


def test_order_status_lists_placed_orders_only(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], make_order
) -> None:
    conversation = make_conversation()
    placed = make_order(customer=conversation.customer, status=OrderStatus.READY, paid_amount="100")
    make_order(customer=conversation.customer, status=OrderStatus.NEW)
    bot = Bot(db, conversation, ScriptedLLM(default=understanding(intent="ORDER_STATUS")))

    text = reply_text(bot.say("Где мой заказ?"))

    assert text.startswith(f"Заказ №{placed.id}: готов")
    assert "оплата: оплачен" in text
    assert text.count("Заказ №") == 1


def test_delivery_query_without_configured_texts_needs_a_manager(
    db: Session, make_conversation: Callable[..., Conversation]
) -> None:
    bot = Bot(db, make_conversation(), ScriptedLLM(default=understanding(intent="DELIVERY_QUERY")))
    assert bot.say("Сколько стоит доставка?").reply.kind == ReplyKind.NEED_MANAGER

    SettingsService(db).update({"delivery_info_text": "Доставка по городу — 20 сомони."})
    assert reply_text(bot.say("А доставка?")) == "Доставка по городу — 20 сомони."


# --------------------------------------------------------------------------- language


def test_tajik_customer_gets_tajik_templates(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    text = "Салом, фардо соати 18 медовик мехоҳам"
    llm = ScriptedLLM(
        {
            text: understanding(
                language="tg",
                intent="CREATE_ORDER",
                secondary_intents=["GREETING"],
                entities={
                    "items": [item("медовик", None, honey.id)],
                    "delivery_date": DAY.isoformat(),
                    "delivery_time": "18:00",
                },
            )
        }
    )
    conversation = make_conversation()
    bot = Bot(db, conversation, llm)

    ask = bot.say(text)

    assert reply_text(ask) == "Чанд дона лозим аст: Медовик?"
    db.refresh(conversation.customer)
    assert conversation.customer.language.value == "tg"
    next_question = bot.say("1")
    assert reply_text(next_question) == (
        "Лутфан, аниқ кунед:\n1. Расонидан лозим аст ё худатон мегиред?\n"
        "2. Лутфан, рақами телефонатонро барои тамос нависед."
    )


def test_tools_are_offered_read_only(db: Session, make_conversation: Callable[..., Conversation]) -> None:
    llm = ScriptedLLM(default=understanding(intent="GREETING"))
    bot = Bot(db, make_conversation(), llm)

    bot.say("Салом")

    call = llm.json_calls[0]
    names = {tool["name"] for tool in call["tools"]}
    assert "get_products" in names and "confirm_order" not in names and "create_order_draft" not in names
    assert call["tool_executor"]("confirm_order", {"order_id": 1}) == {
        "error": "not_allowed",
        "detail": "the backend performs this action itself",
    }


# --------------------------------------------------------------------------- helpers


def _bot_at_summary(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> tuple[Bot, Order]:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {
            "Медовик": understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовик", 1, honey.id)],
                    "delivery_date": DAY.isoformat(),
                    "delivery_time": "18:00",
                    "delivery_type": "PICKUP",
                    "phone": "901234567",
                },
            )
        }
    )
    bot = Bot(db, make_conversation(), llm)
    outcome = bot.say("Медовик")
    assert outcome.reply.kind == ReplyKind.ORDER_SUMMARY, reply_text(outcome)
    order = bot.draft()
    assert order.status == OrderStatus.WAITING_CONFIRMATION
    assert order.delivery_type == DeliveryType.PICKUP
    return bot, order


# --------------------------------------------------------------------------- further branches


def test_items_can_be_removed_and_replaced(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    velvet, honey, eclair = catalog["velvet"], catalog["honey"], catalog["eclair"]
    llm = ScriptedLLM(
        {
            "Медовик и 3 эклера": understanding(
                intent="CREATE_ORDER",
                entities={"items": [item("медовик", 1, honey.id), item("эклер", 3, eclair.id)]},
            ),
            "Уберите эклеры": understanding(
                intent="CHANGE_ORDER", entities={"items": [item("эклер", None, eclair.id)], "items_mode": "remove"}
            ),
            "Вместо этого красный бархат": understanding(
                intent="CHANGE_ORDER",
                entities={"items": [item("красный бархат", None, velvet.id)], "items_mode": "replace"},
            ),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    bot.say("Медовик и 3 эклера")
    assert sorted((line.product_name, line.quantity) for line in bot.draft().items) == [("Медовик", 1), ("Эклер", 3)]
    bot.say("Уберите эклеры")
    assert [(line.product_name, line.quantity) for line in bot.draft().items] == [("Медовик", 1)]
    bot.say("Вместо этого красный бархат")
    order = bot.draft()
    assert [(line.product_name, line.quantity) for line in order.items] == [("Красный бархат", 1)]
    assert order.total_amount == Decimal("750.00")


def test_ambiguous_product_asks_which_one(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], make_product
) -> None:
    classic = make_product("Наполеон классический", "400")
    royal = make_product("Наполеон королевский", "600")
    llm = ScriptedLLM(
        {
            "Наполеон": understanding(intent="CREATE_ORDER", entities={"items": [item("наполеон", 1)]}),
            "Королевский": understanding(
                intent="CREATE_ORDER", entities={"items": [item("Наполеон королевский", None, royal.id)]}
            ),
        }
    )
    bot = Bot(db, make_conversation(), llm)

    ask = bot.say("Наполеон")

    text = reply_text(ask)
    assert text.startswith("Уточните, пожалуйста, какой именно товар вы имели в виду:")
    assert classic.name in text and royal.name in text
    assert bot.state.pending_items[0]["kind"] == "ambiguous"

    bot.say("Королевский")
    assert [(line.product_name, line.quantity) for line in bot.draft().items] == [("Наполеон королевский", 1)]
    assert bot.state.pending_items == []


def test_yes_is_refused_when_the_slot_became_too_soon(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    bot, order = _bot_at_summary(db, catalog, make_conversation)
    SettingsService(db).update({"min_lead_time_hours": 24 * 30})  # the bakery changed its rules meanwhile

    outcome = bot.say("Да")

    assert outcome.reply.kind == ReplyKind.ASK_MISSING
    assert reply_text(outcome).startswith("Заказы принимаем не позднее чем за 720 ч.")
    db.refresh(order)
    assert order.status == OrderStatus.NEW and order.delivery_date is None and order.delivery_time is None


def test_question_while_the_summary_waits_reminds_to_confirm(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    bot, order = _bot_at_summary(db, catalog, make_conversation)
    SettingsService(db).update({"payment_methods_text": "Наличными или переводом на карту."})
    bot.llm.script["А как оплатить?"] = understanding(intent="PAYMENT_QUERY")

    outcome = bot.say("А как оплатить?")

    assert reply_text(outcome) == (
        f"Наличными или переводом на карту.\n\nЧтобы подтвердить заказ №{order.id}, напишите «Да»."
    )
    assert bot.state.awaiting == AWAITING_CONFIRMATION
    assert bot.say("Да").reply.kind == ReplyKind.ORDER_CONFIRMED


def test_greeting_during_an_open_draft_repeats_the_questions(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {
            "2 медовика": understanding(intent="CREATE_ORDER", entities={"items": [item("медовик", 2, honey.id)]}),
            "Привет": understanding(intent="GREETING"),
        }
    )
    bot = Bot(db, make_conversation(), llm)
    bot.say("2 медовика")

    assert reply_text(bot.say("Привет")) == (
        "Здравствуйте! Чем можем помочь? 😊\n\n"
        "Уточните, пожалуйста:\n1. На какую дату нужен заказ?\n2. К какому времени?"
    )


def test_misunderstood_message_during_a_draft_repeats_the_question_then_hands_over(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {"2 медовика": understanding(intent="CREATE_ORDER", entities={"items": [item("медовик", 2, honey.id)]})},
        default=understanding(intent="OTHER"),
    )
    bot = Bot(db, make_conversation(), llm)
    bot.say("2 медовика")

    again = bot.say("эээ")
    assert again.reply.kind == ReplyKind.ASK_MISSING and not again.handoff
    assert bot.say("ммм").handoff


def test_address_choice_named_in_words_is_understood(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    geocoder = FakeGeocoder(ambiguous("Сино, 82 мкр, 5", "Сино, 82 мкр, 5/1"))
    llm = _delivery_llm(catalog["honey"])
    llm.script["Первый вариант"] = understanding(intent="OTHER", address_candidate_choice=1)
    bot = Bot(db, make_conversation(), llm, geocoder)
    bot.say("Медовик")
    bot.say("Сино, 82 мкр, дом 5")

    outcome = bot.say("Первый вариант")

    assert outcome.reply.kind == ReplyKind.ORDER_SUMMARY
    assert bot.draft().delivery.address_formatted == "Сино, 82 мкр, 5"


def test_a_new_address_after_candidates_is_geocoded_again(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    geocoder = FakeGeocoder(ambiguous("Сино, 82 мкр, 5", "Сино, 82 мкр, 5/1"))
    llm = _delivery_llm(catalog["honey"])
    llm.script["Сино, 82 мкр, дом 5/1, подъезд 2"] = understanding(
        intent="CREATE_ORDER", entities={"address": "Сино, 82 мкр, дом 5/1, подъезд 2"}
    )
    bot = Bot(db, make_conversation(), llm, geocoder)
    bot.say("Медовик")
    bot.say("Сино, 82 мкр, дом 5")
    from tests.bot_fakes import single_house

    geocoder.result = single_house("Сино, 82 мкр, 5/1")
    outcome = bot.say("Сино, 82 мкр, дом 5/1, подъезд 2")

    assert outcome.reply.kind == ReplyKind.ORDER_SUMMARY
    assert len(geocoder.queries) == 2
    assert bot.draft().delivery.address_raw == "Сино, 82 мкр, дом 5/1, подъезд 2"


def test_invalid_phone_is_asked_again(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {
            "Медовик": understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовик", 1, honey.id)],
                    "delivery_date": DAY.isoformat(),
                    "delivery_time": "18:00",
                    "delivery_type": "PICKUP",
                    "phone": "12-34",
                },
            )
        }
    )
    conversation = make_conversation()
    bot = Bot(db, conversation, llm)

    outcome = bot.say("Медовик")

    assert reply_text(outcome) == (
        "Номер телефона не получилось распознать. Напишите, пожалуйста, в формате 92 780 91 52 "
        "или +992 92 780 91 52.\n"
        "Напишите, пожалуйста, номер телефона для связи."
    )
    db.refresh(conversation.customer)
    assert conversation.customer.phone is None


def test_customer_name_is_saved_and_asked_when_unknown(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    honey = catalog["honey"]
    llm = ScriptedLLM(
        {
            "Медовик": understanding(
                intent="CREATE_ORDER",
                entities={
                    "items": [item("медовик", 1, honey.id)],
                    "delivery_date": DAY.isoformat(),
                    "delivery_time": "18:00",
                    "delivery_type": "DELIVERY",
                    "phone": "901234567",
                },
            ),
            "Меня зовут Зарина": understanding(intent="CREATE_ORDER", entities={"customer_name": "Зарина"}),
        }
    )
    conversation = make_conversation("")  # no name from the Instagram profile
    bot = Bot(db, conversation, llm)

    ask = bot.say("Медовик")
    assert reply_text(ask) == (
        "Уточните, пожалуйста:\n1. Как к вам можно обращаться?\n"
        "2. Напишите, пожалуйста, адрес доставки: район, улица, дом, квартира и ориентир."
    )  # the recipient is not asked while the customer's own name is still unknown
    bot.say("Меня зовут Зарина")
    db.refresh(conversation.customer)
    assert conversation.customer.name == "Зарина"
