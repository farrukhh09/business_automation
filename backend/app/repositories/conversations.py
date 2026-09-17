"""Conversations and messages (02-data-model.md; 04-api.md §11)."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.enums import ConversationMode
from app.repositories.base import BaseRepository, coerce_enum
from app.repositories.customers import customer_search_condition

DEFAULT_MESSAGES_LIMIT = 50


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation
    not_found_detail = "Диалог не найден"

    def get_by_instagram_conversation_id(self, instagram_conversation_id: str) -> Conversation | None:
        stmt = select(Conversation).where(Conversation.instagram_conversation_id == instagram_conversation_id)
        return self.db.scalars(stmt).first()

    def list_filtered(
        self,
        mode: ConversationMode | str | None = None,
        needs_attention: bool | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Conversation], int]:
        """``GET /conversations``: sorted by ``-last_message_at`` (NULLs last), customer loaded."""
        stmt: Select[Any] = select(Conversation).options(selectinload(Conversation.customer))
        if mode:
            stmt = stmt.where(Conversation.mode == coerce_enum(ConversationMode, mode, "Недопустимый режим диалога"))
        if needs_attention is not None:
            stmt = stmt.where(Conversation.needs_attention.is_(needs_attention))
        if search and search.strip():
            matching_customers = select(Customer.id).where(customer_search_condition(search))
            stmt = stmt.where(Conversation.customer_id.in_(matching_customers))
        stmt = stmt.order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc())
        return self.paginate(stmt, page, page_size)

    def count_needing_attention(self) -> int:
        stmt = select(func.count()).select_from(Conversation).where(Conversation.needs_attention.is_(True))
        return int(self.db.execute(stmt).scalar_one())

    def latest_for_customer(self, customer_id: int) -> Conversation | None:
        """The customer's most recently active conversation (``CustomerDetail.conversation_id``)."""
        stmt = (
            select(Conversation)
            .where(Conversation.customer_id == customer_id)
            .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()


class MessageRepository(BaseRepository[Message]):
    model = Message
    not_found_detail = "Сообщение не найдено"

    def get_by_instagram_message_id(self, instagram_message_id: str) -> Message | None:
        return self.db.scalars(select(Message).where(Message.instagram_message_id == instagram_message_id)).first()

    def recent_for_conversation(self, conversation_id: int, limit: int = DEFAULT_MESSAGES_LIMIT) -> list[Message]:
        """The last ``limit`` messages, returned in ascending (chronological) order."""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(max(int(limit), 1))
        )
        return list(reversed(self.db.scalars(stmt).all()))

    def page_before(
        self, conversation_id: int, before_id: int, limit: int = DEFAULT_MESSAGES_LIMIT
    ) -> list[Message]:
        """Up to ``limit`` messages with ``id < before_id`` (older history), ascending order."""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.id < before_id)
            .order_by(Message.id.desc())
            .limit(max(int(limit), 1))
        )
        return list(reversed(self.db.scalars(stmt).all()))

    def last_for_conversations(self, conversation_ids: Iterable[int]) -> dict[int, Message]:
        """``{conversation_id: last message}`` in one query (list previews)."""
        ids = sorted(set(conversation_ids))
        if not ids:
            return {}
        last_ids = (
            select(func.max(Message.id))
            .where(Message.conversation_id.in_(ids))
            .group_by(Message.conversation_id)
            .scalar_subquery()
        )
        messages = self.db.scalars(select(Message).where(Message.id.in_(last_ids))).all()
        return {message.conversation_id: message for message in messages}
