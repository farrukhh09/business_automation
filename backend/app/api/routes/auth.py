"""Auth routes — docs/architecture/04-api.md §1 "Auth".

- ``POST /api/auth/login``   PUBLIC  ``{username, password}`` → ``TokenPair`` (rate limit ``LOGIN_LIMIT``)
- ``POST /api/auth/refresh`` PUBLIC  ``{refresh_token}`` → ``TokenPair`` (ротация; ``REFRESH_LIMIT``)
- ``POST /api/auth/logout``  STAFF   ``{refresh_token}`` → 204
- ``GET  /api/auth/me``      STAFF   → ``UserOut``

Роутеры тонкие: разбор тела, вызов ``AuthService``, возврат схемы (01-overview.md §3).
Эндпоинты с ``@limiter.limit`` обязаны принимать ``request: Request`` (app/core/rate_limit.py).
"""

from fastapi import APIRouter, Request, Response, status

from app.api.deps import DbSession, StaffUser
from app.core.rate_limit import LOGIN_LIMIT, REFRESH_LIMIT, limiter
from app.schemas.auth import LoginIn, LogoutIn, RefreshIn, TokenPair
from app.schemas.common import ErrorOut
from app.schemas.user import UserOut
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

UNAUTHORIZED: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorOut, "description": "Неверные учётные данные или токен"}
}
RATE_LIMITED: dict[int | str, dict[str, object]] = {
    429: {"model": ErrorOut, "description": "Слишком много запросов"}
}


@router.post(
    "/login",
    response_model=TokenPair,
    responses={**UNAUTHORIZED, **RATE_LIMITED},
    summary="Вход по логину и паролю",
)
@limiter.limit(LOGIN_LIMIT)
def login(request: Request, payload: LoginIn, db: DbSession) -> TokenPair:
    return AuthService(db).login(payload.username, payload.password)


@router.post(
    "/refresh",
    response_model=TokenPair,
    responses={**UNAUTHORIZED, **RATE_LIMITED},
    summary="Обновление пары токенов (ротация refresh-токена)",
)
@limiter.limit(REFRESH_LIMIT)
def refresh(request: Request, payload: RefreshIn, db: DbSession) -> TokenPair:
    return AuthService(db).refresh(payload.refresh_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=UNAUTHORIZED,
    summary="Выход: отзыв refresh-токена",
)
def logout(payload: LogoutIn, db: DbSession, user: StaffUser) -> Response:
    AuthService(db).logout(payload.refresh_token, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=UserOut,
    responses=UNAUTHORIZED,
    summary="Текущий пользователь",
)
def me(user: StaffUser) -> UserOut:
    return UserOut.model_validate(user)
