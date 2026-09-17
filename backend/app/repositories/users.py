"""Users and refresh tokens (02-data-model.md: users, refresh_tokens)."""

from datetime import datetime

from sqlalchemy import delete, func, select, update

from app.core.time import now_utc
from app.models.enums import UserRole
from app.models.user import RefreshToken, User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User
    not_found_detail = "Пользователь не найден"

    def get_by_username(self, username: str) -> User | None:
        """Exact (case-sensitive) match, like the unique constraint; surrounding spaces ignored."""
        return self.db.scalars(select(User).where(User.username == username.strip())).first()

    def list_all(self) -> list[User]:
        return list(self.db.scalars(select(User).order_by(User.id)).all())

    def count_active_admins(self) -> int:
        stmt = select(func.count()).select_from(User).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        return int(self.db.execute(stmt).scalar_one())


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken
    not_found_detail = "Токен не найден"

    def get_by_jti(self, jti: str) -> RefreshToken | None:
        return self.db.scalars(select(RefreshToken).where(RefreshToken.jti == jti)).first()

    def revoke(self, token: RefreshToken, at: datetime | None = None) -> None:
        """Mark one token revoked (keeps the first revocation time)."""
        if token.revoked_at is None:
            token.revoked_at = at or now_utc()
            self.db.flush()

    def revoke_all_for_user(self, user_id: int, at: datetime | None = None) -> int:
        """Revoke every not-yet-revoked token of the user; returns how many were revoked."""
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at or now_utc())
            .execution_options(synchronize_session="fetch")
        )
        result = self.db.execute(stmt)
        self.db.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    def delete_expired(self, before: datetime | None = None) -> int:
        """Delete tokens that expired before ``before`` (default: now); returns how many were deleted."""
        stmt = (
            delete(RefreshToken)
            .where(RefreshToken.expires_at < (before or now_utc()))
            .execution_options(synchronize_session="fetch")
        )
        result = self.db.execute(stmt)
        self.db.flush()
        return int(getattr(result, "rowcount", 0) or 0)
