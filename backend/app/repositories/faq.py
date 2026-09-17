"""FAQ items (02-data-model.md: faq_items; 04-api.md §10)."""

import builtins

from sqlalchemy import select

from app.models.faq import FaqItem
from app.repositories.base import BaseRepository


class FaqRepository(BaseRepository[FaqItem]):
    model = FaqItem
    not_found_detail = "Вопрос не найден"

    def list(self, include_inactive: bool = False) -> builtins.list[FaqItem]:
        """Ordered by ``sort_order``, then id; active only unless ``include_inactive``."""
        stmt = select(FaqItem)
        if not include_inactive:
            stmt = stmt.where(FaqItem.is_active.is_(True))
        stmt = stmt.order_by(FaqItem.sort_order, FaqItem.id)
        return builtins.list(self.db.scalars(stmt).all())
