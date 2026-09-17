"""Password hashing, JWT and auth dependencies (01-overview.md §1, 04-api.md §0)."""

import base64
import json
from collections.abc import Callable, Iterator
from datetime import timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, StaffUser, get_db, require_roles
from app.core.config import get_settings
from app.core.exceptions import InvalidTokenError, TokenExpiredError, register_exception_handlers
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    JWT_ALGORITHM,
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_and_update_password,
    verify_password,
    verify_password_dummy,
)
from app.core.time import now_utc
from app.models import User
from app.models.enums import UserRole

# --------------------------------------------------------------------------- passwords


def test_hash_password_uses_argon2_and_verifies() -> None:
    hashed = hash_password("s3cret-pass")
    assert hashed.startswith("$argon2id$")
    assert "s3cret-pass" not in hashed
    assert verify_password("s3cret-pass", hashed)
    assert not verify_password("wrong-pass", hashed)


def test_hash_password_uses_random_salt() -> None:
    first, second = hash_password("same-password"), hash_password("same-password")
    assert first != second
    assert verify_password("same-password", first)
    assert verify_password("same-password", second)


@pytest.mark.parametrize("stored", ["", None, "not-a-hash", "$2b$12$invalidbcrypthashvalue"])
def test_verify_password_with_invalid_hash_returns_false(stored: str | None) -> None:
    assert verify_password("anything", stored) is False


def test_verify_password_rejects_empty_password() -> None:
    assert verify_password("", hash_password("x-password")) is False


def test_verify_and_update_password() -> None:
    hashed = hash_password("pass-word-1")
    valid, new_hash = verify_and_update_password("pass-word-1", hashed)
    assert valid is True
    assert new_hash is None
    assert verify_and_update_password("wrong", hashed) == (False, None)


def test_verify_password_dummy_is_always_false() -> None:
    assert verify_password_dummy("whatever") is False


# --------------------------------------------------------------------------- tokens


def test_access_token_roundtrip() -> None:
    token = create_access_token(42, role=UserRole.ADMIN)
    payload = decode_token(token, expected_type=ACCESS_TOKEN_TYPE)
    assert payload["sub"] == "42"
    assert payload["type"] == "access"
    assert payload["role"] == "ADMIN"
    assert payload["jti"]
    assert payload["exp"] - payload["iat"] == get_settings().JWT_ACCESS_TTL_MINUTES * 60


def test_refresh_token_has_jti_and_expiry() -> None:
    data = create_refresh_token(7)
    payload = decode_token(data.token, expected_type=REFRESH_TOKEN_TYPE)
    assert payload["type"] == "refresh"
    assert payload["sub"] == "7"
    assert payload["jti"] == data.jti
    assert len(data.jti) >= 16
    assert "role" not in payload
    remaining = data.expires_at - now_utc()
    assert timedelta(days=13, hours=23) < remaining <= timedelta(days=14)
    assert create_refresh_token(7).jti != data.jti


def test_refresh_token_with_explicit_jti() -> None:
    data = create_refresh_token(1, jti="fixed-jti-value")
    assert decode_token(data.token)["jti"] == "fixed-jti-value"


def test_extra_claims_cannot_override_reserved_claims() -> None:
    token = create_access_token(5, extra_claims={"type": "refresh", "sub": "999", "scope": "x"})
    payload = decode_token(token, expected_type=ACCESS_TOKEN_TYPE)
    assert payload["sub"] == "5"
    assert payload["scope"] == "x"


def test_decode_rejects_wrong_token_type() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(create_refresh_token(1).token, expected_type=ACCESS_TOKEN_TYPE)
    with pytest.raises(InvalidTokenError):
        decode_token(create_access_token(1), expected_type=REFRESH_TOKEN_TYPE)


def test_expired_token_is_rejected() -> None:
    token = create_access_token(1, expires_delta=timedelta(seconds=-5))
    with pytest.raises(TokenExpiredError) as exc_info:
        decode_token(token)
    assert exc_info.value.code == "invalid_token"
    assert exc_info.value.status_code == 401


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "eyJhbGciOiJIUzI1NiJ9.e30.invalid"])
def test_malformed_token_is_rejected(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(token)


def _claims(**overrides: object) -> dict[str, object]:
    now = now_utc()
    claims: dict[str, object] = {
        "sub": "1",
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": "jti-1",
    }
    claims.update(overrides)
    return claims


def test_token_signed_with_another_secret_is_rejected() -> None:
    forged = jwt.encode(_claims(), "another-secret-" + "y" * 40, algorithm=JWT_ALGORITHM)
    with pytest.raises(InvalidTokenError):
        decode_token(forged)


def test_unsigned_token_is_rejected() -> None:
    def b64(data: dict[str, object]) -> str:
        raw = json.dumps(data, default=lambda value: int(value.timestamp())).encode()  # type: ignore[attr-defined]
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(_claims())}."
    with pytest.raises(InvalidTokenError):
        decode_token(unsigned)


@pytest.mark.parametrize("missing", ["type", "sub", "exp", "jti"])
def test_token_missing_required_claim_is_rejected(missing: str) -> None:
    claims = _claims()
    claims.pop(missing)
    token = jwt.encode(claims, get_settings().JWT_SECRET, algorithm=JWT_ALGORITHM)
    with pytest.raises(InvalidTokenError):
        decode_token(token)


def test_token_with_unknown_type_is_rejected() -> None:
    token = jwt.encode(_claims(type="magic"), get_settings().JWT_SECRET, algorithm=JWT_ALGORITHM)
    with pytest.raises(InvalidTokenError):
        decode_token(token)


# --------------------------------------------------------------------------- dependencies


@pytest.fixture
def deps_client(db: Session) -> Iterator[TestClient]:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    def me(user: CurrentUser) -> dict[str, object]:
        return {"id": user.id, "role": user.role.value}

    @app.get("/staff")
    def staff(user: StaffUser) -> dict[str, object]:
        return {"id": user.id}

    @app.get("/admin")
    def admin(user: AdminUser) -> dict[str, object]:
        return {"id": user.id}

    def _override_get_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_missing_authorization_header_is_401(deps_client: TestClient) -> None:
    response = deps_client.get("/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Требуется авторизация", "code": "not_authenticated"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_non_bearer_scheme_is_401(deps_client: TestClient) -> None:
    response = deps_client.get("/me", headers={"Authorization": "Basic YWRtaW46YWRtaW4="})
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_garbage_token_is_401_invalid_token(deps_client: TestClient) -> None:
    response = deps_client.get("/me", headers={"Authorization": "Bearer garbage"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_refresh_token_cannot_be_used_as_access_token(deps_client: TestClient, admin_user: User) -> None:
    refresh = create_refresh_token(admin_user.id).token
    response = deps_client.get("/me", headers={"Authorization": f"Bearer {refresh}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_expired_access_token_is_401(deps_client: TestClient, admin_user: User) -> None:
    token = create_access_token(admin_user.id, role=admin_user.role, expires_delta=timedelta(minutes=-1))
    response = deps_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_token_for_unknown_user_is_401(deps_client: TestClient) -> None:
    token = create_access_token(999_999, role=UserRole.ADMIN)
    response = deps_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_token_with_non_numeric_subject_is_401(deps_client: TestClient) -> None:
    token = create_access_token("abc", role=UserRole.ADMIN)
    response = deps_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_inactive_user_is_401(
    deps_client: TestClient,
    make_user: Callable[..., User],
    make_auth_headers: Callable[[User], dict[str, str]],
) -> None:
    user = make_user("fired", role=UserRole.ADMIN, is_active=False)
    response = deps_client.get("/me", headers=make_auth_headers(user))
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_operator_is_forbidden_on_admin_route(
    deps_client: TestClient, operator_user: User, operator_headers: dict[str, str]
) -> None:
    response = deps_client.get("/admin", headers=operator_headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Недостаточно прав", "code": "forbidden"}

    staff = deps_client.get("/staff", headers=operator_headers)
    assert staff.status_code == 200
    assert staff.json() == {"id": operator_user.id}


def test_admin_passes_every_role_check(deps_client: TestClient, admin_user: User, admin_headers: dict[str, str]) -> None:
    assert deps_client.get("/me", headers=admin_headers).json() == {"id": admin_user.id, "role": "ADMIN"}
    assert deps_client.get("/staff", headers=admin_headers).status_code == 200
    assert deps_client.get("/admin", headers=admin_headers).status_code == 200


def test_role_is_read_from_database_not_token(
    deps_client: TestClient, make_user: Callable[..., User]
) -> None:
    operator = make_user("sneaky", role=UserRole.OPERATOR)
    token = create_access_token(operator.id, role=UserRole.ADMIN)  # role claim is not trusted
    response = deps_client.get("/admin", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_require_roles_needs_at_least_one_role() -> None:
    with pytest.raises(ValueError):
        require_roles()
    with pytest.raises(ValueError):
        require_roles("SUPERUSER")
