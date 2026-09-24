"""Follow-up questions (03-business-rules.md §6a).

The bot starts these messages itself, so the tests that matter most are the ones that keep it quiet:
a dialog an operator is looking at, a customer who wrote last, the night, the closed 24-hour window.
"""

import sys
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import now_utc
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.enums import ConversationMode, Language, MessageDirection, MessageSender, MessageType, OrderStatus
from app.models.order import Order
from app.services.dialog_state import AWAITING_CONFIRMATION, AWAITING_MISSING_FIELDS, DialogState
from app.services.follow_up_service import FollowUpService, run_follow_ups
from app.services.settings_service import SettingsService
from tests.test_dialog_service import make_conversation  # noqa: F401 - fixture used by name

#: 07:00 UTC = 12:00 in Dushanbe — inside the working hours whenever the suite runs. Without it the
#: tests that expect a follow-up failed before 9:00 local time (seen 24.09.2026 at 08:10).
_MIDDAY_UTC_HOUR = 7


@pytest.fixture(autouse=True)
def midday(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service and these helpers share one clock, fixed at midday in Dushanbe."""
    fixed = now_utc().replace(hour=_MIDDAY_UTC_HOUR, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(sys.modules["app.services.follow_up_service"], "now_utc", lambda: fixed)
    monkeypatch.setattr(sys.modules[__name__], "now_utc", lambda: fixed)


class FakeQueue:
    """``TaskQueue`` that only records what would have been sent to Instagram."""

    def __init__(self) -> None:
        self.outbox: list[int] = []

    def process_instagram_event(self, event: dict[str, Any]) -> bool:
        return False

    def send_message(self, message_id: int) -> bool:
        self.outbox.append(message_id)
        return True

    def transcribe_voice(self, message_id: int) -> bool:
        return False

    def synthesize_voice_reply(self, message_id: int) -> bool:
        return False

    def continue_after_location(self, order_id: int) -> bool:
        return False


def bot_said(db: Session, conversation: Conversation, text: str, *, hours_ago: float = 4) -> Message:
    """The bot's last reply, sent ``hours_ago`` — that is what the follow-up waits after."""
    moment = now_utc() - timedelta(hours=hours_ago)
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.OUTGOING,
        message_type=MessageType.TEXT,
        sender=MessageSender.AI,
        text=text,
        created_at=moment,
    )
    db.add(message)
    conversation.last_message_at = moment
    conversation.last_customer_message_at = moment - timedelta(minutes=1)
    db.commit()
    return message


def customer_said(db: Session, conversation: Conversation, text: str, *, hours_ago: float = 4) -> Message:
    moment = now_utc() - timedelta(hours=hours_ago)
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.INCOMING,
        message_type=MessageType.TEXT,
        sender=MessageSender.CUSTOMER,
        text=text,
        created_at=moment,
    )
    db.add(message)
    conversation.last_message_at = moment
    conversation.last_customer_message_at = moment
    db.commit()
    return message


def set_state(db: Session, conversation: Conversation, **fields: Any) -> None:
    state = DialogState.from_json(conversation.state)
    for key, value in fields.items():
        setattr(state, key, value)
    conversation.state = state.to_json()
    db.commit()


def waiting_dialog(
    db: Session,
    conversation: Conversation,
    make_order: Callable[..., Order],
    *,
    status: OrderStatus = OrderStatus.NEW,
    awaiting: str = AWAITING_MISSING_FIELDS,
    missing_fields: list[str] | None = None,
    reply: str = "Напишите, пожалуйста, номер телефона для связи.",
) -> Order:
    order = make_order(customer=conversation.customer, status=status)
    set_state(
        db,
        conversation,
        draft_order_id=order.id,
        awaiting=awaiting,
        missing_fields=missing_fields if missing_fields is not None else ["phone"],
    )
    bot_said(db, conversation, reply)
    return order


def run(db: Session, **kwargs: Any) -> tuple[Any, FakeQueue]:
    queue = FakeQueue()
    result = FollowUpService(db, queue=queue).run(**kwargs)
    return result, queue


def sent_texts(db: Session, conversation: Conversation) -> list[str]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.direction == MessageDirection.OUTGOING)
        .order_by(Message.id)
    )
    return [message.text or "" for message in db.scalars(stmt)]


# --------------------------------------------------------------------------- the three stages


def test_an_unfinished_order_is_chased_with_the_open_question(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    """«Вам коробочку оставить?» — plus the question the customer never answered."""
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)

    result, queue = run(db)

    assert len(result.sent) == 1 and queue.outbox == result.sent
    assert sent_texts(db, conversation)[-1] == (
        "Вам коробочку оставить? Напишите, пожалуйста, и мы всё оформим 🙂\n\n"
        "Напишите, пожалуйста, номер телефона для связи."
    )
    db.refresh(conversation)
    assert DialogState.from_json(conversation.state).follow_ups_sent  # remembered


def test_an_unconfirmed_summary_is_chased_without_naming_a_number(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    """«Заказ кабул кунам е не» — the order has no number for the customer yet, so none is named."""
    conversation = make_conversation()
    order = waiting_dialog(
        db,
        conversation,
        make_order,
        status=OrderStatus.WAITING_CONFIRMATION,
        awaiting=AWAITING_CONFIRMATION,
        missing_fields=[],
        reply="Проверьте, пожалуйста, заказ: …",
    )

    result, _ = run(db)

    assert len(result.sent) == 1
    text = sent_texts(db, conversation)[-1]
    assert text == "Подскажите, заказ оформляем? Напишите «Да» — и всё готово. Если передумали, просто скажите."
    assert str(order.id) not in text


def test_a_placed_order_waiting_for_a_receipt_is_chased_only_while_prepayment_is_on(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    """The archive's most common follow-up — «Мне для оформления заказа чек нужен или вы передумали?»."""
    conversation = make_conversation()
    make_order(customer=conversation.customer, status=OrderStatus.CONFIRMED)
    bot_said(db, conversation, "Заказ оформлен ✅")

    assert run(db)[0].sent == []  # payment is switched off: nothing to chase

    SettingsService(db).update({"prepayment_enabled": True, "prepayment_wallet": "+992 92 111 16 57"})
    result, _ = run(db)

    assert len(result.sent) == 1
    assert sent_texts(db, conversation)[-1] == "Мне для оформления заказа чек нужен, или вы передумали?"


def test_the_tajik_dialog_is_chased_in_tajik(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    conversation = make_conversation(language=Language.TG)
    waiting_dialog(db, conversation, make_order, missing_fields=[])

    run(db)

    assert sent_texts(db, conversation)[-1].startswith("Қуттиро барои шумо монем?")


# --------------------------------------------------------------------------- when the bot stays quiet


def test_nobody_is_chased_twice_about_the_same_thing(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)

    first, _ = run(db)
    # the follow-up itself is the last message now, and it was sent just now — rewind it as well
    conversation.last_message_at = now_utc() - timedelta(hours=4)
    db.commit()
    second, _ = run(db)

    assert len(first.sent) == 1
    assert second.sent == [] and second.skipped == {"already_asked": 1}


def test_a_customer_who_wrote_last_gets_an_answer_not_a_follow_up(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)
    customer_said(db, conversation, "а можно на пятницу?", hours_ago=3)

    result, _ = run(db)

    assert result.sent == [] and result.skipped == {"customer_wrote_last": 1}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", ConversationMode.HUMAN_HANDOFF),  # the operator answers, not the bot
        ("needs_attention", True),  # an operator has already been alerted
    ],
)
def test_a_dialog_a_human_is_looking_at_is_left_alone(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
    field: str,
    value: Any,
) -> None:
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)
    setattr(conversation, field, value)
    db.commit()

    result, _ = run(db)

    assert result.sent == [] and result.considered == 0


def test_the_closed_instagram_window_stops_the_follow_up(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    """06 §1: after 24 hours Instagram refuses the message, so it is never even written."""
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)
    conversation.last_customer_message_at = now_utc() - timedelta(hours=25)
    db.commit()

    result, _ = run(db)

    assert result.sent == [] and result.considered == 0


def test_nothing_is_sent_before_the_delay_has_passed(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)
    conversation.last_message_at = now_utc() - timedelta(minutes=30)
    db.commit()

    assert run(db)[0].sent == []

    SettingsService(db).update({"follow_up_after_hours": 1})
    conversation.last_message_at = now_utc() - timedelta(hours=2)
    db.commit()

    assert len(run(db)[0].sent) == 1


def test_a_blocked_customer_is_never_written_to(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)
    customer: Customer = conversation.customer
    customer.is_blocked = True
    db.commit()

    result, _ = run(db)

    assert result.sent == [] and result.skipped == {"blocked": 1}


def test_at_night_the_bot_says_nothing(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    """A follow-up is a message we start: it waits for the bakery's hours (03 §6a)."""
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)
    SettingsService(db).update({"order_hours_start": "09:00", "order_hours_end": "20:00"})
    night = now_utc().replace(hour=22, minute=0)  # 03:00 in Dushanbe

    result, _ = run(db, now=night)

    assert result.sent == [] and result.skipped == {"outside_hours": 1}


@pytest.mark.parametrize("switch", ["follow_up_enabled", "ai_enabled"])
def test_the_switches_stop_every_follow_up(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
    switch: str,
) -> None:
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)
    SettingsService(db).update({switch: False})

    result, _ = run(db)

    assert result.sent == [] and result.considered == 0


def test_a_dialog_with_nothing_open_is_not_chased(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
) -> None:
    """Someone who only asked a question and left is not a lost order — the bot does not nag."""
    conversation = make_conversation()
    bot_said(db, conversation, "Доставка по Худжанду — 14 сомони.")

    result, _ = run(db)

    assert result.sent == [] and result.skipped == {"nothing_to_ask": 1}


def test_run_follow_ups_is_the_entry_point_of_the_task(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)

    result = run_follow_ups(db, queue=FakeQueue())

    assert len(result.sent) == 1


def test_a_dry_run_decides_the_same_and_writes_nothing(
    db: Session,
    make_conversation: Callable[..., Conversation],  # noqa: F811
    make_order: Callable[..., Order],
) -> None:
    """``scripts/run_follow_ups --dry``: the plan without a single stored message."""
    conversation = make_conversation()
    waiting_dialog(db, conversation, make_order)
    before = sent_texts(db, conversation)

    dry = run_follow_ups(db, queue=FakeQueue(), dry_run=True)

    assert dry.planned == [(conversation.id, "draft")] and dry.sent == []
    assert sent_texts(db, conversation) == before
    db.refresh(conversation)
    assert DialogState.from_json(conversation.state).follow_ups_sent == []

    real = run_follow_ups(db, queue=FakeQueue())

    assert real.planned == dry.planned and len(real.sent) == 1
