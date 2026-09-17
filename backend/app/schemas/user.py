"""User schemas — docs/architecture/04-api.md §2 "Users" (``UserOut`` is also used by §1 "Auth").

``password_hash`` is never part of a response (02-data-model.md: «Argon2, никогда не отдаётся в API»):
``UserOut`` lists the exposed fields explicitly.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import UserRole
from app.schemas.common import ORMModel

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 64  # users.username is str(64)
# Логин используется в URL и в журналах — только латиница, цифры, точка, дефис, подчёркивание.
USERNAME_PATTERN = r"^[A-Za-z0-9._-]+$"
FULL_NAME_MAX_LENGTH = 128
MIN_PASSWORD_LENGTH = 8  # 04-api.md §2: password (≥8)
MAX_PASSWORD_LENGTH = 128


def _strip_or_none(value: Any) -> Any:
    """Trim a string field; an empty string means «нет значения»."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class UserOut(ORMModel):
    """`UserOut = {id, username, full_name, role, is_active, last_login_at, created_at}`.

    Все поля присутствуют в ответе всегда (``full_name``/``last_login_at`` могут быть ``null``).
    """

    id: int
    username: str
    full_name: str | None
    role: UserRole
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    """`POST /users`: `{username, full_name?, password (≥8), role}`."""

    model_config = ConfigDict(extra="ignore")

    username: str = Field(
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        pattern=USERNAME_PATTERN,
        description="Логин (латиница, цифры, . _ -)",
    )
    full_name: str | None = Field(default=None, max_length=FULL_NAME_MAX_LENGTH)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    role: UserRole

    @field_validator("username", mode="before")
    @classmethod
    def _strip_username(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("full_name", mode="before")
    @classmethod
    def _strip_full_name(cls, value: Any) -> Any:
        return _strip_or_none(value)


class UserUpdate(BaseModel):
    """`PATCH /users/{id}`: `{full_name?, password?, role?, is_active?}`.

    Only the keys present in the request body are applied (``model_dump(exclude_unset=True)``):
    ``"full_name": null`` clears the name, ``null`` in the other fields means «не менять».
    """

    model_config = ConfigDict(extra="ignore")

    full_name: str | None = Field(default=None, max_length=FULL_NAME_MAX_LENGTH)
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    role: UserRole | None = None
    is_active: bool | None = None

    @field_validator("full_name", mode="before")
    @classmethod
    def _strip_full_name(cls, value: Any) -> Any:
        return _strip_or_none(value)
