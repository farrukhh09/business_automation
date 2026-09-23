"""The price list picture the bot sends (03-business-rules.md §1.4, 04-api.md §12, 23.09.2026).

The owner asked the bot to answer a question about prices and the assortment with the photo of the
price list they send in Direct by hand. The picture is uploaded in the admin panel, stored in
``MEDIA_ROOT`` and sent by public URL, exactly as a voice reply is (06 §3).

What these tests hold in place: the upload refuses what Instagram would refuse (JPEG/PNG, ≤ 8 MB),
replacing the picture deletes the old file, the bot sends it once per dialog with a short caption
instead of the price list as text, a missing file falls back to the text, and the delivery goes
through ``send_image`` with the public URL.
"""

# ruff: noqa: F811 — pytest fixtures imported from test_dialog_service are reused as parameter names

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ValidationError
from app.models.conversation import Conversation, Message
from app.models.enums import MessageDeliveryStatus, MessageDirection, MessageSender, MessageType
from app.models.product import Product
from app.services.media_storage import PRICE_LIST_PREFIX, MediaStorage
from app.services.messaging_service import MessagingService
from app.services.price_list_service import (
    IMAGE_TOO_LARGE,
    IMAGE_TYPE_NOT_SUPPORTED,
    MAX_IMAGE_BYTES,
    PriceListService,
)
from app.services.settings_service import SettingsService
from tests.bot_fakes import FakeMessenger, ScriptedLLM, understanding
from tests.test_dialog_service import (  # noqa: F401 - fixtures are picked up by name
    Bot,
    catalog,
    make_conversation,
    reply_text,
)

JPEG = b"\xff\xd8\xff\xe0" + b"price list" * 10
URL = "/api/settings/price-list-image"


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, APP_ENV="test", MEDIA_ROOT=str(tmp_path))


def _upload(db: Session, tmp_path: Path, data: bytes = JPEG, content_type: str = "image/jpeg") -> str:
    PriceListService(db, MediaStorage(tmp_path)).save(data, content_type)
    name = SettingsService(db).get().price_list_image
    assert name is not None
    return name


# --------------------------------------------------------------------------- upload


def test_the_uploaded_picture_is_stored_and_remembered(db: Session, tmp_path: Path) -> None:
    name = _upload(db, tmp_path)

    assert name.startswith(f"{PRICE_LIST_PREFIX}-") and name.endswith(".jpg")
    assert (tmp_path / name).read_bytes() == JPEG


def test_replacing_the_picture_removes_the_previous_file(db: Session, tmp_path: Path) -> None:
    first = _upload(db, tmp_path)
    second = _upload(db, tmp_path, data=JPEG + b"new prices")

    assert second != first
    assert not (tmp_path / first).exists()
    assert (tmp_path / second).exists()


def test_clearing_removes_the_file_and_the_setting(db: Session, tmp_path: Path) -> None:
    name = _upload(db, tmp_path)

    settings = PriceListService(db, MediaStorage(tmp_path)).clear()

    assert settings.price_list_image is None
    assert not (tmp_path / name).exists()


@pytest.mark.parametrize(
    ("content_type", "size", "detail"),
    [
        pytest.param("image/webp", len(JPEG), IMAGE_TYPE_NOT_SUPPORTED, id="webp"),  # Instagram: jpeg/png only
        pytest.param("image/gif", len(JPEG), IMAGE_TYPE_NOT_SUPPORTED, id="gif"),
        pytest.param("image/jpeg", MAX_IMAGE_BYTES + 1, IMAGE_TOO_LARGE, id="too-large"),
    ],
)
def test_what_instagram_would_refuse_is_refused_at_upload(
    db: Session, tmp_path: Path, content_type: str, size: int, detail: str
) -> None:
    data = JPEG if size == len(JPEG) else b"x" * size
    with pytest.raises(ValidationError) as error:
        PriceListService(db, MediaStorage(tmp_path)).save(data, content_type)

    assert error.value.detail == detail
    assert SettingsService(db).get().price_list_image is None
    assert list(tmp_path.glob(f"{PRICE_LIST_PREFIX}-*")) == []


def test_the_api_uploads_and_deletes(client, app: FastAPI, admin_headers: dict[str, str], tmp_path: Path) -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)

    uploaded = client.post(URL, headers=admin_headers, files={"file": ("price.jpg", JPEG, "image/jpeg")})
    assert uploaded.status_code == 200
    name = uploaded.json()["price_list_image"]
    assert (tmp_path / name).exists()
    # the picture is served publicly — this is the URL Instagram downloads it from
    assert client.get(f"/api/media/{name}").content == JPEG

    bad = client.post(URL, headers=admin_headers, files={"file": ("price.webp", JPEG, "image/webp")})
    assert bad.status_code == 422

    removed = client.delete(URL, headers=admin_headers)
    assert removed.status_code == 200 and removed.json()["price_list_image"] is None
    assert not (tmp_path / name).exists()


# --------------------------------------------------------------------------- the bot sends it


@pytest.fixture
def price_question() -> ScriptedLLM:
    return ScriptedLLM({"Сколько стоит?": understanding(intent="PRODUCT_QUERY")}, default=understanding())


def _ask(bot: Bot) -> str:
    return reply_text(bot.say("Сколько стоит?"))


def test_a_price_question_is_answered_with_the_photo_and_a_short_caption(
    db: Session,
    tmp_path: Path,
    catalog: dict[str, Product],
    price_question: ScriptedLLM,
    make_conversation: Callable[..., Conversation],
) -> None:
    name = _upload(db, tmp_path)
    bot = Bot(db, make_conversation(), price_question)
    bot.service._media = MediaStorage(tmp_path)

    outcome = bot.service.handle_incoming(bot.conversation, _incoming(db, bot.conversation, "Сколько стоит?"))

    assert outcome.image == name
    assert outcome.reply is not None
    assert outcome.reply.text.startswith("Вот наш прайс-лист 📋 Цены за штуку.")
    assert "Медовик" not in outcome.reply.text  # the list is on the picture, not under it
    assert bot.state.price_list_sent == name


def test_the_same_dialog_gets_the_photo_once(
    db: Session,
    tmp_path: Path,
    catalog: dict[str, Product],
    price_question: ScriptedLLM,
    make_conversation: Callable[..., Conversation],
) -> None:
    name = _upload(db, tmp_path)
    bot = Bot(db, make_conversation(), price_question)
    bot.service._media = MediaStorage(tmp_path)
    conversation = bot.conversation

    first = bot.service.handle_incoming(conversation, _incoming(db, conversation, "Сколько стоит?"))
    second = bot.service.handle_incoming(conversation, _incoming(db, conversation, "Сколько стоит?"))

    assert first.image == name
    assert second.image is None
    # asked again, the customer gets the prices as text instead of the same picture
    assert second.reply is not None and "Медовик" in second.reply.text


def test_a_new_picture_is_sent_again(
    db: Session,
    tmp_path: Path,
    catalog: dict[str, Product],
    price_question: ScriptedLLM,
    make_conversation: Callable[..., Conversation],
) -> None:
    _upload(db, tmp_path)
    bot = Bot(db, make_conversation(), price_question)
    bot.service._media = MediaStorage(tmp_path)
    conversation = bot.conversation
    bot.service.handle_incoming(conversation, _incoming(db, conversation, "Сколько стоит?"))

    replaced = _upload(db, tmp_path, data=JPEG + b"new prices")
    again = bot.service.handle_incoming(conversation, _incoming(db, conversation, "Сколько стоит?"))

    assert again.image == replaced  # the prices on it have changed — the customer has the old one


def test_a_missing_file_falls_back_to_the_price_list_as_text(
    db: Session,
    tmp_path: Path,
    catalog: dict[str, Product],
    price_question: ScriptedLLM,
    make_conversation: Callable[..., Conversation],
) -> None:
    name = _upload(db, tmp_path)
    (tmp_path / name).unlink()
    bot = Bot(db, make_conversation(), price_question)
    bot.service._media = MediaStorage(tmp_path)

    outcome = bot.service.handle_incoming(bot.conversation, _incoming(db, bot.conversation, "Сколько стоит?"))

    assert outcome.image is None
    assert outcome.reply is not None and "Медовик" in outcome.reply.text


def _incoming(db: Session, conversation: Conversation, text: str) -> Message:
    message = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.INCOMING,
        message_type=MessageType.TEXT,
        sender=MessageSender.CUSTOMER,
        text=text,
    )
    db.add(message)
    db.commit()
    return message


# --------------------------------------------------------------------------- delivery


def test_an_image_message_is_delivered_by_public_url(
    db: Session, tmp_path: Path, make_conversation: Callable[..., Conversation]
) -> None:
    conversation = make_conversation()
    name = _upload(db, tmp_path)
    messenger = FakeMessenger()
    settings = _settings(tmp_path).model_copy(update={"PUBLIC_BASE_URL": "https://bakery.example"})
    service = MessagingService(db, settings=settings, messenger=messenger, media=MediaStorage(tmp_path))
    message = service.create_outgoing(
        conversation,
        None,
        sender=MessageSender.AI,
        message_type=MessageType.IMAGE,
        media_url=MediaStorage.url_path(name),
    )

    service.deliver(message.id)

    assert messenger.sent_images == [(conversation.customer.instagram_user_id, f"https://bakery.example/api/media/{name}")]
    assert messenger.sent_texts == []
    db.refresh(message)
    assert message.delivery_status == MessageDeliveryStatus.SENT
