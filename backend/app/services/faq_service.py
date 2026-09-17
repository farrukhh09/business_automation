"""FAQ managed from the admin panel (04-api.md §10, SPEC §14).

SPEC §14: the FAQ must not be hard-coded — the admin edits questions and answers (RU, optionally
TG) in the panel, and the bot answers from this table. Rules:

- ``question``/``answer`` are required and trimmed; the Tajik pair is optional;
- ``keywords`` are normalized exactly like product aliases (trimmed, lowercased, de-duplicated);
- the list is ordered by ``sort_order``, then ``id``; ``include_inactive`` shows disabled items;
- ``DELETE`` removes the row for good (04 §10: "физическое удаление").

The small helpers shared with the catalog (``normalize_tags``, ``validate_payload``, ``apply_patch``)
live in ``product_service``, whose aliases are their primary user.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_event
from app.models.faq import FaqItem
from app.models.user import User
from app.repositories.faq import FaqRepository
from app.schemas.faq import FaqCreate, FaqUpdate
from app.services.product_service import apply_patch, normalize_tags, validate_payload

logger = get_logger(__name__)

# ``null`` in a PATCH body clears only the optional Tajik texts; other fields stay unchanged.
CLEARABLE_FAQ_FIELDS: frozenset[str] = frozenset({"question_tg", "answer_tg"})

FAQ_INVALID_DETAIL = "Некорректные данные вопроса"


class FaqService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = FaqRepository(db)

    # ------------------------------------------------------------------ reads

    def list(self, include_inactive: bool = False) -> list[FaqItem]:
        """``GET /faq``: ordered by ``sort_order``, then ``id``."""
        return self.repository.list(include_inactive=include_inactive)

    def get(self, faq_id: int) -> FaqItem:
        return self.repository.get_or_raise(faq_id)

    # ------------------------------------------------------------------ writes

    def create(self, data: FaqCreate | Mapping[str, Any], user: User | None = None) -> FaqItem:
        payload = validate_payload(FaqCreate, data, FAQ_INVALID_DETAIL)
        item = FaqItem(
            question=payload.question,
            answer=payload.answer,
            question_tg=payload.question_tg,
            answer_tg=payload.answer_tg,
            keywords=normalize_tags(payload.keywords),
            is_active=payload.is_active,
            sort_order=payload.sort_order,
        )
        self.repository.add(item)
        self.db.commit()
        log_event(logger, "faq.created", faq_id=item.id, is_active=item.is_active, user_id=user.id if user else None)
        return item

    def update(self, faq_id: int, data: FaqUpdate | Mapping[str, Any], user: User | None = None) -> FaqItem:
        payload = validate_payload(FaqUpdate, data, FAQ_INVALID_DETAIL)
        item = self.repository.get_or_raise(faq_id)
        changed = apply_patch(
            item,
            payload.model_dump(exclude_unset=True),
            clearable=CLEARABLE_FAQ_FIELDS,
            normalized_lists=frozenset({"keywords"}),
        )
        self.repository.flush()
        self.db.commit()
        log_event(logger, "faq.updated", faq_id=faq_id, fields=sorted(changed), user_id=user.id if user else None)
        return item

    def delete(self, faq_id: int, user: User | None = None) -> None:
        """Hard delete (04 §10)."""
        item = self.repository.get_or_raise(faq_id)
        self.repository.delete(item)
        self.db.commit()
        log_event(logger, "faq.deleted", faq_id=faq_id, user_id=user.id if user else None)
