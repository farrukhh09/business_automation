"""Conversations and messages (02-data-model.md: conversations, messages).

``Conversation.state`` is written only by the backend (05-ai.md §4). JSON columns track top-level
in-place changes (``conversation.state["awaiting"] = ...``); nested mutations need reassignment.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UTCDateTime, enum_type, json_dict_type
from app.models.enums import (
    ConversationMode,
    MessageDeliveryStatus,
    MessageDirection,
    MessageSender,
    MessageType,
)

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.user import User


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(sa.ForeignKey("customers.id"), nullable=False, index=True)
    # Thread key; for Instagram Login: "{ig_account_id}:{igsid}"
    instagram_conversation_id: Mapped[str | None] = mapped_column(sa.String(128), unique=True)
    mode: Mapped[ConversationMode] = mapped_column(
        enum_type(ConversationMode, "mode"),
        nullable=False,
        default=ConversationMode.AI,
        server_default=ConversationMode.AI.value,
    )
    handoff_reason: Mapped[str | None] = mapped_column(sa.Text)
    handoff_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    needs_attention: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    assigned_user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    last_customer_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    state: Mapped[dict[str, Any]] = mapped_column(json_dict_type(), nullable=False, default=dict)
    failed_ai_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")

    customer: Mapped["Customer"] = relationship(back_populates="conversations")
    assigned_user: Mapped[Optional["User"]] = relationship()
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.id",
    )


class Message(CreatedAtMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (sa.Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[MessageDirection] = mapped_column(enum_type(MessageDirection, "direction"), nullable=False)
    message_type: Mapped[MessageType] = mapped_column(enum_type(MessageType, "message_type"), nullable=False)
    sender: Mapped[MessageSender] = mapped_column(enum_type(MessageSender, "sender"), nullable=False)
    text: Mapped[str | None] = mapped_column(sa.Text)  # for VOICE: STT result
    audio_url: Mapped[str | None] = mapped_column(sa.Text)
    media_url: Mapped[str | None] = mapped_column(sa.Text)
    instagram_message_id: Mapped[str | None] = mapped_column(sa.String(255), unique=True)  # webhook idempotency
    ai_processed: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    intent: Mapped[str | None] = mapped_column(sa.String(32))
    ai_payload: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON(none_as_null=True))
    delivery_status: Mapped[MessageDeliveryStatus] = mapped_column(
        enum_type(MessageDeliveryStatus, "delivery_status"),
        nullable=False,
        default=MessageDeliveryStatus.NOT_APPLICABLE,
        server_default=MessageDeliveryStatus.NOT_APPLICABLE.value,
    )
    error: Mapped[str | None] = mapped_column(sa.Text)
    sent_by_user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    sent_by_user: Mapped[Optional["User"]] = relationship()
