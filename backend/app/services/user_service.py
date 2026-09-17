"""Управление пользователями (04-api.md §2, SPEC §34) — только для ADMIN.

Правила:

- логин уникален: повтор → 409 ``username_taken``;
- пароль хранится только в виде Argon2-хеша и никогда не возвращается;
- администратор не может деактивировать или понизить самого себя → 409 ``cannot_modify_self``;
- смена пароля и деактивация отзывают все refresh-токены пользователя;
- ``DELETE /users/{id}`` — деактивация, строки не удаляются (на них ссылаются заказы и события).
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ValidationError
from app.core.logging import get_logger, log_event
from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.users import RefreshTokenRepository, UserRepository
from app.schemas.user import MIN_PASSWORD_LENGTH, UserCreate, UserUpdate

logger = get_logger(__name__)

USERNAME_TAKEN_CODE = "username_taken"
USERNAME_TAKEN_DETAIL = "Пользователь с таким логином уже существует"
CANNOT_MODIFY_SELF_CODE = "cannot_modify_self"
CANNOT_DEACTIVATE_SELF_DETAIL = "Нельзя деактивировать собственную учётную запись"
CANNOT_CHANGE_OWN_ROLE_DETAIL = "Нельзя изменить собственную роль"
SHORT_PASSWORD_DETAIL = f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов"


class UserService:
    """CRUD пользователей. Каждая операция — одна транзакция (commit внутри сервиса)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.tokens = RefreshTokenRepository(db)

    # ------------------------------------------------------------------ read

    def list_users(self) -> list[User]:
        return self.users.list_all()

    def get(self, user_id: int) -> User:
        return self.users.get_or_raise(user_id)

    # ------------------------------------------------------------------ create

    def create(self, data: UserCreate, actor: User | None = None) -> User:
        """`POST /users` → 201. Повтор логина → 409 ``username_taken``."""
        username = data.username.strip()
        self._check_password(data.password)
        if self.users.get_by_username(username) is not None:
            raise ConflictError(USERNAME_TAKEN_DETAIL, code=USERNAME_TAKEN_CODE)

        user = User(
            username=username,
            full_name=data.full_name,
            password_hash=hash_password(data.password),
            role=UserRole(data.role),
            is_active=True,
        )
        self.users.add(user)
        self._commit()

        log_event(
            logger,
            "user.created",
            user_id=user.id,
            username=user.username,
            role=user.role.value,
            actor_user_id=actor.id if actor else None,
        )
        return user

    # ------------------------------------------------------------------ update

    def update(self, user_id: int, data: UserUpdate, actor: User | None = None) -> User:
        """`PATCH /users/{id}`. Применяются только переданные поля."""
        user = self.users.get_or_raise(user_id)
        changes = data.model_dump(exclude_unset=True)
        is_self = actor is not None and actor.id == user.id
        changed: list[str] = []

        # Сначала проверки, потом изменения: при отказе объект остаётся нетронутым.
        role = UserRole(changes["role"]) if changes.get("role") is not None else None
        new_role = role if role is not None and role != user.role else None
        is_active = changes.get("is_active")
        deactivating = is_active is not None and not is_active and user.is_active
        password = changes.get("password")
        if password is not None:
            self._check_password(password)
        if is_self and new_role is not None:
            raise ConflictError(CANNOT_CHANGE_OWN_ROLE_DETAIL, code=CANNOT_MODIFY_SELF_CODE)
        if is_self and deactivating:
            raise ConflictError(CANNOT_DEACTIVATE_SELF_DETAIL, code=CANNOT_MODIFY_SELF_CODE)

        if new_role is not None:
            user.role = new_role
            changed.append("role")

        if is_active is not None and bool(is_active) != user.is_active:
            user.is_active = bool(is_active)
            changed.append("is_active")

        if "full_name" in changes and changes["full_name"] != user.full_name:
            user.full_name = changes["full_name"]
            changed.append("full_name")

        if password is not None:
            user.password_hash = hash_password(password)
            changed.append("password")  # в журнал попадает только имя поля

        # Смена пароля и деактивация обесценивают ранее выданные refresh-токены.
        if "password" in changed or ("is_active" in changed and not user.is_active):
            self.tokens.revoke_all_for_user(user.id)
        self._commit()

        if changed:
            log_event(
                logger,
                "user.updated",
                user_id=user.id,
                changed=sorted(changed),
                actor_user_id=actor.id if actor else None,
            )
        return user

    # ------------------------------------------------------------------ deactivate

    def deactivate(self, user_id: int, actor: User | None = None) -> User:
        """`DELETE /users/{id}` → 204: мягкая деактивация + отзыв refresh-токенов (идемпотентно)."""
        user = self.users.get_or_raise(user_id)
        if actor is not None and actor.id == user.id:
            raise ConflictError(CANNOT_DEACTIVATE_SELF_DETAIL, code=CANNOT_MODIFY_SELF_CODE)

        changed = user.is_active
        user.is_active = False
        self.tokens.revoke_all_for_user(user.id)
        self._commit()

        if changed:
            log_event(
                logger,
                "user.updated",
                user_id=user.id,
                changed=["is_active"],
                actor_user_id=actor.id if actor else None,
            )
        return user

    # ------------------------------------------------------------------ helpers

    def _check_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationError(SHORT_PASSWORD_DETAIL)

    def _commit(self) -> None:
        """Commit с переводом гонки по уникальному логину в 409 ``username_taken``."""
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictError(USERNAME_TAKEN_DETAIL, code=USERNAME_TAKEN_CODE) from exc
