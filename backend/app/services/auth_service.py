"""Аутентификация: вход, ротация refresh-токенов, выход (04-api.md §1, SPEC §33/§35).

Правила:

- Вход не раскрывает, существует ли логин: неизвестный пользователь, неверный пароль и
  деактивированный аккаунт дают один и тот же 401 «Неверный логин или пароль». Для неизвестного
  логина всё равно проверяется фиктивный хеш (``verify_password_dummy``), чтобы время ответа не
  отличалось.
- Refresh-токены хранятся в БД по ``jti``. Обновление — ротация: старый ``jti`` отзывается, выдаётся
  новая пара. Повторное использование уже отозванного токена считается компрометацией: отзываются
  **все** refresh-токены пользователя, ответ — 401 ``invalid_token``.
- Access-токен не принимается там, где ожидается refresh (и наоборот) — проверяет ``decode_token``.
"""

import logging
from typing import NoReturn

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, InvalidTokenError
from app.core.logging import get_logger, log_event
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_and_update_password,
    verify_password_dummy,
)
from app.core.time import ensure_utc, now_utc
from app.models.user import RefreshToken, User
from app.repositories.users import RefreshTokenRepository, UserRepository
from app.schemas.auth import TokenPair
from app.schemas.user import UserOut

logger = get_logger(__name__)

INVALID_CREDENTIALS_DETAIL = "Неверный логин или пароль"
INVALID_REFRESH_DETAIL = "Недействительный токен обновления"
REUSED_REFRESH_DETAIL = "Токен обновления уже использован, войдите заново"

SECONDS_PER_MINUTE = 60


class AuthService:
    """Вход/обновление/выход. Каждая операция — одна транзакция (commit внутри сервиса)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.tokens = RefreshTokenRepository(db)

    # ------------------------------------------------------------------ login

    def login(self, username: str, password: str) -> TokenPair:
        """`POST /auth/login`. Любая ошибка → 401 с одинаковым текстом (без перебора логинов)."""
        user = self.users.get_by_username(username)
        if user is None:
            verify_password_dummy(password)
            self._login_failed(username, "unknown_user")

        valid, new_hash = verify_and_update_password(password, user.password_hash)
        if not valid:
            self._login_failed(username, "invalid_password", user_id=user.id)
        if not user.is_active:
            self._login_failed(username, "inactive_user", user_id=user.id)

        if new_hash:  # параметры Argon2 изменились — сохраняем пересчитанный хеш
            user.password_hash = new_hash
        user.last_login_at = now_utc()
        pair = self._issue_pair(user)
        self.db.commit()

        log_event(logger, "auth.login_succeeded", user_id=pair.user.id, username=pair.user.username)
        return pair

    def _login_failed(self, username: str, reason: str, user_id: int | None = None) -> NoReturn:
        """Единый отказ входа: пароль в журнал не попадает, только логин и причина."""
        log_event(
            logger,
            "auth.login_failed",
            level=logging.WARNING,
            username=username,
            reason=reason,
            user_id=user_id,
        )
        raise AuthenticationError(INVALID_CREDENTIALS_DETAIL)

    # ------------------------------------------------------------------ refresh

    def refresh(self, refresh_token: str) -> TokenPair:
        """`POST /auth/refresh`: проверка + ротация. Ошибки → 401 ``invalid_token``."""
        payload = decode_token(refresh_token, expected_type=REFRESH_TOKEN_TYPE)
        jti = payload.get("jti")
        stored = self.tokens.get_by_jti(str(jti)) if jti else None
        if stored is None:
            self._refresh_failed("unknown_token")

        if stored.revoked_at is not None:
            self._handle_reuse(stored)

        if ensure_utc(stored.expires_at) <= now_utc():
            self._refresh_failed("expired", user_id=stored.user_id)
        if str(stored.user_id) != str(payload.get("sub")):
            self._refresh_failed("subject_mismatch", user_id=stored.user_id)

        user = stored.user
        if user is None or not user.is_active:
            self._refresh_failed("inactive_user", user_id=stored.user_id)

        self.tokens.revoke(stored)  # ротация: старый токен больше не действителен
        pair = self._issue_pair(user)
        self.db.commit()

        log_event(logger, "auth.refresh", user_id=pair.user.id)
        return pair

    def _handle_reuse(self, stored: RefreshToken) -> NoReturn:
        """Повторное использование отозванного токена → отзыв всех токенов пользователя."""
        revoked = self.tokens.revoke_all_for_user(stored.user_id)
        self.db.commit()
        log_event(
            logger,
            "auth.refresh_reuse_detected",
            level=logging.WARNING,
            user_id=stored.user_id,
            revoked_tokens=revoked,
        )
        raise InvalidTokenError(REUSED_REFRESH_DETAIL)

    def _refresh_failed(self, reason: str, user_id: int | None = None) -> NoReturn:
        log_event(
            logger,
            "auth.refresh_failed",
            level=logging.WARNING,
            reason=reason,
            user_id=user_id,
        )
        raise InvalidTokenError(INVALID_REFRESH_DETAIL)

    # ------------------------------------------------------------------ logout

    def logout(self, refresh_token: str, user: User) -> None:
        """`POST /auth/logout` → 204 всегда: чужой, просроченный или уже отозванный токен игнорируется."""
        jti: str | None = None
        try:
            payload = decode_token(refresh_token, expected_type=REFRESH_TOKEN_TYPE)
        except AuthenticationError:  # просроченный/повреждённый токен — выход всё равно успешен
            payload = {}
        if payload.get("jti"):
            jti = str(payload["jti"])

        revoked = False
        stored = self.tokens.get_by_jti(jti) if jti else None
        if stored is not None and stored.user_id == user.id:
            revoked = stored.revoked_at is None
            self.tokens.revoke(stored)
        self.db.commit()

        log_event(logger, "auth.logout", user_id=user.id, revoked=revoked)

    # ------------------------------------------------------------------ helpers

    def _issue_pair(self, user: User) -> TokenPair:
        """Новая пара токенов; ``jti`` refresh-токена сохраняется в ``refresh_tokens`` (flush)."""
        settings = get_settings()
        access_token = create_access_token(user.id, role=user.role.value)
        refresh = create_refresh_token(user.id)
        self.tokens.add(RefreshToken(user_id=user.id, jti=refresh.jti, expires_at=refresh.expires_at))
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh.token,
            expires_in=settings.JWT_ACCESS_TTL_MINUTES * SECONDS_PER_MINUTE,
            user=UserOut.model_validate(user),
        )
