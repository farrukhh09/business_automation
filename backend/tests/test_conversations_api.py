"""Conversations API (04-api.md §11), Instagram webhook and media routes (§13)."""

import hashlib
import hmac
import json
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.time import now_utc
from app.models import Customer, Message
from app.models.conversation import Conversation
from app.models.enums import (
    ConversationMode,
    DeliveryType,
    MessageDirection,
    MessageSender,
    MessageType,
    OrderStatus,
)
from app.services.conversation_service import ConversationService
from app.services.dialog_state import DialogState
from app.services.location_service import LocationService
from app.services.media_storage import MediaStorage
from app.services.task_queue import get_task_queue
from tests.bot_fakes import RecordingQueue

ACCOUNT = "17841400000000000"
APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "verify-me"


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.dependency_overrides[get_task_queue] = lambda: recording
    return recording


@pytest.fixture
def make_conversation(db: Session, make_customer: Callable[..., Customer]) -> Callable[..., Conversation]:
    counter = iter(range(1, 1000))

    def _make(name: str = "Алия", *, texts: tuple[str, ...] = ("Здравствуйте",), **fields: Any) -> Conversation:
        index = next(counter)
        customer = make_customer(
            name, instagram_user_id=f"igsid-{index}", username=f"user{index}", phone="+992900000001"
        )
        conversation = ConversationService(db).get_or_create_instagram(customer, f"{ACCOUNT}:igsid-{index}")
        for text in texts:
            db.add(
                Message(
                    conversation_id=conversation.id,
                    direction=MessageDirection.INCOMING,
                    message_type=MessageType.TEXT,
                    sender=MessageSender.CUSTOMER,
                    text=text,
                )
            )
        conversation.last_message_at = now_utc()
        conversation.last_customer_message_at = now_utc()
        for key, value in fields.items():
            setattr(conversation, key, value)
        db.commit()
        return conversation

    return _make


# --------------------------------------------------------------------------- list / detail


def test_list_filters_and_shows_preview_and_active_order(client, admin_headers, make_conversation, make_order) -> None:
    calm = make_conversation("Алия", texts=("Здравствуйте", "Хочу медовик"))
    urgent = make_conversation(
        "Бахор", mode=ConversationMode.HUMAN_HANDOFF, needs_attention=True, handoff_reason="Жалоба"
    )
    make_order(customer=calm.customer, status=OrderStatus.NEW)
    placed = make_order(customer=calm.customer, status=OrderStatus.CONFIRMED)

    everything = client.get("/api/conversations", headers=admin_headers).json()
    assert everything["total"] == 2
    item = next(entry for entry in everything["items"] if entry["id"] == calm.id)
    assert item["last_message_preview"] == "Хочу медовик"
    assert item["active_order_id"] == placed.id  # drafts are not "orders in progress"
    assert item["customer"] == {
        "id": calm.customer.id,
        "name": "Алия",
        "username": calm.customer.username,
        "phone": "+992900000001",
    }

    attention = client.get("/api/conversations", params={"needs_attention": True}, headers=admin_headers).json()
    assert [entry["id"] for entry in attention["items"]] == [urgent.id]
    handoff = client.get("/api/conversations", params={"mode": "HUMAN_HANDOFF"}, headers=admin_headers).json()
    assert [entry["handoff_reason"] for entry in handoff["items"]] == ["Жалоба"]
    found = client.get("/api/conversations", params={"search": "бахор"}, headers=admin_headers).json()
    assert [entry["id"] for entry in found["items"]] == [urgent.id]


def test_detail_pages_messages_and_summarizes_state(
    client, operator_headers, db: Session, make_conversation, make_order
) -> None:
    conversation = make_conversation(texts=tuple(f"сообщение {index}" for index in range(1, 8)))
    draft = make_order(customer=conversation.customer, status=OrderStatus.NEW)
    conversation.state = DialogState(draft_order_id=draft.id, awaiting="missing_fields", language="tg").to_json()
    db.commit()

    detail = client.get(f"/api/conversations/{conversation.id}", params={"limit": 3}, headers=operator_headers).json()
    assert [message["text"] for message in detail["messages"]] == ["сообщение 5", "сообщение 6", "сообщение 7"]
    assert detail["state_summary"] == {"draft_order_id": draft.id, "awaiting": "missing_fields", "language": "tg"}
    older = client.get(
        f"/api/conversations/{conversation.id}",
        params={"before_id": detail["messages"][0]["id"], "limit": 2},
        headers=operator_headers,
    ).json()
    assert [message["text"] for message in older["messages"]] == ["сообщение 3", "сообщение 4"]
    message = detail["messages"][0]
    assert set(message) == {
        "id", "direction", "message_type", "sender", "text", "audio_url", "media_url", "intent",
        "delivery_status", "error", "sent_by_user_id", "created_at",
    }  # fmt: skip

    draft.status = OrderStatus.CONFIRMED  # confirmed from the panel: no longer a draft
    db.commit()
    summary = client.get(f"/api/conversations/{conversation.id}", headers=operator_headers).json()["state_summary"]
    assert summary == {"draft_order_id": None, "awaiting": None, "language": "tg"}


def test_unknown_conversation_is_404(client, admin_headers) -> None:
    response = client.get("/api/conversations/999", headers=admin_headers)
    assert response.status_code == 404 and response.json()["code"] == "not_found"


# --------------------------------------------------------------------------- handoff / resume / read


def test_take_over_and_return_to_the_bot(
    client, operator_headers, operator_user, db: Session, make_conversation
) -> None:
    conversation = make_conversation(needs_attention=True, failed_ai_attempts=2)

    taken = client.post(
        f"/api/conversations/{conversation.id}/handoff", json={"reason": "Сложный заказ"}, headers=operator_headers
    )
    assert taken.status_code == 200
    assert taken.json()["mode"] == "HUMAN_HANDOFF" and taken.json()["handoff_reason"] == "Сложный заказ"
    db.refresh(conversation)
    assert conversation.assigned_user_id == operator_user.id and conversation.handoff_at is not None

    default_reason = client.post(f"/api/conversations/{conversation.id}/handoff", headers=operator_headers).json()
    assert default_reason["handoff_reason"] == "Оператор взял диалог на себя"

    resumed = client.post(f"/api/conversations/{conversation.id}/resume", headers=operator_headers).json()
    assert resumed["mode"] == "AI" and resumed["needs_attention"] is False and resumed["handoff_reason"] is None
    db.refresh(conversation)
    assert conversation.failed_ai_attempts == 0 and conversation.assigned_user_id is None


def test_mark_read(client, admin_headers, db: Session, make_conversation) -> None:
    conversation = make_conversation(needs_attention=True)
    response = client.post(f"/api/conversations/{conversation.id}/read", headers=admin_headers)
    assert response.status_code == 204 and response.content == b""
    db.refresh(conversation)
    assert conversation.needs_attention is False


# --------------------------------------------------------------------------- operator messages


def test_operator_message_is_queued_and_takes_the_dialog_over(
    client, operator_headers, operator_user, queue: RecordingQueue, db: Session, make_conversation
) -> None:
    conversation = make_conversation(needs_attention=True)

    response = client.post(
        f"/api/conversations/{conversation.id}/messages",
        json={"text": "  Здравствуйте, это менеджер  "},
        headers=operator_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["text"] == "Здравствуйте, это менеджер" and body["sender"] == "OPERATOR"
    assert body["delivery_status"] == "PENDING" and body["sent_by_user_id"] == operator_user.id
    assert queue.of("send_message") == [body["id"]]
    db.refresh(conversation)
    assert conversation.mode == ConversationMode.HUMAN_HANDOFF and conversation.needs_attention is False
    assert conversation.handoff_reason == "Оператор ответил клиенту вручную"


def test_operator_message_outside_the_window_is_409(
    client, operator_headers, queue: RecordingQueue, db: Session, make_conversation
) -> None:
    conversation = make_conversation()
    conversation.last_customer_message_at = now_utc() - timedelta(hours=24, minutes=1)
    db.commit()

    response = client.post(
        f"/api/conversations/{conversation.id}/messages", json={"text": "Добрый день"}, headers=operator_headers
    )

    assert response.status_code == 409 and response.json()["code"] == "messaging_window_closed"
    assert queue.calls == []
    assert db.query(Message).filter(Message.direction == MessageDirection.OUTGOING).count() == 0


def test_operator_message_validation(client, operator_headers, queue: RecordingQueue, make_conversation) -> None:
    conversation = make_conversation()
    for payload in ({"text": ""}, {"text": "   "}, {"text": "x" * 2001}, {}):
        response = client.post(f"/api/conversations/{conversation.id}/messages", json=payload, headers=operator_headers)
        assert response.status_code == 422, payload


# --------------------------------------------------------------------------- webhook


@pytest.fixture
def webhook_settings(app: FastAPI) -> Iterator[Settings]:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        INSTAGRAM_APP_SECRET=APP_SECRET,
        INSTAGRAM_VERIFY_TOKEN=VERIFY_TOKEN,
        JWT_SECRET="x" * 40,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings


def _payload(*messaging: dict[str, Any]) -> bytes:
    return json.dumps(
        {"object": "instagram", "entry": [{"id": ACCOUNT, "time": 1758000000000, "messaging": list(messaging)}]}
    ).encode()


def _message(mid: str, text: str = "Здравствуйте", sender: str = "5550001", **message: Any) -> dict[str, Any]:
    return {
        "sender": {"id": sender},
        "recipient": {"id": ACCOUNT},
        "timestamp": 1758000000000,
        "message": {"mid": mid, "text": text, **message},
    }


def _signature(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_subscription_verification(client, webhook_settings: Settings) -> None:
    ok = client.get(
        "/api/webhooks/instagram",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1158201444"},
    )
    assert ok.status_code == 200 and ok.text == "1158201444" and ok.headers["content-type"].startswith("text/plain")

    wrong = client.get(
        "/api/webhooks/instagram", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}
    )
    assert wrong.status_code == 403


def test_signed_webhook_enqueues_one_task_per_customer_message(
    client, webhook_settings: Settings, queue: RecordingQueue
) -> None:
    body = _payload(
        _message("mid.1"),
        _message("mid.2", text="echo", sender=ACCOUNT, is_echo=True),
        {"sender": {"id": "5550001"}, "recipient": {"id": ACCOUNT}, "timestamp": 1, "read": {"mid": "mid.1"}},
        _message("mid.3", text="Хочу торт"),
    )

    response = client.post("/api/webhooks/instagram", content=body, headers={"X-Hub-Signature-256": _signature(body)})

    assert response.status_code == 200 and response.json() == {"status": "ok", "events": 2}
    events = queue.of("process_instagram_event")
    assert [event["mid"] for event in events] == ["mid.1", "mid.3"]
    assert events[0]["sender_id"] == "5550001" and events[0]["account_id"] == ACCOUNT


def test_webhook_passes_on_the_manager_s_reply_to_a_customer(
    client, webhook_settings: Settings, queue: RecordingQueue
) -> None:
    """Test mode (06 §1a) records what the manager wrote in the Instagram app; the task decides."""
    reply = _message("mid.m1", text="10 сомони", sender=ACCOUNT, is_echo=True)
    reply["recipient"] = {"id": "5550001"}
    body = _payload(reply)

    response = client.post("/api/webhooks/instagram", content=body, headers={"X-Hub-Signature-256": _signature(body)})

    assert response.status_code == 200
    [queued] = queue.of("process_instagram_event")
    assert (queued["mid"], queued["is_echo"], queued["recipient_id"]) == ("mid.m1", True, "5550001")


def test_webhook_with_a_bad_signature_is_rejected(client, webhook_settings: Settings, queue: RecordingQueue) -> None:
    body = _payload(_message("mid.1"))
    for header in ({"X-Hub-Signature-256": _signature(body, "other-secret")}, {}):
        response = client.post("/api/webhooks/instagram", content=body, headers=header)
        assert response.status_code == 403
    assert queue.calls == []


def test_webhook_without_a_configured_secret(client, app: FastAPI, queue: RecordingQueue) -> None:
    body = _payload(_message("mid.1"))
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, APP_ENV="production", JWT_SECRET="x" * 40)
    assert client.post("/api/webhooks/instagram", content=body).status_code == 503

    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, APP_ENV="development")
    assert client.post("/api/webhooks/instagram", content=body).status_code == 200
    assert [event["mid"] for event in queue.of("process_instagram_event")] == ["mid.1"]


def test_webhook_invalid_json_and_broker_failure(client, app: FastAPI, webhook_settings: Settings) -> None:
    class DownQueue(RecordingQueue):
        def process_instagram_event(self, event: dict[str, Any]) -> bool:
            return False

    app.dependency_overrides[get_task_queue] = DownQueue
    garbage = b"not json"
    assert (
        client.post(
            "/api/webhooks/instagram", content=garbage, headers={"X-Hub-Signature-256": _signature(garbage)}
        ).status_code
        == 400
    )

    body = _payload(_message("mid.1"))
    response = client.post("/api/webhooks/instagram", content=body, headers={"X-Hub-Signature-256": _signature(body)})
    assert response.status_code == 502


# --------------------------------------------------------------------------- media


def test_media_route_serves_only_stored_files(client, app: FastAPI, tmp_path: Path) -> None:
    storage = MediaStorage(tmp_path)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, APP_ENV="test", MEDIA_ROOT=str(tmp_path))
    name = storage.save(b"voice-bytes", prefix="tts", extension="m4a")
    (tmp_path / "notes.txt").write_text("secret")

    ok = client.get(f"/api/media/{name}")
    assert ok.status_code == 200 and ok.content == b"voice-bytes" and ok.headers["content-type"] == "audio/mp4"

    for bad in ("notes.txt", "tts-doesnotexistatallxxxx.m4a", "..%2Fnotes.txt", "tts-..%2F..%2Fnotes.txt"):
        assert client.get(f"/api/media/{bad}").status_code == 404, bad


# --------------------------------------------------------------------------- map pin → dialog continues


def test_public_pin_on_a_bot_draft_continues_the_dialog(
    client, queue: RecordingQueue, db: Session, make_conversation, make_order
) -> None:
    conversation = make_conversation()
    draft = make_order(
        customer=conversation.customer,
        status=OrderStatus.NEW,
        delivery_type=DeliveryType.DELIVERY,
        delivery={"address_raw": "Сино, 82 мкр"},
        conversation_id=conversation.id,
    )
    admin_order = make_order(delivery_type=DeliveryType.DELIVERY, delivery={"address_raw": "Шохмансур"})
    service = LocationService(db)
    draft_link = service.create_link(draft.delivery)
    admin_link = service.create_link(admin_order.delivery)

    assert (
        client.post(
            f"/api/public/location/{draft_link.token}", json={"latitude": 38.56, "longitude": 68.78}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/public/location/{admin_link.token}", json={"latitude": 38.56, "longitude": 68.78}
        ).status_code
        == 200
    )

    assert queue.of("continue_after_location") == [draft.id]
