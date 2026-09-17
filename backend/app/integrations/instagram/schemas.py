"""Normalized Instagram webhook events (06-integrations.md §1).

``InstagramEvent`` is what ``parse_webhook`` returns and what the Celery task
``process_instagram_event(event_dict)`` receives: ``event.model_dump(mode="json")`` round-trips
through ``InstagramEvent.model_validate(...)``.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.integrations.instagram.attachments import (
    attachment_label,
    classify_attachment,
    is_story_attachment,
    normalize_attachment_type,
    should_store_media,
)
from app.models.enums import MessageType


class InstagramAttachment(BaseModel):
    """``message.attachments[]`` item: ``{type, payload: {url}}``. ``url`` may be missing."""

    model_config = ConfigDict(frozen=True)

    type: str
    url: str | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: object) -> str:
        return normalize_attachment_type(value)

    @property
    def message_type(self) -> MessageType:
        return classify_attachment(self.type)

    @property
    def label(self) -> str | None:
        """``"[вложение: video]"`` for attachments stored as TEXT, ``None`` for audio/image."""
        return attachment_label(self.type)

    @property
    def is_story(self) -> bool:
        """Story mention / story media: never download or store (only the CDN URL may be kept)."""
        return is_story_attachment(self.type)

    @property
    def store_media(self) -> bool:
        """True when the media should be downloaded right away (audio/image with a URL)."""
        return self.url is not None and should_store_media(self.type)


class InstagramEvent(BaseModel):
    """One incoming ``entry[].messaging[]`` item that carries a ``message``.

    Flags are reported, not acted upon — the caller decides:

    - ``is_echo``: a message sent by the business account itself (bot, operator in the IG app);
    - ``is_self``: a message to oneself — ``message.is_self``, ``sender == recipient``, or a
      non-echo message whose sender is the business account (``sender == entry.id``);
    - ``is_deleted``: the customer unsent the message ``mid``;
    - ``is_unsupported``: Instagram could not deliver the content to the API.

    ``raw_type`` is a short description of the raw content kind, for logs and ``ai_payload``:
    ``"deleted"``, ``"text"``, the raw type of the first attachment (``"audio"``, ``"ig_reel"``,
    ``"unknown"``...), ``"unsupported"`` or ``"empty"``.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str
    sender_id: str
    recipient_id: str
    timestamp: datetime
    mid: str
    text: str | None = None
    attachments: list[InstagramAttachment] = Field(default_factory=list)
    is_echo: bool = False
    is_deleted: bool = False
    is_self: bool = False
    is_unsupported: bool = False
    reply_to_mid: str | None = None
    raw_type: str = "text"

    @field_validator("timestamp")
    @classmethod
    def _timestamp_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @property
    def is_customer_message(self) -> bool:
        """A message written by the customer (not an echo, not a message to oneself)."""
        return not (self.is_echo or self.is_self)

    @property
    def message_type(self) -> MessageType:
        """VOICE if any attachment is audio, else IMAGE if any is an image, else TEXT."""
        kinds = {attachment.message_type for attachment in self.attachments}
        if MessageType.VOICE in kinds:
            return MessageType.VOICE
        if MessageType.IMAGE in kinds:
            return MessageType.IMAGE
        return MessageType.TEXT

    @property
    def attachment_labels(self) -> list[str]:
        return [label for attachment in self.attachments if (label := attachment.label)]

    @property
    def display_text(self) -> str | None:
        """Customer text followed by ``[вложение: ...]`` labels of TEXT-type attachments."""
        parts = [self.text.strip()] if self.text and self.text.strip() else []
        parts.extend(self.attachment_labels)
        return "\n".join(parts) if parts else None

    @property
    def voice_attachment(self) -> InstagramAttachment | None:
        return next((a for a in self.attachments if a.message_type is MessageType.VOICE and a.url), None)

    @property
    def image_attachments(self) -> list[InstagramAttachment]:
        return [a for a in self.attachments if a.message_type is MessageType.IMAGE and a.url]

    @property
    def customer_id(self) -> str:
        """IGSID of the customer: the party that is not the business account.

        For an echo — and for anything else the business account sent (an operator replying from
        the Instagram app arrives with ``sender == entry.id``) — that is the recipient.
        """
        if self.is_echo or self.sender_id == self.account_id:
            return self.recipient_id
        return self.sender_id

    @property
    def conversation_key(self) -> str:
        """Thread key stored in ``conversations.instagram_conversation_id``: ``"{account_id}:{igsid}"``."""
        return f"{self.account_id}:{self.customer_id}"
