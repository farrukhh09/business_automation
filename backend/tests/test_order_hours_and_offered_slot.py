"""Audit fixes of 18.09.2026 (docs/PROGRESS.md): order hours, "Да" to an offered slot, price wording.

- "завтра в 23:30" was accepted although the bakery hands orders over 9:00–20:00: ``order_hours_*``;
- "Да" to "На завтра можем не раньше 18:00" re-asked the time instead of taking 18:00;
- a Tajik reply said "100.00 сомонӣ кор." ("кор" is "work" in Tajik).

Deterministic: ``ScriptedLLM`` only.
"""

# ruff: noqa: F811 — pytest fixtures imported from test_dialog_service are reused as parameter names

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.ai import templates
from app.ai.responder import ReplyKind, fix_money
from app.core import time as app_time
from app.core.exceptions import ValidationError
from app.models.conversation import Conversation
from app.models.product import Product
from app.repositories.app_settings import AppSettingRepository
from app.schemas.settings import BusinessSettings
from app.services.dialog_state import DialogState
from app.services.order_validator import OUT_OF_HOURS, OrderValidator
from app.services.settings_service import BUSINESS_SETTINGS_KEY, SettingsService
from tests.bot_fakes import ScriptedLLM, item, understanding
from tests.test_dialog_service import (  # noqa: F401 - fixtures are picked up by name
    Bot,
    catalog,
    make_conversation,
    reply_text,
)

#: 17.09.2026 16:12 in Khujand (UTC+5); the lead time is 24 h, so 18.09 starts at 17:00.
MOMENT = datetime(2026, 9, 17, 11, 12, tzinfo=UTC)
TOO_EARLY = "Медовик на 18 сентября к 16:00"


@pytest.fixture()
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(app_time, "now_utc", lambda: MOMENT)
    monkeypatch.setattr("app.services.order_validator.now_utc", lambda: MOMENT)
    return MOMENT


def _too_early_llm(honey: Product) -> ScriptedLLM:
    entities = {"items": [item("медовик", 1, honey.id)], "delivery_date": "2026-09-18", "delivery_time": "16:00"}
    return ScriptedLLM({TOO_EARLY: understanding(intent="CREATE_ORDER", entities=entities)})


# --------------------------------------------------------------------------- "Да" to the offered slot


@pytest.mark.usefixtures("frozen_now")
@pytest.mark.parametrize("answer", ["да", "Давайте!", "ок", "ха майлаш", "хоп"])
def test_agreeing_to_the_earliest_slot_takes_it(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation], answer: str
) -> None:
    llm = _too_early_llm(catalog["honey"])
    bot = Bot(db, make_conversation(), llm)
    assert reply_text(bot.say(TOO_EARLY)).startswith("На завтра можем не раньше 17:00.")
    calls = len(llm.json_calls)

    outcome = bot.say(answer)

    order = bot.draft()
    assert (order.delivery_date, order.delivery_time) == (date(2026, 9, 18), time(17, 0))
    assert "offered_slot_taken" in outcome.actions
    assert len(llm.json_calls) == calls  # decided without the model
    text = reply_text(outcome)  # the next question, in the customer's language ("ха майлаш" → Tajik)
    assert "Это будет доставка или самовывоз?" in text or "Расонем ё худатон мегиред?" in text
    assert "К какому времени?" not in text and "Барои соати чанд?" not in text


@pytest.mark.usefixtures("frozen_now")
def test_a_yes_with_another_time_is_read_by_the_model(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    llm = _too_early_llm(catalog["honey"])
    llm.script["да, в 19:00"] = understanding(intent="CREATE_ORDER", entities={"delivery_time": "19:00"})
    bot = Bot(db, make_conversation(), llm)
    bot.say(TOO_EARLY)

    outcome = bot.say("да, в 19:00")

    assert bot.draft().delivery_time == time(19, 0)
    assert "offered_slot_taken" not in outcome.actions


@pytest.mark.usefixtures("frozen_now")
def test_the_offer_is_valid_for_one_answer_only(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    llm = _too_early_llm(catalog["honey"])
    llm.script["а доставка есть?"] = understanding(intent="DELIVERY_QUERY")
    llm.script["да"] = understanding(intent="OTHER")
    bot = Bot(db, make_conversation(), llm)
    bot.say(TOO_EARLY)
    bot.say("а доставка есть?")

    assert DialogState.from_json(bot.conversation.state).offered_slot is None
    outcome = bot.say("да")

    assert bot.draft().delivery_time is None
    assert "offered_slot_taken" not in outcome.actions


def test_the_offered_slot_survives_the_state_json_and_rejects_garbage() -> None:
    state = DialogState(offered_slot={"date": "2026-09-18", "time": "17:00"})
    assert DialogState.from_json(state.to_json()).offered_slot == {"date": "2026-09-18", "time": "17:00"}
    assert DialogState.from_json({"offered_slot": {"date": "завтра", "time": "17:00"}}).offered_slot is None
    assert DialogState.from_json({"offered_slot": "2026-09-18"}).offered_slot is None


# --------------------------------------------------------------------------- order hours


def _hours(db: Session, start: str | None = "09:00", end: str | None = "20:00") -> None:
    SettingsService(db).update({"order_hours_start": start, "order_hours_end": end})


@pytest.mark.usefixtures("frozen_now")
def test_a_time_after_closing_is_refused_and_the_day_is_kept(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    _hours(db)
    text = "Медовик на 20 сентября в 23:30"
    entities = {
        "items": [item("медовик", 1, catalog["honey"].id)],
        "delivery_date": "2026-09-20",
        "delivery_time": "23:30",
    }
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)}))

    outcome = bot.say(text)

    order = bot.draft()
    assert (order.delivery_date, order.delivery_time) == (date(2026, 9, 20), None)
    assert outcome.reply.kind == ReplyKind.ASK_MISSING
    assert reply_text(outcome).startswith("Заказы выдаём с 09:00 до 20:00. Выберите, пожалуйста, другое время.")


@pytest.mark.usefixtures("frozen_now")
def test_the_earliest_slot_moves_to_the_next_opening(
    db: Session,
    catalog: dict[str, Product],
    make_conversation: Callable[..., Conversation],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evening = datetime(2026, 9, 17, 15, 30, tzinfo=UTC)  # 20:30 in Khujand: 24 h later the bakery is closed
    monkeypatch.setattr(app_time, "now_utc", lambda: evening)
    monkeypatch.setattr("app.services.order_validator.now_utc", lambda: evening)
    _hours(db)
    text = "Медовик на завтра"
    entities = {"items": [item("медовик", 1, catalog["honey"].id)], "delivery_date": "2026-09-18"}
    llm = ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)})
    bot = Bot(db, make_conversation(), llm)

    reply = reply_text(bot.say(text))

    # 18.09 is not possible at all (closes at 20:00, the lead time ends at 20:30): the next opening is offered
    assert "Самое раннее — 19.09.2026 после 09:00." in reply
    bot.say("да")
    order = bot.draft()
    assert (order.delivery_date, order.delivery_time) == (date(2026, 9, 19), time(9, 0))


def test_slot_problems_with_order_hours() -> None:
    settings = BusinessSettings(min_lead_time_hours=0, order_hours_start=time(9), order_hours_end=time(20))
    day = app_time.business_today() + timedelta(days=2)
    assert OrderValidator.slot_problems(day, time(23, 30), settings) == [OUT_OF_HOURS]
    assert OrderValidator.slot_problems(day, time(8, 59), settings) == [OUT_OF_HOURS]
    assert OrderValidator.slot_problems(day, time(9), settings) == []
    assert OrderValidator.slot_problems(day, time(20), settings) == []
    assert OrderValidator.slot_problems(day, time(23, 30), BusinessSettings(min_lead_time_hours=0)) == []


def test_order_hours_settings_validate_and_clear(db: Session) -> None:
    service = SettingsService(db)
    with pytest.raises(ValidationError):
        service.update({"order_hours_start": "20:00", "order_hours_end": "09:00"})
    _hours(db)
    settings = service.get()
    assert (settings.order_hours_start, settings.order_hours_end) == (time(9), time(20))

    service.update({"working_hours": "Ежедневно"})  # an update of another field keeps the hours
    assert service.get().order_hours_end == time(20)
    service.update({"order_hours_start": None, "order_hours_end": None})  # explicit null removes the limit
    assert (service.get().order_hours_start, service.get().order_hours_end) == (None, None)


def test_a_broken_stored_hours_pair_does_not_reset_other_settings(db: Session) -> None:
    AppSettingRepository(db).set_value(
        BUSINESS_SETTINGS_KEY,
        {"business_name": "Синнамоны", "order_hours_start": "20:00", "order_hours_end": "09:00"},
    )
    db.commit()

    settings = SettingsService(db).get()

    assert settings.business_name == "Синнамоны"
    assert (settings.order_hours_start, settings.order_hours_end) == (None, None)


def test_out_of_hours_templates() -> None:
    both = {"timing_problem": "delivery_out_of_hours", "order_hours_start": "09:00", "order_hours_end": "20:00"}
    assert templates.render(ReplyKind.ASK_MISSING, "tg", both, ["delivery_time"]).startswith(
        "Фармоишро аз соати 09:00 то 20:00 медиҳем."
    )
    until = {"timing_problem": "delivery_out_of_hours", "order_hours_end": "20:00"}
    assert templates.render(ReplyKind.ASK_MISSING, "ru", until, ["delivery_time"]).startswith(
        "Заказы выдаём не позже 20:00."
    )


# --------------------------------------------------------------------------- prices in model replies


@pytest.mark.parametrize(
    ("language", "text", "expected"),
    [
        ("tg", "Шоколадные синнамоны — 100.00 сомонӣ кор.", "Шоколадные синнамоны — 100 сомонӣ / қуттӣ."),
        ("tg", "Классические — 10.90 сомонӣ / шт. Боз?", "Классические — 10.90 сомонӣ / дона. Боз?"),
        ("ru", "Итого 200.00 сомони / кор.", "Итого 200 сомони / кор."),
        ("ru", "Время 18.00, сумма 100 сомони", "Время 18.00, сумма 100 сомони"),
        ("tg", "Корҳо тайёр", "Корҳо тайёр"),
    ],
)
def test_fix_money(language: str, text: str, expected: str) -> None:
    fixed, changed = fix_money(text, language)
    assert fixed == expected
    assert changed == (text != expected)
