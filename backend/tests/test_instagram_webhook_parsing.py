"""Instagram webhooks: subscription check, X-Hub-Signature-256, payload parsing (06 §1).

No network is involved: the payloads are the official examples from docs/research/instagram.md §4
written inline (recorded-style fixtures). Everything here must be tolerant — Meta's attachment types
change over time, batches hold up to 1000 updates and one malformed item must never drop the batch.
"""

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.config import Settings
from app.integrations.instagram import InstagramAttachment, InstagramEvent, attachment_label, should_store_media
from app.integrations.instagram.webhook import (
    SIGNATURE_HEADER,
    classify_attachment,
    parse_webhook,
    signature_secrets,
    verify_signature,
    verify_subscription,
)
from app.models.enums import MessageType

ACCOUNT_ID = "17841400000000000"  # entry[].id — the Instagram professional account id
CUSTOMER_ID = "1234567890123456"  # IGSID of the customer
MID_TEXT = "aWdfZAG1faXRlbToxOklHTWVzc2FnZAUlEOjE3ODQx..."
MID_VOICE = "aWdfZAG1faXRlbToxOklHTWVzc2FnZAUlEOjE3ODQy..."
ENTRY_TIME_MS = 1789459200123
EVENT_TIME_MS = 1789459199876
EVENT_TIME = datetime(2026, 9, 15, 7, 59, 59, 876000, tzinfo=UTC)
ENTRY_TIME = datetime(2026, 9, 15, 8, 0, 0, 123000, tzinfo=UTC)

APP_SECRET = "instagram-app-secret-0123456789"
META_SECRET = "meta-app-secret-9876543210"
VERIFY_TOKEN = "bakery-verify-token-42"
CDN_URL = "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1&signature=abc"

LOGGER_NAME = "app.integrations.instagram.webhook"


# --------------------------------------------------------------------------- fixture builders


def incoming(message: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """One ``entry[].messaging[]`` item for a message from the customer to the business."""
    item: dict[str, Any] = {
        "sender": {"id": CUSTOMER_ID},
        "recipient": {"id": ACCOUNT_ID},
        "timestamp": EVENT_TIME_MS,
        "message": message,
    }
    item.update(overrides)
    return item


def envelope(*messaging: Any, entry_id: Any = ACCOUNT_ID, time_ms: Any = ENTRY_TIME_MS) -> dict[str, Any]:
    """The official webhook envelope with one ``entry`` (items may be deliberately malformed)."""
    entry: dict[str, Any] = {"id": entry_id, "time": time_ms, "messaging": list(messaging)}
    return {"object": "instagram", "entry": [entry]}


def only(payload: dict[str, Any]) -> InstagramEvent:
    events = parse_webhook(payload)
    assert len(events) == 1
    return events[0]


def events_of(records: list[logging.LogRecord]) -> list[str | None]:
    return [getattr(record, "event", None) for record in records]


# --------------------------------------------------------------------------- incoming messages


def test_text_message_webhook() -> None:
    """Official envelope + Russian text (docs/research/instagram.md §4.5)."""
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_ID,
                "time": ENTRY_TIME_MS,
                "messaging": [
                    {
                        "sender": {"id": CUSTOMER_ID},
                        "recipient": {"id": ACCOUNT_ID},
                        "timestamp": EVENT_TIME_MS,
                        "message": {
                            "mid": MID_TEXT,
                            "text": "Здравствуйте! Можно заказать торт Наполеон на субботу?",
                        },
                    }
                ],
            }
        ],
    }

    event = only(payload)

    assert event.account_id == ACCOUNT_ID
    assert event.sender_id == CUSTOMER_ID
    assert event.recipient_id == ACCOUNT_ID
    assert event.timestamp == EVENT_TIME  # unix milliseconds → aware UTC
    assert event.timestamp.tzinfo is UTC
    assert event.mid == MID_TEXT
    assert event.text == "Здравствуйте! Можно заказать торт Наполеон на субботу?"
    assert event.attachments == []
    assert (event.is_echo, event.is_self, event.is_deleted, event.is_unsupported) == (False, False, False, False)
    assert event.reply_to_mid is None
    assert event.raw_type == "text"
    assert event.message_type is MessageType.TEXT
    assert event.is_customer_message is True
    assert event.display_text == event.text
    assert event.conversation_key == f"{ACCOUNT_ID}:{CUSTOMER_ID}"


def test_voice_message_webhook() -> None:
    """Voice notes arrive as an ``audio`` attachment (docs/research/instagram.md §4.5)."""
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_ID,
                "time": 1789459260456,
                "messaging": [
                    {
                        "sender": {"id": CUSTOMER_ID},
                        "recipient": {"id": ACCOUNT_ID},
                        "timestamp": 1789459260111,
                        "message": {
                            "mid": MID_VOICE,
                            "attachments": [{"type": "audio", "payload": {"url": CDN_URL}}],
                        },
                    }
                ],
            }
        ],
    }

    event = only(payload)

    assert event.timestamp == datetime(2026, 9, 15, 8, 1, 0, 111000, tzinfo=UTC)
    assert event.text is None
    assert event.attachments == [InstagramAttachment(type="audio", url=CDN_URL)]
    assert event.message_type is MessageType.VOICE
    assert event.raw_type == "audio"
    assert event.voice_attachment is not None
    assert event.voice_attachment.url == CDN_URL
    assert event.voice_attachment.store_media is True
    assert event.attachment_labels == []
    assert event.display_text is None


def test_image_message_webhook_with_caption() -> None:
    event = only(
        envelope(
            incoming(
                {
                    "mid": "mid.image",
                    "text": "Хочу такой торт",
                    "attachments": [{"type": "image", "payload": {"url": CDN_URL}}],
                }
            )
        )
    )

    assert event.message_type is MessageType.IMAGE
    assert event.raw_type == "image"
    assert [attachment.url for attachment in event.image_attachments] == [CDN_URL]
    assert event.display_text == "Хочу такой торт"
    assert event.attachments[0].store_media is True
    assert event.attachments[0].is_story is False


def test_voice_wins_over_image_for_message_type() -> None:
    event = only(
        envelope(
            incoming(
                {
                    "mid": "mid.mixed",
                    "attachments": [
                        {"type": "image", "payload": {"url": CDN_URL}},
                        {"type": "audio", "payload": {"url": CDN_URL + "&a=2"}},
                    ],
                }
            )
        )
    )

    assert event.message_type is MessageType.VOICE
    assert event.raw_type == "image"  # raw_type keeps the first attachment as received


def test_story_mention_is_text_and_media_is_not_stored() -> None:
    story = {"mid": "mid.story", "attachments": [{"type": "story_mention", "payload": {"url": CDN_URL}}]}
    event = only(envelope(incoming(story)))

    attachment = event.attachments[0]
    assert event.message_type is MessageType.TEXT
    assert attachment.is_story is True
    assert attachment.store_media is False  # Story Mention policy: never store or cache the media
    assert attachment.url == CDN_URL  # only the CDN URL may be kept
    assert event.display_text == "[вложение: story_mention]"


@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [
        ("video", "[вложение: video]"),
        ("file", "[вложение: file]"),
        ("share", "[вложение: share]"),
        ("ig_reel", "[вложение: ig_reel]"),
        ("ig_post", "[вложение: ig_post]"),
        ("media", "[вложение: media]"),
        ("appointment_booking", "[вложение: appointment_booking]"),  # unknown/undocumented type
    ],
)
def test_other_attachment_types_become_text_with_a_label(raw_type: str, expected: str) -> None:
    event = only(envelope(incoming({"mid": "mid.x", "attachments": [{"type": raw_type, "payload": {"url": CDN_URL}}]})))

    assert event.message_type is MessageType.TEXT
    assert event.attachment_labels == [expected]
    assert event.attachments[0].store_media is False
    assert event.raw_type == raw_type


def test_attachment_without_payload_url_is_tolerated() -> None:
    event = only(
        envelope(
            incoming(
                {
                    "mid": "mid.nourl",
                    "attachments": [
                        {"type": "audio"},
                        {"type": "image", "payload": {}},
                        {"type": "image", "payload": {"url": "   "}},
                        {"payload": {"url": CDN_URL}},
                    ],
                }
            )
        )
    )

    assert [(a.type, a.url) for a in event.attachments] == [
        ("audio", None),
        ("image", None),
        ("image", None),
        ("unknown", CDN_URL),
    ]
    assert event.voice_attachment is None  # nothing to download
    assert event.image_attachments == []
    assert event.message_type is MessageType.VOICE  # the message still is a voice message
    assert event.attachments[3].store_media is False


def test_attachments_sent_as_a_single_object() -> None:
    """The docs show ``attachments`` both as an array and as a single object."""
    event = only(envelope(incoming({"mid": "mid.obj", "attachments": {"type": "audio", "payload": {"url": CDN_URL}}})))

    assert [(a.type, a.url) for a in event.attachments] == [("audio", CDN_URL)]


def test_reply_to_and_deleted_and_unsupported_flags() -> None:
    payload = envelope(
        incoming({"mid": "mid.reply", "text": "Да, подтверждаю", "reply_to": {"mid": MID_TEXT}}),
        incoming({"mid": "mid.deleted", "is_deleted": True}),
        incoming({"mid": "mid.unsupported", "is_unsupported": True}),
        incoming({"mid": "mid.story_reply", "text": "Красиво!", "reply_to": {"story": {"url": CDN_URL, "id": "9"}}}),
    )

    reply, deleted, unsupported, story_reply = parse_webhook(payload)

    assert reply.reply_to_mid == MID_TEXT
    assert deleted.is_deleted is True
    assert deleted.raw_type == "deleted"
    assert unsupported.is_unsupported is True
    assert unsupported.raw_type == "unsupported"
    assert story_reply.reply_to_mid is None  # reply_to.story has no mid
    assert story_reply.text == "Красиво!"


# --------------------------------------------------------------------------- echo / self


def test_echo_is_flagged_not_dropped() -> None:
    """``is_echo`` messages are returned with the flag set so the caller decides (06 §1)."""
    payload = envelope(
        {
            "sender": {"id": ACCOUNT_ID},
            "recipient": {"id": CUSTOMER_ID},
            "timestamp": EVENT_TIME_MS,
            "message": {"mid": "mid.echo", "text": "Здравствуйте! Отвечает бот пекарни.", "is_echo": True},
        }
    )

    event = only(payload)

    assert event.is_echo is True
    assert event.is_self is False
    assert event.is_customer_message is False
    assert event.sender_id == ACCOUNT_ID
    assert event.recipient_id == CUSTOMER_ID
    assert event.conversation_key == f"{ACCOUNT_ID}:{CUSTOMER_ID}"  # the customer is the recipient here


@pytest.mark.parametrize(
    ("item", "expected_self"),
    [
        (incoming({"mid": "m", "text": "привет", "is_self": True}), True),
        # a message to oneself: sender == recipient == the business account
        (
            {
                "sender": {"id": ACCOUNT_ID},
                "recipient": {"id": ACCOUNT_ID},
                "timestamp": EVENT_TIME_MS,
                "message": {"mid": "m", "text": "заметка"},
            },
            True,
        ),
        # sender is the business account but the message is not marked as an echo
        (
            {
                "sender": {"id": ACCOUNT_ID},
                "recipient": {"id": CUSTOMER_ID},
                "timestamp": EVENT_TIME_MS,
                "message": {"mid": "m", "text": "из приложения"},
            },
            True,
        ),
        (incoming({"mid": "m", "text": "привет"}), False),
    ],
)
def test_is_self_detection(item: dict[str, Any], expected_self: bool) -> None:
    assert only(envelope(item)).is_self is expected_self


# --------------------------------------------------------------------------- skipped event kinds


@pytest.mark.parametrize(
    "item",
    [
        {"sender": {"id": CUSTOMER_ID}, "recipient": {"id": ACCOUNT_ID}, "read": {"mid": MID_TEXT}},
        {"sender": {"id": CUSTOMER_ID}, "recipient": {"id": ACCOUNT_ID}, "seen": {"mid": MID_TEXT}},
        {
            "sender": {"id": CUSTOMER_ID},
            "recipient": {"id": ACCOUNT_ID},
            "reaction": {"mid": MID_TEXT, "action": "react", "reaction": "love", "emoji": "❤️"},
        },
        {
            "sender": {"id": CUSTOMER_ID},
            "recipient": {"id": ACCOUNT_ID},
            "postback": {"mid": MID_TEXT, "title": "Сделать заказ", "payload": "ORDER"},
        },
        {
            "sender": {"id": CUSTOMER_ID},
            "recipient": {"id": ACCOUNT_ID},
            "referral": {"ref": "promo", "source": "IGME", "type": "OPEN_THREAD"},
        },
        {
            "sender": {"id": CUSTOMER_ID},
            "recipient": {"id": ACCOUNT_ID},
            "message_edit": {"mid": MID_TEXT, "text": "исправил", "num_edit": "1"},
        },
        {"sender": {"id": CUSTOMER_ID}, "recipient": {"id": ACCOUNT_ID}, "timestamp": EVENT_TIME_MS},
    ],
)
def test_events_without_message_are_skipped(item: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    assert parse_webhook(envelope(item)) == []
    assert "instagram.webhook_malformed" not in events_of(caplog.records)  # skipped on purpose, not malformed


def test_entry_without_messaging_is_skipped() -> None:
    payload = {
        "object": "instagram",
        "entry": [{"id": ACCOUNT_ID, "time": ENTRY_TIME_MS, "changes": [{"field": "comments", "value": {}}]}],
    }

    assert parse_webhook(payload) == []


@pytest.mark.parametrize("obj", ["page", "user", "", None, 5])
def test_only_instagram_payloads_are_parsed(obj: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    payload = envelope(incoming({"mid": "m", "text": "привет"}))
    payload["object"] = obj

    assert parse_webhook(payload) == []
    assert "instagram.webhook_ignored" in events_of(caplog.records)


# --------------------------------------------------------------------------- batches & malformed items


def test_batch_keeps_delivery_order_across_entries() -> None:
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_ID,
                "time": ENTRY_TIME_MS,
                "messaging": [
                    incoming({"mid": "m1", "text": "первое"}),
                    incoming({"mid": "m2", "text": "второе"}),
                ],
            },
            {
                "id": ACCOUNT_ID,
                "time": ENTRY_TIME_MS,
                "messaging": [incoming({"mid": "m3", "text": "третье"})],
            },
        ],
    }

    assert [event.mid for event in parse_webhook(payload)] == ["m1", "m2", "m3"]


@pytest.mark.parametrize(
    ("broken", "reason", "expected_mids"),
    [
        ("not-an-object", "messaging_item_not_object", ["good"]),
        (
            {"sender": {"id": CUSTOMER_ID}, "recipient": {"id": ACCOUNT_ID}, "message": "текст"},
            "message_not_object",
            ["good"],
        ),
        (
            {"recipient": {"id": ACCOUNT_ID}, "message": {"mid": "x", "text": "нет отправителя"}},
            "missing_fields",
            ["good"],
        ),
        (
            {"sender": {"id": CUSTOMER_ID}, "message": {"mid": "x", "text": "нет получателя"}},
            "missing_fields",
            ["good"],
        ),
        (
            {"sender": {"id": CUSTOMER_ID}, "recipient": {"id": ACCOUNT_ID}, "message": {"text": "нет mid"}},
            "missing_fields",
            ["good"],
        ),
        (
            {"sender": "1234", "recipient": {"id": ACCOUNT_ID}, "message": {"mid": "x", "text": "sender не объект"}},
            "missing_fields",
            ["good"],
        ),
        # a broken attachment list is logged, but the message itself is still delivered
        (
            {
                "sender": {"id": CUSTOMER_ID},
                "recipient": {"id": ACCOUNT_ID},
                "message": {"mid": "x", "text": "привет", "attachments": "нет списка"},
            },
            "attachments_not_list",
            ["x", "good"],
        ),
        (
            {
                "sender": {"id": CUSTOMER_ID},
                "recipient": {"id": ACCOUNT_ID},
                "message": {"mid": "x", "text": "привет", "attachments": ["строка"]},
            },
            "attachment_not_object",
            ["x", "good"],
        ),
    ],
)
def test_malformed_item_is_logged_and_the_batch_survives(
    broken: Any, reason: str, expected_mids: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    payload = envelope(broken, incoming({"mid": "good", "text": "второе сообщение"}))

    events = parse_webhook(payload)

    assert [event.mid for event in events] == expected_mids
    malformed = [record for record in caplog.records if getattr(record, "event", None) == "instagram.webhook_malformed"]
    assert [record.reason for record in malformed] == [reason]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("не json-объект", "payload_not_object"),
        ({"object": "instagram"}, "entry_not_list"),
        ({"object": "instagram", "entry": {"id": ACCOUNT_ID}}, "entry_not_list"),
        ({"object": "instagram", "entry": ["строка"]}, "entry_not_object"),
        ({"object": "instagram", "entry": [{"id": ACCOUNT_ID, "messaging": "нет списка"}]}, "messaging_not_list"),
    ],
)
def test_malformed_envelope_never_raises(payload: Any, reason: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    assert parse_webhook(payload) == []
    malformed = [record for record in caplog.records if getattr(record, "event", None) == "instagram.webhook_malformed"]
    assert [record.reason for record in malformed] == [reason]


@pytest.mark.parametrize("payload", [None, [], "", 0, {"object": "instagram", "entry": [None, 1, []]}])
def test_parse_webhook_never_raises_on_junk(payload: Any) -> None:
    assert parse_webhook(payload) == []


def test_unexpected_error_in_one_item_does_not_drop_the_batch(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    class ExplodingMessage(dict):  # type: ignore[type-arg]
        def get(self, key: str, default: Any = None) -> Any:
            if key == "text":
                raise RuntimeError("boom")
            return super().get(key, default)

    payload = envelope(
        incoming(ExplodingMessage({"mid": "bad"})),
        incoming({"mid": "good", "text": "всё в порядке"}),
    )

    assert [event.mid for event in parse_webhook(payload)] == ["good"]
    malformed = [record for record in caplog.records if getattr(record, "event", None) == "instagram.webhook_malformed"]
    assert [(record.reason, record.error) for record in malformed] == [("unexpected_error", "RuntimeError")]


# --------------------------------------------------------------------------- timestamps & ids


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (EVENT_TIME_MS, EVENT_TIME),
        (str(EVENT_TIME_MS), EVENT_TIME),
        (1789459300, datetime(2026, 9, 15, 8, 1, 40, tzinfo=UTC)),  # seconds tolerated
        (None, ENTRY_TIME),  # falls back to entry.time
        ("вчера", ENTRY_TIME),
        (True, ENTRY_TIME),
    ],
)
def test_timestamp_parsing(timestamp: Any, expected: datetime) -> None:
    item = incoming({"mid": "m", "text": "привет"})
    item["timestamp"] = timestamp

    assert only(envelope(item)).timestamp == expected


def test_timestamp_missing_everywhere_falls_back_to_now(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    item = incoming({"mid": "m", "text": "привет"})
    item["timestamp"] = None

    before = datetime.now(UTC)
    event = only(envelope(item, time_ms=None))

    assert before <= event.timestamp <= datetime.now(UTC)
    assert "timestamp_missing" in [getattr(record, "reason", None) for record in caplog.records]


@pytest.mark.parametrize("entry_id", [None, "", "   ", []])
def test_missing_entry_id_falls_back_to_the_recipient(entry_id: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    event = only(envelope(incoming({"mid": "m", "text": "привет"}), entry_id=entry_id))

    assert event.account_id == ACCOUNT_ID
    assert "entry_id_missing" in [getattr(record, "reason", None) for record in caplog.records]


def test_numeric_ids_are_normalized_to_strings() -> None:
    event = only(
        envelope(
            {
                "sender": {"id": 1234567890123456},
                "recipient": {"id": 17841400000000000},
                "timestamp": EVENT_TIME_MS,
                "message": {"mid": " mid.spaces ", "text": "привет"},
            },
            entry_id=17841400000000000,
        )
    )

    assert (event.sender_id, event.recipient_id, event.mid) == (CUSTOMER_ID, ACCOUNT_ID, "mid.spaces")


def test_event_round_trips_through_json() -> None:
    """``process_instagram_event(event_dict)`` receives ``model_dump(mode="json")`` (06 §5)."""
    event = only(envelope(incoming({"mid": "m", "text": "Салом!", "attachments": [{"type": "audio"}]})))

    dumped = json.loads(json.dumps(event.model_dump(mode="json")))

    assert InstagramEvent.model_validate(dumped) == event


# --------------------------------------------------------------------------- attachment classification


@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [
        ("audio", MessageType.VOICE),
        ("AUDIO", MessageType.VOICE),
        (" audio ", MessageType.VOICE),
        ("image", MessageType.IMAGE),
        ("sticker", MessageType.IMAGE),
        ("gif", MessageType.IMAGE),
        ("video", MessageType.TEXT),
        ("file", MessageType.TEXT),
        ("share", MessageType.TEXT),
        ("ig_reel", MessageType.TEXT),
        ("reel", MessageType.TEXT),
        ("story_mention", MessageType.TEXT),
        ("ig_story", MessageType.TEXT),
        ("template", MessageType.TEXT),
        ("", MessageType.TEXT),
        (None, MessageType.TEXT),
    ],
)
def test_classify_attachment(raw_type: str | None, expected: MessageType) -> None:
    assert classify_attachment(raw_type) is expected
    assert (attachment_label(raw_type) is None) is (expected is not MessageType.TEXT)


@pytest.mark.parametrize(
    ("raw_type", "stored"),
    [("audio", True), ("image", True), ("sticker", True), ("story_mention", False), ("story", False),
     ("ig_story", False), ("video", False), ("ig_reel", False), ("unknown", False)],
)  # fmt: skip
def test_should_store_media(raw_type: str, stored: bool) -> None:
    assert should_store_media(raw_type) is stored


# --------------------------------------------------------------------------- GET verification


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "INSTAGRAM_VERIFY_TOKEN": VERIFY_TOKEN,
        "INSTAGRAM_APP_SECRET": APP_SECRET,
        "META_APP_SECRET": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_verify_subscription_echoes_the_challenge(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    assert verify_subscription("subscribe", VERIFY_TOKEN, "1158201444", make_settings()) == "1158201444"
    assert "instagram.webhook_verified" in events_of(caplog.records)


@pytest.mark.parametrize(
    ("mode", "token", "challenge", "overrides", "reason"),
    [
        ("unsubscribe", VERIFY_TOKEN, "1158201444", {}, "invalid_mode"),
        (None, VERIFY_TOKEN, "1158201444", {}, "invalid_mode"),
        ("subscribe", "wrong-token", "1158201444", {}, "token_mismatch"),
        ("subscribe", VERIFY_TOKEN + "x", "1158201444", {}, "token_mismatch"),
        ("subscribe", None, "1158201444", {}, "token_mismatch"),
        ("subscribe", VERIFY_TOKEN, None, {}, "invalid_challenge"),
        ("subscribe", VERIFY_TOKEN, "", {}, "invalid_challenge"),
        ("subscribe", VERIFY_TOKEN, "x" * 257, {}, "invalid_challenge"),
        ("subscribe", "", "1158201444", {"INSTAGRAM_VERIFY_TOKEN": ""}, "verify_token_not_configured"),
    ],
)
def test_verify_subscription_rejects(
    mode: str | None,
    token: str | None,
    challenge: str | None,
    overrides: dict[str, Any],
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    assert verify_subscription(mode, token, challenge, make_settings(**overrides)) is None
    failures = [
        record for record in caplog.records if getattr(record, "event", None) == "instagram.webhook_verification_failed"
    ]
    assert [record.reason for record in failures] == [reason]


def test_verify_token_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    verify_subscription("subscribe", VERIFY_TOKEN, "42", make_settings())
    verify_subscription("subscribe", "guess", "42", make_settings())

    dumped = " ".join(str(record.__dict__) for record in caplog.records)
    assert VERIFY_TOKEN not in dumped
    assert "guess" not in dumped


# --------------------------------------------------------------------------- POST signature


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


RAW_BODY = json.dumps(
    {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_ID,
                "time": ENTRY_TIME_MS,
                "messaging": [
                    {
                        "sender": {"id": CUSTOMER_ID},
                        "recipient": {"id": ACCOUNT_ID},
                        "timestamp": EVENT_TIME_MS,
                        "message": {"mid": MID_TEXT, "text": "Здравствуйте!"},
                    }
                ],
            }
        ],
    },
    ensure_ascii=True,  # Meta escapes unicode in the payload
).encode("utf-8")


def test_signature_header_name() -> None:
    assert SIGNATURE_HEADER == "X-Hub-Signature-256"


def test_valid_signature() -> None:
    assert verify_signature(RAW_BODY, sign(RAW_BODY), [APP_SECRET]) is True


def test_signature_is_computed_over_the_raw_bytes_not_a_reserialized_body() -> None:
    header = sign(RAW_BODY)
    reserialized = json.dumps(json.loads(RAW_BODY), ensure_ascii=False).encode("utf-8")

    assert reserialized != RAW_BODY
    assert verify_signature(reserialized, header, [APP_SECRET]) is False


def test_any_of_the_configured_secrets_may_match() -> None:
    """Which app secret Meta signs with is not documented, so the list is tried (06 §1)."""
    secrets = [APP_SECRET, META_SECRET]

    assert verify_signature(RAW_BODY, sign(RAW_BODY, APP_SECRET), secrets) is True
    assert verify_signature(RAW_BODY, sign(RAW_BODY, META_SECRET), secrets) is True
    assert verify_signature(RAW_BODY, sign(RAW_BODY, "third-secret"), secrets) is False


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "   ",
        hmac.new(APP_SECRET.encode(), RAW_BODY, hashlib.sha256).hexdigest(),  # no "sha256=" prefix
        "sha1=" + "b" * 40,  # the v1 header is not accepted
        "sha256=",
        "sha256=not-hex",
        "sha256=" + "a" * 63,
        "sha256=" + "a" * 65,
        "sha256=" + hmac.new(b"other", RAW_BODY, hashlib.sha256).hexdigest(),
        b"sha256=deadbeef",  # not a str
        42,
    ],
)
def test_invalid_signature_headers(header: Any) -> None:
    assert verify_signature(RAW_BODY, header, [APP_SECRET]) is False


def test_uppercase_hex_and_surrounding_whitespace_are_accepted() -> None:
    header = sign(RAW_BODY)
    assert verify_signature(RAW_BODY, f"  SHA256={header.split('=', 1)[1].upper()}  ", [APP_SECRET]) is True


@pytest.mark.parametrize("secrets", [[], ["", "   "], iter([])])
def test_without_secrets_the_signature_never_verifies(secrets: Any) -> None:
    assert verify_signature(RAW_BODY, sign(RAW_BODY), secrets) is False


def test_tampered_body_fails() -> None:
    assert verify_signature(RAW_BODY + b" ", sign(RAW_BODY), [APP_SECRET]) is False
    assert verify_signature(b"", sign(RAW_BODY), [APP_SECRET]) is False


def test_bytearray_and_memoryview_bodies() -> None:
    header = sign(RAW_BODY)
    assert verify_signature(bytearray(RAW_BODY), header, [APP_SECRET]) is True
    assert verify_signature(memoryview(RAW_BODY), header, [APP_SECRET]) is True


def test_str_body_is_a_programming_error() -> None:
    with pytest.raises(TypeError):
        verify_signature(RAW_BODY.decode(), sign(RAW_BODY), [APP_SECRET])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("instagram_secret", "meta_secret", "expected"),
    [
        (APP_SECRET, META_SECRET, [APP_SECRET, META_SECRET]),
        (APP_SECRET, "", [APP_SECRET]),
        ("", META_SECRET, [META_SECRET]),
        ("", "", []),
        ("  ", "  ", []),
        (f"  {APP_SECRET}  ", APP_SECRET, [APP_SECRET]),  # deduplicated, order kept
    ],
)
def test_signature_secrets(instagram_secret: str, meta_secret: str, expected: list[str]) -> None:
    settings = make_settings(INSTAGRAM_APP_SECRET=instagram_secret, META_APP_SECRET=meta_secret)

    assert signature_secrets(settings) == expected
    assert settings.instagram_app_secrets == [s for s in (instagram_secret, meta_secret) if s]
