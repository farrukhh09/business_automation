"""Days off and the packing rule (03-business-rules.md §1.3, added 21.09.2026).

Both rules come from the bakery's own Instagram archive:

- every Friday the owner writes "завтра мы не работаем, в понедельник снова будем" — ``closed_weekdays``;
- "у нас либо 4, либо 8", "только 4, 8, 12... так как в коробке 4 штук" — ``min_order_quantity``
  and ``order_quantity_step``.

Like the timing checks, both block the bot only: staff may still enter anything by hand.
Deterministic throughout — ``ScriptedLLM`` only, no live model.
"""

# ruff: noqa: F811 — pytest fixtures imported from test_dialog_service are reused as parameter names

from collections.abc import Callable
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy.orm import Session

from app.ai import templates
from app.ai.responder import ReplyKind
from app.core import time as app_time
from app.models.conversation import Conversation
from app.models.product import Product
from app.repositories.app_settings import AppSettingRepository
from app.schemas.settings import BusinessSettings
from app.services.dialog_service import _earliest_slot
from app.services.order_validator import (
    CLOSED_DAY,
    QUANTITY_BELOW_MIN,
    QUANTITY_NOT_MULTIPLE,
    OrderValidator,
    allowed_quantities_around,
    next_open_day,
    quantity_problem,
    smallest_allowed_quantity,
)
from app.services.settings_service import BUSINESS_SETTINGS_KEY, SettingsService
from tests.bot_fakes import ScriptedLLM, item, understanding
from tests.test_dialog_service import (  # noqa: F401 - fixtures are picked up by name
    Bot,
    catalog,
    make_conversation,
    reply_text,
)

#: 17.09.2026 16:12 in Khujand (UTC+5) — a Thursday. 18.09 is the Friday the archive calls the last
#: working day, 19–20.09 the weekend, 21.09 the Monday the owner offers instead.
MOMENT = datetime(2026, 9, 17, 11, 12, tzinfo=UTC)
SATURDAY = date(2026, 9, 19)
MONDAY = date(2026, 9, 21)
WEEKEND = [5, 6]


@pytest.fixture()
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(app_time, "now_utc", lambda: MOMENT)
    monkeypatch.setattr("app.services.order_validator.now_utc", lambda: MOMENT)
    return MOMENT


def _closed(db: Session, weekdays: list[int] = WEEKEND) -> None:
    SettingsService(db).update({"closed_weekdays": weekdays})


def _packing(db: Session, minimum: int = 4, step: int = 4) -> None:
    SettingsService(db).update({"min_order_quantity": minimum, "order_quantity_step": step})


# --------------------------------------------------------------------------- closed weekdays: rules


def _slot_problems(day: date, settings: BusinessSettings) -> list[str]:
    """``slot_problems`` at the frozen moment, so only the weekday can be wrong."""
    return OrderValidator.slot_problems(day, time(12, 0), settings, MOMENT)


@pytest.mark.usefixtures("frozen_now")
def test_a_day_off_is_reported_and_a_working_day_is_not() -> None:
    settings = BusinessSettings(closed_weekdays=WEEKEND)

    assert CLOSED_DAY in _slot_problems(SATURDAY, settings)
    assert CLOSED_DAY not in _slot_problems(MONDAY, settings)
    assert _slot_problems(SATURDAY, BusinessSettings()) == []  # no days off configured


@pytest.mark.parametrize(
    ("day", "expected"),
    [(date(2026, 9, 18), date(2026, 9, 18)), (SATURDAY, MONDAY), (date(2026, 9, 20), MONDAY)],
)
def test_next_open_day_skips_the_weekend(day: date, expected: date) -> None:
    assert next_open_day(day, BusinessSettings(closed_weekdays=WEEKEND)) == expected


def test_closed_weekdays_are_sorted_deduplicated_and_never_the_whole_week() -> None:
    assert BusinessSettings(closed_weekdays=[6, 5, 6]).closed_weekdays == WEEKEND
    with pytest.raises(ValueError, match="все семь дней"):
        BusinessSettings(closed_weekdays=[0, 1, 2, 3, 4, 5, 6])
    with pytest.raises(ValueError):  # a weekday outside 0..6  # noqa: PT011 - pydantic wraps the message
        BusinessSettings(closed_weekdays=[7])


def test_a_broken_stored_week_drops_only_that_field(db: Session) -> None:
    """A model-level check must not take the other settings down with it (settings_service)."""
    AppSettingRepository(db).set_value(
        BUSINESS_SETTINGS_KEY,
        {"closed_weekdays": [0, 1, 2, 3, 4, 5, 6], "order_hours_start": "09:00", "business_name": "Синнамоны"},
    )
    db.commit()

    settings = SettingsService(db).get()

    assert settings.closed_weekdays == []
    assert settings.order_hours_start == time(9, 0)
    assert settings.business_name == "Синнамоны"


# --------------------------------------------------------------------------- closed weekdays: dialog


@pytest.mark.usefixtures("frozen_now")
def test_an_order_for_a_day_off_is_refused_with_the_next_working_day(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    _closed(db)
    text = "Медовик на субботу к 14:00"
    entities = {
        "items": [item("медовик", 1, catalog["honey"].id)],
        "delivery_date": SATURDAY.isoformat(),
        "delivery_time": "14:00",
    }
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)}))

    outcome = bot.say(text)

    order = bot.draft()
    assert (order.delivery_date, order.delivery_time) == (None, None)  # the whole slot is dropped
    assert outcome.reply.kind == ReplyKind.ASK_MISSING
    reply = reply_text(outcome)
    assert "В субботу мы не работаем." in reply
    assert "Ближайший рабочий день — понедельник, 21.09.2026." in reply


def test_the_earliest_slot_skips_the_weekend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Friday 20:30 + 24 h lands after closing on Saturday, and the weekend is off: Monday 09:00."""
    friday_evening = datetime(2026, 9, 18, 15, 30, tzinfo=UTC)  # 20:30 in Khujand
    monkeypatch.setattr(app_time, "now_utc", lambda: friday_evening)
    settings = BusinessSettings(
        closed_weekdays=WEEKEND, order_hours_start=time(9, 0), order_hours_end=time(20, 0)
    )

    assert _earliest_slot(settings) == {
        "earliest_date": MONDAY.isoformat(),
        "earliest_time": "09:00",
        "earliest_relative": None,
    }


# --------------------------------------------------------------------------- packing rule: pure rules


@pytest.mark.parametrize(
    ("total", "expected"),
    [(1, QUANTITY_BELOW_MIN), (3, QUANTITY_BELOW_MIN), (4, None), (5, QUANTITY_NOT_MULTIPLE), (8, None)],
)
def test_quantity_problem_of_a_total(total: int, expected: str | None) -> None:
    assert quantity_problem(total, BusinessSettings(min_order_quantity=4, order_quantity_step=4)) == expected


@pytest.mark.parametrize(("total", "expected"), [(1, (None, 4)), (5, (4, 8)), (6, (4, 8)), (9, (8, 12))])
def test_the_totals_offered_instead(total: int, expected: tuple[int | None, int]) -> None:
    assert allowed_quantities_around(total, BusinessSettings(min_order_quantity=4, order_quantity_step=4)) == expected


def test_the_packing_rule_is_off_by_default() -> None:
    settings = BusinessSettings()
    assert smallest_allowed_quantity(settings) == 1
    assert quantity_problem(5, settings) is None


def test_a_minimum_that_is_not_a_whole_box_rounds_up() -> None:
    """min 6 with boxes of 4: the smallest order the bakery can pack is 8, not 6."""
    settings = BusinessSettings(min_order_quantity=6, order_quantity_step=4)
    assert smallest_allowed_quantity(settings) == 8
    assert allowed_quantities_around(5, settings) == (None, 8)


# --------------------------------------------------------------------------- packing rule: dialog


@pytest.mark.usefixtures("frozen_now")
def test_five_pieces_are_answered_with_the_two_possible_totals(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    _packing(db)
    text = "5 медовиков на 21 сентября к 14:00, самовывоз"
    entities = {
        "items": [item("медовик", 5, catalog["honey"].id)],
        "delivery_date": MONDAY.isoformat(),
        "delivery_time": "14:00",
        "delivery_type": "PICKUP",
    }
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)}))

    outcome = bot.say(text)

    assert outcome.reply.kind == ReplyKind.ASK_MISSING  # not the summary
    reply = reply_text(outcome)
    # The rule is named by the totals that fit it, never by the step: with boxes of 4 and 6 the step
    # is 2 and calling it a box size would be a lie (03 §1.3, 23.09.2026).
    assert "подходят 4, 8, 12, 16 и так далее" in reply
    assert "Сейчас 5 — сделаем 4 или 8?" in reply


@pytest.mark.usefixtures("frozen_now")
def test_boxes_of_four_and_six_accept_every_even_total_from_four(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """23.09.2026: the bakery added boxes of 6, so 4 + 6 add up to 4, 6, 8, 10 … (minimum 4, step 2)."""
    _packing(db, minimum=4, step=2)
    text = "5 медовиков на 21 сентября к 14:00, самовывоз"
    entities = {
        "items": [item("медовик", 5, catalog["honey"].id)],
        "delivery_date": MONDAY.isoformat(),
        "delivery_time": "14:00",
        "delivery_type": "PICKUP",
    }
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)}))

    reply = reply_text(bot.say(text))

    assert "подходят 4, 6, 8, 10 и так далее" in reply
    assert "Сейчас 5 — сделаем 4 или 6?" in reply

    settings = BusinessSettings(min_order_quantity=4, order_quantity_step=2)
    assert quantity_problem(6, settings) is None  # a box of six on its own
    assert quantity_problem(10, settings) is None  # four and six together
    assert quantity_problem(3, settings) == QUANTITY_BELOW_MIN
    assert quantity_problem(7, settings) == QUANTITY_NOT_MULTIPLE


@pytest.mark.usefixtures("frozen_now")
def test_one_piece_is_answered_with_the_minimum(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    _packing(db)
    text = "Один медовик на 21 сентября к 14:00, самовывоз"
    entities = {
        "items": [item("медовик", 1, catalog["honey"].id)],
        "delivery_date": MONDAY.isoformat(),
        "delivery_time": "14:00",
        "delivery_type": "PICKUP",
    }
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)}))

    reply = reply_text(bot.say(text))

    assert "Минимальный заказ — 4 шт." in reply
    assert "Сделаем 4?" in reply


@pytest.mark.usefixtures("frozen_now")
def test_a_whole_box_goes_through_to_the_summary(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    _packing(db)
    text = "4 медовика на 21 сентября к 14:00, самовывоз, Алия 928001122"
    entities = {
        "items": [item("медовик", 4, catalog["honey"].id)],
        "delivery_date": MONDAY.isoformat(),
        "delivery_time": "14:00",
        "delivery_type": "PICKUP",
        "customer_name": "Алия",
        "phone": "928001122",
    }
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="CREATE_ORDER", entities=entities)}))

    outcome = bot.say(text)

    assert outcome.reply.kind == ReplyKind.ORDER_SUMMARY
    assert "кратно" not in reply_text(outcome)


# --------------------------------------------------------------------------- templates


@pytest.mark.usefixtures("frozen_now")
def test_a_price_question_says_how_big_a_box_is(
    db: Session, catalog: dict[str, Product], make_conversation: Callable[..., Conversation]
) -> None:
    """The commonest message of the archive is "Сколько стоит коробка?" — per-piece prices alone
    do not answer it."""
    _packing(db)
    text = "Сколько стоит коробка?"
    bot = Bot(db, make_conversation(), ScriptedLLM({text: understanding(intent="PRODUCT_QUERY")}))

    reply = reply_text(bot.say(text))

    assert "Заказ собираем коробочками от 4 шт. — подходят 4, 8, 12, 16 и так далее" in reply


@pytest.mark.parametrize("language", ["ru", "tg"])
def test_both_languages_have_the_new_texts(language: str) -> None:
    facts = {
        "timing_problem": CLOSED_DAY,
        "closed_weekday": 5,
        "next_open_date": MONDAY.isoformat(),
        "next_open_weekday": 0,
        "quantity_problem": QUANTITY_NOT_MULTIPLE,
        "quantity_total": 5,
        "quantity_step": 4,
        "quantity_min": 4,
        "quantity_lower": 4,
        "quantity_upper": 8,
    }

    text = templates.render(ReplyKind.ASK_MISSING, language, facts, ["delivery_date"])

    assert "21.09.2026" in text
    assert "4" in text and "8" in text
    assert "{" not in text  # every placeholder was filled
