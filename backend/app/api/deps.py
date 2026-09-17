"""Shared FastAPI dependencies (04-api.md §0).

- ``DbSession`` — request-scoped SQLAlchemy session (``get_db``; tests override it).
- ``CurrentUser`` — active user from ``Authorization: Bearer <access_token>``.
  401 ``not_authenticated`` (no/malformed header) or ``invalid_token`` (bad/expired token,
  wrong token type, unknown or inactive user).
- ``StaffUser`` (ADMIN or OPERATOR), ``AdminUser`` (ADMIN) — 403 ``forbidden`` otherwise.
- ``Pagination`` — ``page`` (≥1, default 1), ``page_size`` (1..100, default 20).
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AuthenticationError, InvalidTokenError, PermissionDeniedError
from app.core.security import ACCESS_TOKEN_TYPE, decode_token
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import PageParams

__all__ = [
    "AdminUser",
    "CurrentUser",
    "DbSession",
    "Pagination",
    "StaffUser",
    "get_current_user",
    "get_db",
    "get_pagination",
    "require_roles",
]

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError()
    payload = decode_token(credentials.credentials, expected_type=ACCESS_TOKEN_TYPE)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError() from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError("Пользователь не найден или деактивирован")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole | str) -> Callable[[User], User]:
    """Dependency factory: the current user must have one of ``roles``."""
    allowed = frozenset(UserRole(role) for role in roles)
    if not allowed:
        raise ValueError("require_roles() needs at least one role")

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise PermissionDeniedError()
        return user

    dependency.__name__ = "require_roles_" + "_".join(sorted(role.value.lower() for role in allowed))
    return dependency


StaffUser = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR))]
AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


def get_pagination(
    page: Annotated[int, Query(ge=1, description="Номер страницы")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Размер страницы")] = 20,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


Pagination = Annotated[PageParams, Depends(get_pagination)]
