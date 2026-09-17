"""Users API (04-api.md §2): CRUD только для ADMIN, деактивация вместо удаления."""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.enums import UserRole
from app.models.user import RefreshToken, User

OLD_PASSWORD = "baker-password-123"
NEW_PASSWORD = "baker-password-456"
USER_OUT_FIELDS = {"id", "username", "full_name", "role", "is_active", "last_login_at", "created_at"}

MakeUser = Callable[..., User]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, username: str, password: str = OLD_PASSWORD) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _tokens_of(db: Session, user_id: int) -> list[RefreshToken]:
    db.expire_all()
    return list(db.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id)).all())


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == event]


# --------------------------------------------------------------------------- access control


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/users", None),
        ("POST", "/api/users", {"username": "newbie", "password": OLD_PASSWORD, "role": "OPERATOR"}),
        ("PATCH", "/api/users/1", {"full_name": "Кто-то"}),
        ("DELETE", "/api/users/1", None),
    ],
)
def test_operator_is_forbidden(
    client: TestClient,
    operator_headers: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    response = client.request(method, path, json=body, headers=operator_headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "Недостаточно прав", "code": "forbidden"}


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/users"), ("POST", "/api/users"), ("PATCH", "/api/users/1"), ("DELETE", "/api/users/1")],
)
def test_anonymous_is_unauthorized(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, json={})

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


# --------------------------------------------------------------------------- list


def test_list_users(client: TestClient, admin_user: User, operator_user: User, admin_headers: dict[str, str]) -> None:
    response = client.get("/api/users", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert {item["username"] for item in body} == {"admin", "operator"}
    assert all(set(item) == USER_OUT_FIELDS for item in body)
    assert "password" not in response.text


# --------------------------------------------------------------------------- create


def test_create_user(client: TestClient, db: Session, admin_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/users",
        json={"username": "baker", "full_name": "Пекарь", "password": OLD_PASSWORD, "role": "OPERATOR"},
        headers=admin_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == USER_OUT_FIELDS
    assert body["username"] == "baker"
    assert body["full_name"] == "Пекарь"
    assert body["role"] == "OPERATOR"
    assert body["is_active"] is True
    assert body["last_login_at"] is None

    created = db.scalars(select(User).where(User.username == "baker")).one()
    assert created.password_hash != OLD_PASSWORD
    assert verify_password(OLD_PASSWORD, created.password_hash)
    assert _login(client, "baker")["user"]["id"] == created.id


def test_create_user_is_logged(client: TestClient, admin_user: User, admin_headers: dict[str, str], caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/users",
            json={"username": "baker", "password": OLD_PASSWORD, "role": "OPERATOR"},
            headers=admin_headers,
        )

    created = _events(caplog, "user.created")
    assert [record.username for record in created] == ["baker"]
    assert created[0].user_id == response.json()["id"]
    assert created[0].actor_user_id == admin_user.id
    assert OLD_PASSWORD not in caplog.text


def test_create_user_with_taken_username_is_409(
    client: TestClient, db: Session, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/users",
        json={"username": "admin", "password": OLD_PASSWORD, "role": "ADMIN"},
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Пользователь с таким логином уже существует", "code": "username_taken"}
    assert db.scalars(select(User).where(User.username == "admin")).one().id == admin_user.id


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "baker", "password": "short12", "role": "OPERATOR"},  # короче 8 символов
        {"username": "baker", "password": OLD_PASSWORD, "role": "MANAGER"},  # неизвестная роль
        {"username": "ba", "password": OLD_PASSWORD, "role": "OPERATOR"},  # слишком короткий логин
        {"username": "пекарь", "password": OLD_PASSWORD, "role": "OPERATOR"},  # недопустимые символы
        {"password": OLD_PASSWORD, "role": "OPERATOR"},  # нет логина
        {"username": "baker", "role": "OPERATOR"},  # нет пароля
        {"username": "baker", "password": OLD_PASSWORD},  # нет роли
    ],
)
def test_create_user_validation(client: TestClient, db: Session, admin_headers: dict[str, str], payload: dict[str, Any]) -> None:
    response = client.post("/api/users", json=payload, headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert db.scalars(select(User).where(User.username.in_(("baker", "ba", "пекарь")))).all() == []


# --------------------------------------------------------------------------- update


def test_patch_updates_name_and_role(
    client: TestClient, db: Session, admin_headers: dict[str, str], make_user: MakeUser
) -> None:
    target = make_user("baker", role=UserRole.OPERATOR)

    response = client.patch(
        f"/api/users/{target.id}",
        json={"full_name": "Главный пекарь", "role": "ADMIN"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Главный пекарь"
    assert body["role"] == "ADMIN"
    db.expire_all()
    assert target.role is UserRole.ADMIN
    assert target.full_name == "Главный пекарь"


def test_patch_is_logged(
    client: TestClient,
    admin_user: User,
    admin_headers: dict[str, str],
    make_user: MakeUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = make_user("baker", role=UserRole.OPERATOR)

    with caplog.at_level(logging.INFO):
        client.patch(f"/api/users/{target.id}", json={"full_name": "Пекарь"}, headers=admin_headers)

    updated = _events(caplog, "user.updated")
    assert [record.changed for record in updated] == [["full_name"]]
    assert updated[0].user_id == target.id
    assert updated[0].actor_user_id == admin_user.id


def test_patch_password_revokes_tokens_and_changes_login(
    client: TestClient, db: Session, admin_headers: dict[str, str], make_user: MakeUser
) -> None:
    target = make_user("baker", password=OLD_PASSWORD, role=UserRole.OPERATOR)
    tokens = _login(client, "baker")

    response = client.patch(f"/api/users/{target.id}", json={"password": NEW_PASSWORD}, headers=admin_headers)

    assert response.status_code == 200
    assert "password" not in response.text
    db.expire_all()
    assert verify_password(NEW_PASSWORD, target.password_hash)
    assert all(row.revoked_at is not None for row in _tokens_of(db, target.id))

    assert client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "baker", "password": OLD_PASSWORD}).status_code == 401
    assert _login(client, "baker", NEW_PASSWORD)["user"]["username"] == "baker"


def test_patch_deactivation_revokes_tokens(
    client: TestClient, db: Session, admin_headers: dict[str, str], make_user: MakeUser
) -> None:
    target = make_user("baker", password=OLD_PASSWORD, role=UserRole.OPERATOR)
    tokens = _login(client, "baker")

    response = client.patch(f"/api/users/{target.id}", json={"is_active": False}, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert all(row.revoked_at is not None for row in _tokens_of(db, target.id))
    assert client.get("/api/auth/me", headers=_bearer(tokens["access_token"])).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_patch_can_clear_full_name(client: TestClient, admin_headers: dict[str, str], make_user: MakeUser) -> None:
    target = make_user("baker", full_name="Пекарь")

    response = client.patch(f"/api/users/{target.id}", json={"full_name": None}, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["full_name"] is None


def test_patch_with_empty_body_changes_nothing(
    client: TestClient, admin_headers: dict[str, str], make_user: MakeUser
) -> None:
    target = make_user("baker", full_name="Пекарь", role=UserRole.OPERATOR)

    response = client.patch(f"/api/users/{target.id}", json={}, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["full_name"] == "Пекарь"
    assert response.json()["role"] == "OPERATOR"


def test_admin_cannot_deactivate_self(
    client: TestClient, db: Session, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = client.patch(f"/api/users/{admin_user.id}", json={"is_active": False}, headers=admin_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "Нельзя деактивировать собственную учётную запись", "code": "cannot_modify_self"}
    db.expire_all()
    assert admin_user.is_active is True


def test_admin_cannot_demote_self(
    client: TestClient, db: Session, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = client.patch(f"/api/users/{admin_user.id}", json={"role": "OPERATOR"}, headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "cannot_modify_self"
    db.expire_all()
    assert admin_user.role is UserRole.ADMIN


def test_admin_can_edit_own_profile(client: TestClient, admin_user: User, admin_headers: dict[str, str]) -> None:
    response = client.patch(
        f"/api/users/{admin_user.id}",
        json={"full_name": "Новый администратор", "role": "ADMIN", "is_active": True},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Новый администратор"


def test_patch_unknown_user_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.patch("/api/users/999999", json={"full_name": "Кто-то"}, headers=admin_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Пользователь не найден", "code": "not_found"}


def test_patch_validates_password_length(client: TestClient, admin_headers: dict[str, str], make_user: MakeUser) -> None:
    target = make_user("baker")

    response = client.patch(f"/api/users/{target.id}", json={"password": "short12"}, headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --------------------------------------------------------------------------- delete (деактивация)


def test_delete_deactivates_user(
    client: TestClient, db: Session, admin_headers: dict[str, str], make_user: MakeUser
) -> None:
    target = make_user("baker", password=OLD_PASSWORD, role=UserRole.OPERATOR)
    _login(client, "baker")

    response = client.delete(f"/api/users/{target.id}", headers=admin_headers)

    assert response.status_code == 204
    assert response.content == b""
    db.expire_all()
    assert target.is_active is False
    assert db.get(User, target.id) is not None  # строка остаётся: на неё ссылаются события заказов
    assert all(row.revoked_at is not None for row in _tokens_of(db, target.id))
    assert client.post("/api/auth/login", json={"username": "baker", "password": OLD_PASSWORD}).status_code == 401

    assert client.delete(f"/api/users/{target.id}", headers=admin_headers).status_code == 204


def test_delete_self_is_409(client: TestClient, db: Session, admin_user: User, admin_headers: dict[str, str]) -> None:
    response = client.delete(f"/api/users/{admin_user.id}", headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "cannot_modify_self"
    db.expire_all()
    assert admin_user.is_active is True


def test_delete_unknown_user_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.delete("/api/users/999999", headers=admin_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
