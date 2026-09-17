"""FAQ items managed from the admin panel (02-data-model.md: faq_items)."""

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, json_list_type


class FaqItem(TimestampMixin, Base):
    __tablename__ = "faq_items"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    question: Mapped[str] = mapped_column(sa.Text, nullable=False)
    answer: Mapped[str] = mapped_column(sa.Text, nullable=False)
    question_tg: Mapped[str | None] = mapped_column(sa.Text)
    answer_tg: Mapped[str | None] = mapped_column(sa.Text)
    keywords: Mapped[list[str]] = mapped_column(json_list_type(), nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, server_default=sa.text("true"))
    sort_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
