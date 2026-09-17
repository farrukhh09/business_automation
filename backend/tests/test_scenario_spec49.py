"""SPEC §49 readiness criterion, end to end.

Everything real except the network: the signed Instagram webhook goes through the HTTP route, the
tasks run inline (``InlineQueue``), the LLM is scripted, Instagram and the geocoder are fakes. The
admin side is checked through the REST API exactly as the panel uses it.
"""

import hashlib
import hmac
import json
from collections.abc import Iterator
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.time import now_utc
from app.models import Customer, Order
from app.models.conversation import Conversation
from app.models.enums import ConversationMode, OrderStatus
from app.services.media_storage import MediaStorage
from app.services.task_queue import get_task_queue
from tests.bot_fakes import FakeGeocoder, FakeMessenger, InlineQueue, ScriptedLLM, future_day, item, understanding

ACCOUNT = "17841400000000000"
CUSTOMER_IGSID = "990011223344"
APP_SECRET = "scenario-app-secret"
DAY = future_day(3)
SECOND_DAY = future_day(4)


class InstagramUser:
    """Sends signed webhook deliveries on behalf of one Instagram customer."""

    def __init__(self, client: Any, messenger: FakeMessenger) -> None:
        self.client = client
        self.messenger = messenger
        self._mids = count(1)

    def says(self, text: str) -> str:
        """Posts one message and returns the bot's answer (or ``""`` when the bot stayed silent)."""
        sent_before = len(self.messenger.sent_texts)
        timestamp_ms = int(now_utc().timestamp() * 1000)
        body = json.dumps(
            {
                "object": "instagram",
                "entry": [
                    {
                        "id": ACCOUNT,
                        "time": timestamp_ms,
                        "messaging": [
                            {
                                "sender": {"id": CUSTOMER_IGSID},
                                "recipient": {"id": ACCOUNT},
                                "timestamp": timestamp_ms,
                                "message": {"mid": f"mid.scenario.{next(self._mids)}", "text": text},
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ).encode()
        signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = self.client.post("/api/webhooks/instagram", content=body, headers={"X-Hub-Signature-256": signature})
        assert response.status_code == 200, response.text
        new = self.messenger.sent_texts[sent_before:]
        assert all(recipient == CUSTOMER_IGSID for recipient, _ in new)
        return "\n---\n".join(text for _, text in new)


@pytest.fixture
def scenario(app: FastAPI, db: Session, client: Any, tmp_path: Path, make_product) -> Iterator[dict[str, Any]]:
    velvet = make_product("Красный бархат", "750", aliases=["торт красный бархат"])
    honey = make_product("Медовик", "750", aliases=["торт медовик", "торти асал"])
    llm = ScriptedLLM(
        {
            # --- first order (Russian, delivery)
            "Здравствуйте, хочу 2 торта": understanding(
                intent="CREATE_ORDER",
                secondary_intents=["GREETING"],
                entities={"items": [item("торт", 2)], "items_mode": "add", "delivery_date": DAY.isoformat()},
            ),
            "Красный бархат и медовик": understanding(
                intent="CREATE_ORDER",
                entities={"items": [item("Красный бархат", None, velvet.id), item("медовик", None, honey.id)]},
            ),
            "К 17:00, нужна доставка": understanding(
                intent="CREATE_ORDER", entities={"delivery_time": "17:00", "delivery_type": "DELIVERY"}
            ),
            "90 123 45 67, Сино, 82 мкр, дом 5, кв 10": understanding(
                intent="CREATE_ORDER", entities={"phone": "90 123 45 67", "address": "Сино, 82 мкр, дом 5, кв 10"}
            ),
            # --- second order (Tajik, pickup)
            "Салом! Боз як медовик мехоҳам, пасфардо соати 12, худам мегирам": understanding(
                language="tg",
                intent="CREATE_ORDER",
                secondary_intents=["GREETING"],
                entities={
                    "items": [item("медовик", 1, honey.id)],
                    "delivery_date": SECOND_DAY.isoformat(),
                    "delivery_time": "12:00",
                    "delivery_type": "PICKUP",
                },
            ),
        }
    )
    messenger = FakeMessenger(profile={"name": "Алия Каримова", "username": "aliya.k"})
    queue = InlineQueue(db, messenger=messenger, llm=llm, geocoder=FakeGeocoder(), media=MediaStorage(tmp_path))
    webhook_settings = Settings(_env_file=None, APP_ENV="test", INSTAGRAM_APP_SECRET=APP_SECRET, JWT_SECRET="x" * 40)
    app.dependency_overrides[get_task_queue] = lambda: queue
    app.dependency_overrides[get_settings] = lambda: webhook_settings
    yield {
        "user": InstagramUser(client, messenger),
        "llm": llm,
        "messenger": messenger,
        "velvet": velvet,
        "honey": honey,
    }


def test_spec_49_readiness_scenario(
    client: Any, db: Session, admin_headers: dict[str, str], scenario: dict[str, Any]
) -> None:
    user: InstagramUser = scenario["user"]
    day_text = f"{DAY.day:02d}.{DAY.month:02d}.{DAY.year}"

    # 1–5. The customer writes; the system receives the message, identifies the customer and answers.
    answer = user.says("Здравствуйте, хочу 2 торта")
    assert answer == "Здравствуйте! Подскажите, пожалуйста, какие именно? Сейчас есть: Красный бархат, Медовик."
    customer = db.scalars(select(Customer)).one()
    assert customer.instagram_user_id == CUSTOMER_IGSID and customer.name == "Алия Каримова"
    # 9. The draft exists from the first ordering message.
    draft = db.scalars(select(Order)).one()
    assert draft.status == OrderStatus.NEW and draft.conversation_id is not None

    # 6–8. The customer picks the products; the bot takes products and quantities and asks for the rest.
    assert user.says("Красный бархат и медовик") == (
        "Уточните, пожалуйста:\n1. К какому времени?\n2. Это будет доставка или самовывоз?"
    )
    assert user.says("К 17:00, нужна доставка") == (
        "Уточните, пожалуйста:\n1. Напишите, пожалуйста, номер телефона для связи.\n"
        "2. Напишите, пожалуйста, адрес доставки: район, улица, дом, квартира и ориентир."
    )

    # 10. The summary (the address was geocoded synchronously, the customer is the recipient).
    summary = user.says("90 123 45 67, Сино, 82 мкр, дом 5, кв 10")
    assert summary == (
        "Проверьте, пожалуйста, заказ:\n\n"
        "Красный бархат — 1 шт.\nМедовик — 1 шт.\n\n"
        f"Дата: {day_text}\nВремя: 17:00\nДоставка: да\nАдрес: Сино, 82 мкр, дом 5, кв 10\n"
        "Получатель: Алия Каримова, +992901234567\n\n"
        "Итого: 1 500 сомони.\n\n"
        "Всё верно? Напишите «Да», чтобы подтвердить."
    )
    assert user.says("ну вроде") == (
        "Чтобы подтвердить заказ, напишите, пожалуйста, «Да». Если нужно что-то изменить — напишите, что именно."
    )

    # 11–12. Explicit confirmation → CONFIRMED.
    confirmation = user.says("Да")
    order = db.scalars(select(Order)).one()
    assert confirmation.startswith(f"Спасибо! Заказ №{order.id} подтверждён ✅")
    assert order.status == OrderStatus.CONFIRMED and order.is_repeat_customer is False

    # 13, 16, 17. The order is in the admin panel with its computed total and payment status.
    listed = client.get("/api/orders", headers=admin_headers).json()["items"]
    assert [(entry["id"], entry["status"], entry["total_amount"], entry["payment_status"]) for entry in listed] == [
        (order.id, "CONFIRMED", 1500.0, "UNPAID")
    ]
    detail = client.get(f"/api/orders/{order.id}", headers=admin_headers).json()
    assert detail["source"] == "INSTAGRAM" and detail["delivery_type"] == "DELIVERY" and detail["missing_fields"] == []
    paid = client.post(
        f"/api/orders/{order.id}/payments",
        json={"kind": "PAYMENT", "amount": 1500, "method": "CASH"},
        headers=admin_headers,
    )
    assert paid.status_code in (200, 201) and paid.json()["payment_status"] == "PAID"

    # 14. The customer is in the database, still "new".
    customers = client.get("/api/customers", params={"search": "Алия"}, headers=admin_headers).json()["items"]
    assert [(entry["id"], entry["customer_type"]) for entry in customers] == [(customer.id, "NEW")]

    # 18. Production summary for the date.
    production = client.get("/api/production", params={"date": DAY.isoformat()}, headers=admin_headers).json()
    assert production["orders_count"] == 1
    assert sorted((entry["product_name"], entry["quantity"]) for entry in production["items"]) == [
        ("Красный бархат", 1),
        ("Медовик", 1),
    ]

    # 19–20. The delivery is in the delivery section, geocoded.
    deliveries = client.get("/api/deliveries", params={"date": DAY.isoformat()}, headers=admin_headers).json()
    assert len(deliveries) == 1
    assert deliveries[0]["order"]["id"] == order.id and deliveries[0]["geocode_status"] == "OK"
    assert deliveries[0]["latitude"] is not None and deliveries[0]["recipient_name"] == "Алия Каримова"

    # 21. Route optimization from the kitchen.
    settings = client.put(
        "/api/settings",
        json={"warehouse": {"name": "Кухня", "address": "ул. Айни, 12", "latitude": 38.575, "longitude": 68.79}},
        headers=admin_headers,
    )
    assert settings.status_code == 200
    route = client.post("/api/deliveries/optimize", json={"date": DAY.isoformat()}, headers=admin_headers)
    assert route.status_code == 200, route.text
    assert [(stop["sequence"], stop["order_id"]) for stop in route.json()["stops"]] == [(1, order.id)]

    # 22. The daily report.
    report = client.post("/api/reports/daily/generate", json={"date": DAY.isoformat()}, headers=admin_headers)
    assert report.status_code == 200
    assert "1 500 сомони" in report.json()["text"]

    # 15. After a completed order the customer is regular; the next order is counted as repeat.
    for status in ("PREPARING", "READY", "HANDED_TO_COURIER", "COMPLETED"):
        moved = client.post(f"/api/orders/{order.id}/status", json={"status": status}, headers=admin_headers)
        assert moved.status_code == 200, (status, moved.text)
    assert client.get(f"/api/customers/{customer.id}", headers=admin_headers).json()["customer_type"] == "REGULAR"

    # 5. The same customer now writes in Tajik and gets Tajik answers.
    tajik_summary = user.says("Салом! Боз як медовик мехоҳам, пасфардо соати 12, худам мегирам")
    assert tajik_summary.startswith("Салом! Лутфан, фармоишро санҷед:\n\nМедовик — 1 дона")
    assert tajik_summary.endswith("Ҳама дуруст? Барои тасдиқ «Ҳа» нависед.")
    tajik_confirmation = user.says("Ҳа")
    second = db.scalars(select(Order).where(Order.id != order.id)).one()
    assert tajik_confirmation.startswith(f"Ташаккур! Фармоиши №{second.id} тасдиқ шуд ✅")
    assert second.status == OrderStatus.CONFIRMED and second.is_repeat_customer is True
    assert len(db.scalars(select(Customer)).all()) == 1

    # 23. An operator takes the dialog over: the bot is silent, the operator answers from the panel.
    conversation = db.scalars(select(Conversation)).one()
    taken = client.post(
        f"/api/conversations/{conversation.id}/handoff", json={"reason": "Уточнить дизайн"}, headers=admin_headers
    )
    assert taken.status_code == 200 and taken.json()["mode"] == "HUMAN_HANDOFF"
    assert user.says("А можно надпись на торте?") == ""
    db.refresh(conversation)
    assert conversation.mode == ConversationMode.HUMAN_HANDOFF and conversation.needs_attention

    reply = client.post(
        f"/api/conversations/{conversation.id}/messages",
        json={"text": "Да, напишите текст надписи"},
        headers=admin_headers,
    )
    assert reply.status_code == 201
    assert scenario["messenger"].texts[-1] == "Да, напишите текст надписи"
    history = client.get(f"/api/conversations/{conversation.id}", params={"limit": 200}, headers=admin_headers).json()
    assert history["messages"][-1]["sender"] == "OPERATOR" and history["messages"][-1]["delivery_status"] == "SENT"
    bot_replies = [message for message in history["messages"] if message["sender"] == "AI"]
    assert bot_replies and all(message["delivery_status"] == "SENT" for message in bot_replies)
