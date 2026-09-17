"""Conversation schemas (docs/architecture/04-api.md §11)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.conversation import Conversation, Message
from app.models.enums import (
    ConversationMode,
    Language,
    MessageDeliveryStatus,
    MessageDirection,
    MessageSender,
    MessageType,
)
from app.schemas.common import ORMModel

MESSAGE_TEXT_MAX = 2000
HANDOFF_REASON_MAX = 500
PREVIEW_CHARS = 120

VOICE_PREVIEW = "[голосовое сообщение]"
IMAGE_PREVIEW = "[изображение]"


class ConversationCustomerRef(ORMModel):
    id: int
    name: str | None = None
    username: str | None = None
    phone: str | None = None


class ConversationListItem(BaseModel):
    id: int
    customer: ConversationCustomerRef
    mode: ConversationMode
    needs_attention: bool
    handoff_reason: str | None = None
    last_message_at: datetime | None = None
    last_message_preview: str | None = None
    active_order_id: int | None = None


class MessageOut(ORMModel):
    id: int
    direction: MessageDirection
    message_type: MessageType
    sender: MessageSender
    text: str | None = None
    audio_url: str | None = None
    media_url: str | None = None
    intent: str | None = None
    delivery_status: MessageDeliveryStatus
    error: str | None = None
    sent_by_user_id: int | None = None
    created_at: datetime


class ConversationStateSummary(BaseModel):
    draft_order_id: int | None = None
    awaiting: str | None = None
    language: Language = Language.RU


class ConversationDetail(ConversationListItem):
    messages: list[MessageOut] = Field(default_factory=list)
    state_summary: ConversationStateSummary


class SendMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=MESSAGE_TEXT_MAX)


class TestChatOut(BaseModel):
    """``/api/test-chat/{key}`` (04-api.md §11a) — a local, no-Instagram conversation with the bot."""

    conversation_id: int | None = None
    mode: ConversationMode = ConversationMode.AI
    needs_attention: bool = False
    messages: list[MessageOut] = Field(default_factory=list)


class HandoffIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=HANDOFF_REASON_MAX)


def message_preview(message: Message | None) -> str | None:
    if message is None:
        return None
    text = " ".join((message.text or "").split())
    if not text:
        if message.message_type == MessageType.VOICE:
            return VOICE_PREVIEW
        if message.message_type == MessageType.IMAGE:
            return IMAGE_PREVIEW
        return None
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"


def build_conversation_list_item(
    conversation: Conversation, last_message: Message | None, active_order_id: int | None
) -> ConversationListItem:
    return ConversationListItem(
        id=conversation.id,
        customer=ConversationCustomerRef.model_validate(conversation.customer),
        mode=conversation.mode,
        needs_attention=conversation.needs_attention,
        handoff_reason=conversation.handoff_reason,
        last_message_at=conversation.last_message_at,
        last_message_preview=message_preview(last_message),
        active_order_id=active_order_id,
    )
