"""Users routes (ADMIN) — docs/architecture/04-api.md §2 "Users".

- ``GET    /api/users``       → ``UserOut[]``
- ``POST   /api/users``       ``{username, full_name?, password (≥8), role}`` → ``UserOut`` 201
- ``PATCH  /api/users/{id}``  ``{full_name?, password?, role?, is_active?}`` → ``UserOut``
  (деактивация/понижение самого себя → 409 ``cannot_modify_self``)
- ``DELETE /api/users/{id}``  деактивация → 204
"""

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app.api.deps import AdminUser, DbSession
from app.schemas.common import ErrorOut
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])

UserId = Annotated[int, Path(ge=1, description="Идентификатор пользователя")]

FORBIDDEN: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorOut, "description": "Требуется авторизация"},
    403: {"model": ErrorOut, "description": "Недостаточно прав (нужна роль ADMIN)"},
}
NOT_FOUND: dict[int | str, dict[str, object]] = {404: {"model": ErrorOut, "description": "Пользователь не найден"}}
CONFLICT: dict[int | str, dict[str, object]] = {409: {"model": ErrorOut, "description": "Конфликт данных"}}


@router.get("", response_model=list[UserOut], responses=FORBIDDEN, summary="Список пользователей")
def list_users(db: DbSession, admin: AdminUser) -> list[UserOut]:
    return [UserOut.model_validate(user) for user in UserService(db).list_users()]


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    responses={**FORBIDDEN, **CONFLICT},
    summary="Создание пользователя",
)
def create_user(payload: UserCreate, db: DbSession, admin: AdminUser) -> UserOut:
    return UserOut.model_validate(UserService(db).create(payload, admin))


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    responses={**FORBIDDEN, **NOT_FOUND, **CONFLICT},
    summary="Изменение пользователя",
)
def update_user(user_id: UserId, payload: UserUpdate, db: DbSession, admin: AdminUser) -> UserOut:
    return UserOut.model_validate(UserService(db).update(user_id, payload, admin))


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={**FORBIDDEN, **NOT_FOUND, **CONFLICT},
    summary="Деактивация пользователя",
)
def deactivate_user(user_id: UserId, db: DbSession, admin: AdminUser) -> Response:
    UserService(db).deactivate(user_id, admin)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
