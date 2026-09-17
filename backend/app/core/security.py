"""Password hashing (pwdlib Argon2) and JWT (PyJWT, HS256) — docs/architecture/01-overview.md §1.

Tokens carry ``sub`` (user id as string), ``type`` (``access``/``refresh``), ``iat``, ``exp`` and
``jti``; access tokens also carry ``role``. Refresh-token ``jti`` values are stored in
``refresh_tokens`` by the auth service (rotation/revocation).
"""

import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import get_settings
from app.core.exceptions import InvalidTokenError, TokenExpiredError
from app.core.logging import get_logger
from app.core.time import now_utc

logger = get_logger(__name__)

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"
_RESERVED_CLAIMS = frozenset({"sub", "type", "iat", "exp", "jti", "nbf", "iss", "aud"})

_password_hash = PasswordHash((Argon2Hasher(),))
_dummy_hash: str | None = None
_ephemeral_secret: str | None = None
_lock = threading.Lock()


# --------------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password or not password_hash:
        return False
    try:
        return _password_hash.verify(password, password_hash)
    except (UnknownHashError, ValueError, TypeError):
        return False


def verify_and_update_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Returns ``(valid, new_hash_or_None)``; a new hash is returned when parameters changed."""
    try:
        return _password_hash.verify_and_update(password, password_hash)
    except (UnknownHashError, ValueError, TypeError):
        return False, None


def verify_password_dummy(password: str) -> bool:
    """Spend the same time as a real check (login for an unknown user). Always ``False``."""
    global _dummy_hash
    if _dummy_hash is None:
        with _lock:
            if _dummy_hash is None:
                _dummy_hash = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _dummy_hash)
    return False


# --------------------------------------------------------------------------- JWT


def _jwt_secret() -> str:
    global _ephemeral_secret
    settings = get_settings()
    if settings.JWT_SECRET:
        return settings.JWT_SECRET
    if settings.is_production:
        raise RuntimeError("JWT_SECRET must be configured when APP_ENV=production")
    with _lock:
        if _ephemeral_secret is None:
            _ephemeral_secret = secrets.token_urlsafe(48)
            logger.warning(
                "JWT_SECRET is not set: using an ephemeral per-process secret (development only)",
                extra={"event": "security.ephemeral_jwt_secret"},
            )
    return _ephemeral_secret


@dataclass(frozen=True, slots=True)
class RefreshTokenData:
    token: str
    jti: str
    expires_at: datetime


def create_access_token(
    subject: int | str,
    *,
    role: str | None = None,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    issued_at = now_utc()
    expires_at = issued_at + (expires_delta or timedelta(minutes=settings.JWT_ACCESS_TTL_MINUTES))
    payload: dict[str, Any] = {
        key: value for key, value in (extra_claims or {}).items() if key not in _RESERVED_CLAIMS
    }
    payload.update(
        {
            "sub": str(subject),
            "type": ACCESS_TOKEN_TYPE,
            "iat": issued_at,
            "exp": expires_at,
            "jti": uuid.uuid4().hex,
        }
    )
    if role is not None:
        payload["role"] = str(role)
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(
    subject: int | str,
    *,
    jti: str | None = None,
    expires_delta: timedelta | None = None,
) -> RefreshTokenData:
    settings = get_settings()
    issued_at = now_utc()
    expires_at = issued_at + (expires_delta or timedelta(days=settings.JWT_REFRESH_TTL_DAYS))
    token_id = jti or uuid.uuid4().hex
    payload = {
        "sub": str(subject),
        "type": REFRESH_TOKEN_TYPE,
        "iat": issued_at,
        "exp": expires_at,
        "jti": token_id,
    }
    token = jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)
    return RefreshTokenData(token=token, jti=token_id, expires_at=expires_at)


def decode_token(token: str, *, expected_type: str | None = None) -> dict[str, Any]:
    """Validate signature, expiry and required claims.

    Raises ``TokenExpiredError`` / ``InvalidTokenError`` (both → 401 ``invalid_token``).
    """
    if not token:
        raise InvalidTokenError()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "type", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError() from exc
    if payload.get("type") not in (ACCESS_TOKEN_TYPE, REFRESH_TOKEN_TYPE):
        raise InvalidTokenError()
    if expected_type is not None and payload["type"] != expected_type:
        raise InvalidTokenError("Неверный тип токена")
    return payload
