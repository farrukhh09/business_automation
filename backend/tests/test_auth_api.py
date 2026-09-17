"""Auth API (04-api.md §1): вход, ротация refresh-токенов, выход, текущий пользователь."""

import logging
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.models.enums import UserRole
from app.models.user import RefreshToken, User

PASSWORD = "manager-password-123"
WRONG_PASSWORD = "manager-password-124"
INVALID_CREDENTIALS = {"detail": "Неверный логин или пароль", "code": "not_authenticated"}
USER_OUT_FIELDS = {"id", "username", "full_name", "role", "is_active", "last_login_at", "created_at"}

MakeUser = Callable[..., User]


@pytest.fixture
def manager(make_user: MakeUser) -> User:
    return make_user("manager", password=PASSWORD, role=UserRole.ADMIN, full_name="Менеджер")


def _login(client: TestClient, username: str = "manager", password: str = PASSWORD) -> httpx.Response:
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _tokens(client: TestClient, username: str = "manager", password: str = PASSWORD) -> dict[str, str]:
    response = _login(client, username, password)
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _refresh_rows(db: Session) -> list[RefreshToken]:
    db.expire_all()
    return list(db.scalars(select(RefreshToken).order_by(RefreshToken.id)).all())


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == event]


# --------------------------------------------------------------------------- login


def test_login_returns_token_pair(client: TestClient, db: Session, manager: User) -> None:
    response = _login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().JWT_ACCESS_TTL_MINUTES * 60
    assert set(body["user"]) == USER_OUT_FIELDS
    assert body["user"]["id"] == manager.id
    assert body["user"]["username"] == "manager"
    assert body["user"]["role"] == "ADMIN"
    assert body["user"]["last_login_at"] is not None

    access = decode_token(body["access_token"], expected_type=ACCESS_TOKEN_TYPE)
    assert access["sub"] == str(manager.id)
    assert access["role"] == "ADMIN"

    refresh = decode_token(body["refresh_token"], expected_type=REFRESH_TOKEN_TYPE)
    rows = _refresh_rows(db)
    assert [row.jti for row in rows] == [refresh["jti"]]
    assert rows[0].user_id == manager.id
    assert rows[0].revoked_at is None
    assert manager.last_login_at is not None


def test_login_never_returns_password_hash(client: TestClient, manager: User) -> None:
    assert "password" not in _login(client).text


def test_second_login_keeps_the_first_session(client: TestClient, db: Session, manager: User) -> None:
    first = _tokens(client)
    second = _tokens(client)

    assert first["refresh_token"] != second["refresh_token"]
    assert [row.revoked_at for row in _refresh_rows(db)] == [None, None]


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("manager", WRONG_PASSWORD),  # неверный пароль
        ("ghost", PASSWORD),  # несуществующий логин
        ("MANAGER", PASSWORD),  # логин регистрозависим
    ],
)
def test_login_failures_are_indistinguishable(
    client: TestClient, db: Session, manager: User, username: str, password: str
) -> None:
    response = _login(client, username, password)

    assert response.status_code == 401
    assert response.json() == INVALID_CREDENTIALS
    assert response.headers["www-authenticate"] == "Bearer"
    assert _refresh_rows(db) == []


def test_inactive_user_cannot_login(client: TestClient, make_user: MakeUser) -> None:
    make_user("fired", password=PASSWORD, is_active=False)

    response = _login(client, "fired")

    assert response.status_code == 401
    assert response.json() == INVALID_CREDENTIALS


def test_login_updates_last_login_at(client: TestClient, db: Session, manager: User) -> None:
    assert manager.last_login_at is None

    _tokens(client)

    db.expire_all()
    assert manager.last_login_at is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "", "password": PASSWORD},
        {"username": "manager"},
        {"password": PASSWORD},
    ],
)
def test_login_validates_body(client: TestClient, payload: dict[str, str]) -> None:
    response = client.post("/api/auth/login", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_login_events_are_logged_without_password(
    client: TestClient, manager: User, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        _tokens(client)
        _login(client, "manager", WRONG_PASSWORD)

    succeeded = _events(caplog, "auth.login_succeeded")
    failed = _events(caplog, "auth.login_failed")
    assert [record.user_id for record in succeeded] == [manager.id]
    assert [record.username for record in failed] == ["manager"]
    assert failed[0].levelno == logging.WARNING
    assert failed[0].reason == "invalid_password"
    assert PASSWORD not in caplog.text
    assert WRONG_PASSWORD not in caplog.text


def test_sixth_login_attempt_is_rate_limited(client: TestClient, manager: User) -> None:
    for _ in range(5):
        assert _login(client, "manager", WRONG_PASSWORD).status_code == 401

    response = _login(client, "manager", WRONG_PASSWORD)

    assert response.status_code == 429
    assert response.json() == {"detail": "Слишком много запросов, попробуйте позже", "code": "rate_limited"}


# --------------------------------------------------------------------------- me


def test_me_returns_current_user(client: TestClient, manager: User) -> None:
    tokens = _tokens(client)

    response = client.get("/api/auth/me", headers=_bearer(tokens["access_token"]))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == USER_OUT_FIELDS
    assert body["id"] == manager.id
    assert body["full_name"] == "Менеджер"


def test_me_requires_a_token(client: TestClient) -> None:
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_me_is_available_to_operator(client: TestClient, operator_user: User, operator_headers: dict[str, str]) -> None:
    response = client.get("/api/auth/me", headers=operator_headers)

    assert response.status_code == 200
    assert response.json()["role"] == "OPERATOR"


def test_refresh_token_is_rejected_as_access_token(client: TestClient, manager: User) -> None:
    tokens = _tokens(client)

    response = client.get("/api/auth/me", headers=_bearer(tokens["refresh_token"]))

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


# --------------------------------------------------------------------------- refresh


def test_refresh_rotates_the_pair(client: TestClient, db: Session, manager: User) -> None:
    first = _tokens(client)

    response = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["user"]["id"] == manager.id

    old_jti = decode_token(first["refresh_token"], expected_type=REFRESH_TOKEN_TYPE)["jti"]
    new_jti = decode_token(second["refresh_token"], expected_type=REFRESH_TOKEN_TYPE)["jti"]
    revoked = {row.jti: row.revoked_at for row in _refresh_rows(db)}
    assert revoked[old_jti] is not None
    assert revoked[new_jti] is None

    assert client.get("/api/auth/me", headers=_bearer(second["access_token"])).status_code == 200


def test_refresh_is_logged(client: TestClient, manager: User, caplog: pytest.LogCaptureFixture) -> None:
    first = _tokens(client)

    with caplog.at_level(logging.INFO):
        client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert [record.user_id for record in _events(caplog, "auth.refresh")] == [manager.id]


def test_reusing_a_rotated_refresh_token_kills_the_chain(
    client: TestClient, db: Session, manager: User, caplog: pytest.LogCaptureFixture
) -> None:
    first = _tokens(client)
    second = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

    with caplog.at_level(logging.INFO):
        reuse = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert reuse.status_code == 401
    assert reuse.json()["code"] == "invalid_token"
    assert _events(caplog, "auth.refresh_reuse_detected")

    # Все токены пользователя отозваны, даже свежий.
    after = client.post("/api/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert after.status_code == 401
    assert after.json()["code"] == "invalid_token"
    assert all(row.revoked_at is not None for row in _refresh_rows(db))


def test_refresh_limit_is_higher_than_the_login_limit(client: TestClient, manager: User) -> None:
    """04-api.md §0: у `/auth/refresh` собственный лимит 30/мин, а не 5/мин, как у `/auth/login`."""
    token = _tokens(client)["refresh_token"]

    for _ in range(6):
        response = client.post("/api/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 200, response.text
        token = response.json()["refresh_token"]


def test_access_token_is_rejected_by_refresh(client: TestClient, manager: User) -> None:
    tokens = _tokens(client)

    response = client.post("/api/auth/refresh", json={"refresh_token": tokens["access_token"]})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


@pytest.mark.parametrize("token", ["garbage", "a.b.c"])
def test_refresh_rejects_broken_tokens(client: TestClient, token: str) -> None:
    response = client.post("/api/auth/refresh", json={"refresh_token": token})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_refresh_rejects_token_without_stored_jti(client: TestClient, manager: User) -> None:
    token = create_refresh_token(manager.id).token  # подписан, но в БД не сохранён

    response = client.post("/api/auth/refresh", json={"refresh_token": token})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_refresh_rejects_expired_token(client: TestClient, db: Session, manager: User) -> None:
    expired = create_refresh_token(manager.id, expires_delta=timedelta(days=-1))
    db.add(RefreshToken(user_id=manager.id, jti=expired.jti, expires_at=expired.expires_at))
    db.commit()

    response = client.post("/api/auth/refresh", json={"refresh_token": expired.token})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_refresh_rejects_deactivated_user(client: TestClient, db: Session, manager: User) -> None:
    tokens = _tokens(client)
    manager.is_active = False
    db.commit()

    response = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


# --------------------------------------------------------------------------- logout


def test_logout_revokes_the_refresh_token(
    client: TestClient, db: Session, manager: User, caplog: pytest.LogCaptureFixture
) -> None:
    tokens = _tokens(client)
    headers = _bearer(tokens["access_token"])

    with caplog.at_level(logging.INFO):
        response = client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=headers)

    assert response.status_code == 204
    assert response.content == b""
    assert [record.user_id for record in _events(caplog, "auth.logout")] == [manager.id]
    assert all(row.revoked_at is not None for row in _refresh_rows(db))

    assert client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_logout_is_idempotent(client: TestClient, manager: User) -> None:
    tokens = _tokens(client)
    headers = _bearer(tokens["access_token"])
    body = {"refresh_token": tokens["refresh_token"]}

    assert client.post("/api/auth/logout", json=body, headers=headers).status_code == 204
    assert client.post("/api/auth/logout", json=body, headers=headers).status_code == 204


def test_logout_accepts_an_unusable_token(client: TestClient, manager: User) -> None:
    tokens = _tokens(client)
    unknown = create_refresh_token(manager.id).token

    response = client.post(
        "/api/auth/logout",
        json={"refresh_token": unknown},
        headers=_bearer(tokens["access_token"]),
    )

    assert response.status_code == 204


def test_logout_does_not_touch_someone_elses_token(
    client: TestClient, db: Session, manager: User, make_user: MakeUser
) -> None:
    make_user("other", password=PASSWORD, role=UserRole.OPERATOR)
    other_tokens = _tokens(client, "other")
    mine = _tokens(client)

    response = client.post(
        "/api/auth/logout",
        json={"refresh_token": other_tokens["refresh_token"]},
        headers=_bearer(mine["access_token"]),
    )

    assert response.status_code == 204
    assert client.post("/api/auth/refresh", json={"refresh_token": other_tokens["refresh_token"]}).status_code == 200


def test_logout_requires_authentication(client: TestClient, manager: User) -> None:
    tokens = _tokens(client)

    response = client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_logout_rejects_a_token_of_a_deleted_user(client: TestClient, db: Session, manager: User) -> None:
    token = create_access_token(999_999, role=UserRole.ADMIN)

    response = client.post("/api/auth/logout", json={"refresh_token": "whatever"}, headers=_bearer(token))

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
