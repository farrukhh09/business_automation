"""Auth schemas — docs/architecture/04-api.md §1 "Auth".

`TokenPair = {access_token, refresh_token, token_type: "bearer", expires_in: int (сек), user: UserOut}`
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.user import MAX_PASSWORD_LENGTH, USERNAME_MAX_LENGTH, UserOut

TOKEN_TYPE = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
TOKEN_MAX_LENGTH = 4096


class LoginIn(BaseModel):
    """`POST /auth/login`: `{username, password}`."""

    model_config = ConfigDict(extra="ignore")

    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("username", mode="before")
    @classmethod
    def _strip_username(cls, value: Any) -> Any:
        # Пароль не обрезаем: пробелы в нём значимы.
        return value.strip() if isinstance(value, str) else value


class RefreshIn(BaseModel):
    """`POST /auth/refresh`: `{refresh_token}`."""

    model_config = ConfigDict(extra="ignore")

    refresh_token: str = Field(min_length=1, max_length=TOKEN_MAX_LENGTH)


class LogoutIn(BaseModel):
    """`POST /auth/logout`: `{refresh_token}`."""

    model_config = ConfigDict(extra="ignore")

    refresh_token: str = Field(min_length=1, max_length=TOKEN_MAX_LENGTH)


class TokenPair(BaseModel):
    """Пара токенов, выдаваемая при входе и при обновлении."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = TOKEN_TYPE
    expires_in: int = Field(description="Срок жизни access-токена в секундах")
    user: UserOut
