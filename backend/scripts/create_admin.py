"""Create the first administrator (idempotent).

Usage (from ``backend/``, after ``alembic upgrade head``)::

    python -m scripts.create_admin

Reads ``FIRST_ADMIN_USERNAME`` (default ``admin``) and ``FIRST_ADMIN_PASSWORD`` from the
environment / ``.env``. If a user with that username already exists nothing is changed (exit 0).
Otherwise the password is required (at least 8 characters) — an empty password is refused
(exit 1). The password is never printed or logged.
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.logging import configure_logging, get_logger, log_event
from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.user import User

MIN_PASSWORD_LENGTH = 8
ADMIN_FULL_NAME = "Администратор"

logger = get_logger("scripts.create_admin")


class AdminCreationError(ValueError):
    pass


def ensure_admin(db: Session, username: str, password: str) -> tuple[User, bool]:
    """Return ``(user, created)``. Flushes but does not commit."""
    username = (username or "").strip()
    if not username:
        raise AdminCreationError("FIRST_ADMIN_USERNAME is empty")

    existing = db.scalar(select(User).where(User.username == username))
    if existing is not None:
        return existing, False

    if not password or not password.strip():
        raise AdminCreationError("FIRST_ADMIN_PASSWORD is empty: refusing to create an administrator")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminCreationError(f"FIRST_ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters")

    user = User(
        username=username,
        full_name=ADMIN_FULL_NAME,
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user, True


def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    try:
        with session_scope() as db:
            user, created = ensure_admin(db, settings.FIRST_ADMIN_USERNAME, settings.FIRST_ADMIN_PASSWORD)
            user_id, username = user.id, user.username
    except AdminCreationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if created:
        log_event(logger, "user.admin_created", user_id=user_id, username=username)
        print(f"Administrator '{username}' created (id={user_id}).")
    else:
        print(f"User '{username}' already exists (id={user_id}); nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
