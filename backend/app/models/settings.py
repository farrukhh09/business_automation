"""Key-value settings edited from the admin panel (02-data-model.md: app_settings).

Typed access goes through ``SettingsService`` + ``BusinessSettings`` schema (04-api.md §12).
"""

from typing import TYPE_CHECKING, Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(sa.JSON(), nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    updated_by_user: Mapped[Optional["User"]] = relationship()
