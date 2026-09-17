"""Core infrastructure: settings, logging redaction, time helpers, common schemas, error handlers,
rate limiting, Celery configuration and the create_admin script."""

import importlib.util
import json
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core import time as app_time
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    GoneError,
    IntegrationError,
    IntegrationNotConfiguredError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    register_exception_handlers,
)
from app.core.logging import MASK, JsonFormatter, Redactor, SecretRedactionFilter, log_event, request_id_ctx
from app.core.rate_limit import MEMORY_STORAGE_URI, limiter, rate_limit_storage_uri
from app.core.security import verify_password
from app.models import User
from app.models.enums import UserRole
from app.schemas.common import HHMM, Money, NonNegativeMoney, Page, PageParams, PositiveMoney, quantize_money

CONTRACT_ENV_VARS = (
    "APP_ENV", "APP_SECRET_KEY", "PUBLIC_BASE_URL", "FRONTEND_PUBLIC_URL", "CORS_ORIGINS", "BUSINESS_TIMEZONE",
    "LOG_LEVEL", "MEDIA_ROOT", "DATABASE_URL", "REDIS_URL", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
    "JWT_SECRET", "JWT_ACCESS_TTL_MINUTES", "JWT_REFRESH_TTL_DAYS", "FIRST_ADMIN_USERNAME", "FIRST_ADMIN_PASSWORD",
    "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_VERIFY_TOKEN", "INSTAGRAM_APP_SECRET", "META_APP_SECRET",
    "INSTAGRAM_ACCOUNT_ID", "INSTAGRAM_GRAPH_URL", "INSTAGRAM_API_VERSION", "LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL",
    "LLM_EFFORT", "LLM_TIMEOUT_SECONDS", "LLM_FALLBACKS_ENABLED", "STT_PROVIDER", "STT_API_KEY", "TTS_PROVIDER",
    "TTS_API_KEY", "TTS_VOICE", "GEOCODER_PROVIDER", "MAPS_API_KEY", "NOMINATIM_URL", "NOMINATIM_USER_AGENT",
    "OSRM_URL", "CITY_NAME", "CITY_BBOX", "MAXIM_MODE", "MAXIM_API_KEY", "MAXIM_API_URL", "NEXT_PUBLIC_APP_NAME",
    "BACKEND_INTERNAL_URL",
)  # fmt: skip


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- settings


def test_settings_declare_every_contract_variable() -> None:
    assert set(CONTRACT_ENV_VARS) <= set(Settings.model_fields)


def test_settings_defaults_match_contract() -> None:
    settings = make_settings()
    assert settings.JWT_ACCESS_TTL_MINUTES == 30
    assert settings.JWT_REFRESH_TTL_DAYS == 14
    assert settings.LLM_MODEL == "claude-opus-5"
    assert settings.LLM_EFFORT == "medium"
    assert settings.LLM_TIMEOUT_SECONDS == 60
    assert settings.LLM_FALLBACKS_ENABLED is True
    assert settings.INSTAGRAM_GRAPH_URL == "https://graph.instagram.com"
    assert settings.INSTAGRAM_API_VERSION == "v25.0"
    assert (settings.STT_PROVIDER, settings.TTS_PROVIDER, settings.TTS_VOICE) == ("elevenlabs", "openai", "alloy")
    assert settings.GEOCODER_PROVIDER == "nominatim"
    assert settings.NOMINATIM_URL == "https://nominatim.openstreetmap.org"
    assert settings.MAXIM_MODE == "manual"
    assert settings.CITY_NAME == "Худжанд"
    assert settings.city_bbox == (40.2623896, 69.5612523, 40.3316825, 69.6740681)
    assert settings.city_center == (40.2842191, 69.6191174)
    assert settings.BUSINESS_TIMEZONE == "Asia/Dushanbe"
    assert settings.daily_report_time == time(21, 0)


def test_settings_helpers() -> None:
    settings = make_settings(
        APP_ENV="Production",
        CORS_ORIGINS=" http://localhost:3000/ , https://admin.example.com ,",
        DAILY_REPORT_TIME="21:30",
        INSTAGRAM_APP_SECRET="app-secret",
        META_APP_SECRET="",
        MEDIA_ROOT="/tmp/media",
    )
    assert settings.is_production and not settings.is_test and not settings.is_development
    assert settings.cors_origins == ["http://localhost:3000", "https://admin.example.com"]
    assert settings.daily_report_time == time(21, 30)
    assert settings.instagram_app_secrets == ["app-secret"]
    assert settings.media_root.as_posix() == "/tmp/media"
    assert settings.celery_task_always_eager is False
    # Local development without Redis has no worker: tasks run inline instead of being lost.
    assert make_settings(APP_ENV="development", REDIS_URL="").celery_task_always_eager is True
    assert make_settings(APP_ENV="development", REDIS_URL="redis://redis:6379/0").celery_task_always_eager is False
    assert make_settings(APP_ENV="production", REDIS_URL="").celery_task_always_eager is False


def test_empty_numeric_env_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ACCESS_TTL_MINUTES", "")
    monkeypatch.setenv("LLM_FALLBACKS_ENABLED", " ")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "")
    settings = make_settings()
    assert settings.JWT_ACCESS_TTL_MINUTES == 30
    assert settings.LLM_FALLBACKS_ENABLED is True
    assert settings.LLM_TIMEOUT_SECONDS == 60


@pytest.mark.parametrize(
    "overrides",
    [
        {"CITY_BBOX": "38.48,68.65,38.65"},
        {"CITY_BBOX": "38.65,68.65,38.48,68.90"},
        {"BUSINESS_TIMEZONE": "Mars/Olympus"},
        {"APP_ENV": "staging"},
        {"DAILY_REPORT_TIME": "9pm"},
        {"GEOCODER_PROVIDER": "yandex"},
        {"MAXIM_MODE": "robot"},
    ],
)
def test_settings_reject_invalid_values(overrides: dict[str, str]) -> None:
    with pytest.raises(PydanticValidationError):
        make_settings(**overrides)


def test_settings_repr_hides_secrets() -> None:
    settings = make_settings(JWT_SECRET="super-secret-value-123456", LLM_API_KEY="sk-ant-api-key-000000")
    text = repr(settings)
    assert "super-secret-value-123456" not in text
    assert "sk-ant-api-key-000000" not in text
    assert "super-secret-value-123456" in settings.secret_values()
    assert "sk-ant-api-key-000000" in settings.secret_values()


def test_secret_values_include_url_passwords() -> None:
    settings = make_settings(DATABASE_URL="postgresql+psycopg://bakery:db-password-99@postgres:5432/bakery")
    assert "db-password-99" in settings.secret_values()


def test_get_settings_is_cached_and_test_configured() -> None:
    assert get_settings() is get_settings()
    assert get_settings().is_test
    assert get_settings().celery_task_always_eager is True


# --------------------------------------------------------------------------- logging


def test_redactor_masks_sensitive_keys_and_text() -> None:
    redactor = Redactor(["sk-live-1234567890"])
    data = redactor.redact(
        {
            "password": "hunter2",
            "nested": {"access_token": "abc", "input_tokens": 42, "hub.verify_token": "verify"},
            "items": [{"api_key": "k"}, ("Authorization", "x")],
            "note": "key sk-live-1234567890 used",
            "refresh_token": None,
            "summary_hash": "deadbeef",
        }
    )
    assert data["password"] == MASK
    assert data["nested"] == {"access_token": MASK, "input_tokens": 42, "hub.verify_token": MASK}
    assert data["items"][0] == {"api_key": MASK}
    assert data["note"] == f"key {MASK} used"
    assert data["refresh_token"] is None
    assert data["summary_hash"] == "deadbeef"

    text = redactor.redact_text(
        "login password=hunter2 Authorization: Bearer eyJhbGciOi.abc.def "
        "db=postgresql+psycopg://bakery:pa55word@postgres/bakery "
        "GET /refresh_access_token?grant_type=ig_refresh_token&access_token=IGQVJ123&fields=name "
        '{"client_secret": "shh"} input_tokens=15'
    )
    for secret in ("hunter2", "eyJhbGciOi", "pa55word", "IGQVJ123", "shh"):
        assert secret not in text
    assert "postgresql+psycopg://bakery:***@postgres" in text
    assert "fields=name" in text
    assert "input_tokens=15" in text


def test_redactor_masks_camel_case_and_prefixed_keys_at_any_depth() -> None:
    data = Redactor().redact(
        {
            "user": {
                "profile": [
                    {
                        "accessToken": "a1",
                        "refreshToken": "r1",
                        "clientSecret": "c1",
                        "privateKey": "k1",
                        "passwordConfirm": "p1",
                        "hub.token": "h1",
                        "xApiKey": "x1",
                    }
                ]
            },
            "tokenType": "bearer",
            "inputTokens": 7,
            "outputTokens": 9,
        }
    )
    assert set(data["user"]["profile"][0].values()) == {MASK}
    assert (data["tokenType"], data["inputTokens"], data["outputTokens"]) == ("bearer", 7, 9)


def test_redactor_masks_bare_jwts_and_quoted_values() -> None:
    from app.core.security import create_access_token, create_refresh_token

    redactor = Redactor()
    access, refresh = create_access_token(1), create_refresh_token(1).token
    text = redactor.redact_text(f"refresh failed for {refresh}; previous {access}")
    assert refresh.split(".")[1] not in text and access.split(".")[2] not in text
    assert text == f"refresh failed for {MASK}; previous {MASK}"
    assert redactor.redact_text('{"password": "hunter 2", "access_token":"abc def", "user": "bob"}') == (
        '{"password": "***", "access_token":"***", "user": "bob"}'
    )
    assert redactor.redact_text("secret='x y z' ok") == "secret='***' ok"


def test_unhandled_error_is_logged_once_with_request_id(app: FastAPI) -> None:
    class Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.setFormatter(JsonFormatter())
            self.records: list[dict[str, object]] = []

        def emit(self, record: logging.LogRecord) -> None:
            # format at emit time: request_id comes from a context variable
            self.records.append(json.loads(self.format(record)))

    @app.get("/api/test/boom")
    def boom() -> None:
        raise RuntimeError("password=topsecret exploded")

    capture = Capture()
    root = logging.getLogger()
    root.addHandler(capture)
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            response = test_client.get("/api/test/boom", headers={"X-Request-ID": "req-boom-1"})
    finally:
        root.removeHandler(capture)

    assert response.status_code == 500
    assert response.json() == {"detail": "Внутренняя ошибка сервера", "code": "internal_error"}
    errors = [r for r in capture.records if r.get("event") == "http.unhandled_error"]
    assert len(errors) == 1
    assert errors[0]["request_id"] == "req-boom-1"
    assert errors[0]["path"] == "/api/test/boom"
    assert "RuntimeError" in str(errors[0]["exception"]) and "topsecret" not in str(errors[0]["exception"])
    access = [r for r in capture.records if r.get("event") == "http.request"]
    assert access and access[-1]["status"] == 500 and access[-1]["request_id"] == "req-boom-1"


def test_unhandled_exception_handler_logs_when_middleware_did_not(
    errors_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="app.core.exceptions"):
        assert errors_client.get("/boom").status_code == 500
    assert [r for r in caplog.records if getattr(r, "event", None) == "http.unhandled_error"]


def test_access_log_masks_capability_tokens_in_path(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    from app.core.middleware import loggable_path

    assert loggable_path("/api/public/location/AbC-123_xyz") == "/api/public/location/***"
    assert loggable_path("/api/public/location/AbC-123_xyz/extra") == "/api/public/location/***/extra"
    assert loggable_path("/api/orders/12") == "/api/orders/12"
    with caplog.at_level(logging.INFO, logger="app.http"):
        client.get("/api/public/location/capability-token-123")
    records = [r for r in caplog.records if getattr(r, "event", None) == "http.request"]
    assert records and records[-1].path == "/api/public/location/***"  # type: ignore[attr-defined]
    assert all("capability-token-123" not in r.getMessage() for r in caplog.records)


def test_json_formatter_outputs_structured_redacted_record() -> None:
    formatter = JsonFormatter(Redactor())
    record = logging.LogRecord("app.orders", logging.INFO, __file__, 1, "order %s confirmed", (12,), None)
    record.event = "order.confirmed"
    record.order_id = 12
    record.refresh_token = "secret-refresh"
    token = request_id_ctx.set("req-1")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        request_id_ctx.reset(token)
    assert payload["message"] == "order 12 confirmed"
    assert payload["event"] == "order.confirmed"
    assert payload["order_id"] == 12
    assert payload["refresh_token"] == MASK
    assert payload["request_id"] == "req-1"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.orders"
    assert payload["ts"].endswith("Z")


def test_json_formatter_includes_exception() -> None:
    formatter = JsonFormatter(Redactor())
    try:
        raise RuntimeError("password=topsecret failed")
    except RuntimeError:
        record = logging.LogRecord("app", logging.ERROR, __file__, 1, "boom", (), exc_info=__import__("sys").exc_info())
    payload = json.loads(formatter.format(record))
    assert "RuntimeError" in payload["exception"]
    assert "topsecret" not in payload["exception"]


def test_secret_redaction_filter_mutates_record() -> None:
    record_filter = SecretRedactionFilter(Redactor(["very-secret-value"]))
    record = logging.LogRecord("app", logging.WARNING, __file__, 1, "value=%s", ("very-secret-value",), None)
    record.api_key = "k-123"
    record.payload = {"authorization": "Bearer abc"}
    assert record_filter.filter(record) is True
    assert record.getMessage() == f"value={MASK}"
    assert record.api_key == MASK
    assert record.payload == {"authorization": MASK}


def test_log_event_adds_event_and_fields(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("app.test.events")
    with caplog.at_level(logging.INFO, logger="app.test.events"):
        log_event(logger, "order.status_changed", order_id=5, name="clash")
    record = caplog.records[-1]
    assert record.event == "order.status_changed"  # type: ignore[attr-defined]
    assert record.order_id == 5  # type: ignore[attr-defined]
    assert record.name_ == "clash"  # type: ignore[attr-defined]


def test_access_log_has_request_fields_without_secrets(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.http"):
        client.get("/api/does-not-exist?token=query-secret", headers={"Authorization": "Bearer header-secret"})
    records = [r for r in caplog.records if getattr(r, "event", None) == "http.request"]
    assert records
    record = records[-1]
    assert record.method == "GET"  # type: ignore[attr-defined]
    assert record.path == "/api/does-not-exist"  # type: ignore[attr-defined]
    assert record.status == 404  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, float)  # type: ignore[attr-defined]
    rendered = JsonFormatter().format(record)
    assert "query-secret" not in rendered
    assert "header-secret" not in rendered


# --------------------------------------------------------------------------- time


def test_business_today_uses_business_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_time, "now_utc", lambda: datetime(2026, 9, 15, 20, 30, tzinfo=UTC))
    assert app_time.business_today() == date(2026, 9, 16)
    assert app_time.business_now().utcoffset() == timedelta(hours=5)


def test_now_utc_is_aware() -> None:
    assert app_time.now_utc().utcoffset() == timedelta(0)


def test_business_tz_constant_matches_settings() -> None:
    assert str(app_time.BUSINESS_TZ) == get_settings().BUSINESS_TIMEZONE == "Asia/Dushanbe"
    assert app_time.BUSINESS_TZ == app_time.business_tz()


def test_to_business_and_ensure_utc() -> None:
    naive = datetime(2026, 9, 15, 19, 0)
    assert app_time.ensure_utc(naive) == datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
    local = app_time.to_business(naive)
    assert (local.date(), local.hour) == (date(2026, 9, 16), 0)
    assert app_time.business_date_of(datetime(2026, 9, 15, 18, 59, tzinfo=UTC)) == date(2026, 9, 15)


def test_business_day_bounds_and_combine() -> None:
    start, end = app_time.business_day_bounds_utc(date(2026, 9, 16))
    assert start == datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 16, 19, 0, tzinfo=UTC)
    combined = app_time.combine_business(date(2026, 9, 16), time(18, 0))
    assert combined.astimezone(UTC) == datetime(2026, 9, 16, 13, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- common schemas


class MoneyModel(BaseModel):
    amount: Money
    price: PositiveMoney | None = None
    paid: NonNegativeMoney = Decimal("0")


class TimeModel(BaseModel):
    at: HHMM


def test_money_is_quantized_half_up_and_serialized_as_number() -> None:
    model = MoneyModel(amount="10.005")
    assert model.amount == Decimal("10.01")
    assert model.model_dump()["amount"] == Decimal("10.01")
    assert json.loads(model.model_dump_json()) == {"amount": 10.01, "price": None, "paid": 0.0}
    assert MoneyModel(amount=1500).model_dump_json() == '{"amount":1500.0,"price":null,"paid":0.0}'
    assert MoneyModel.model_validate_json('{"amount": 12500.5}').amount == Decimal("12500.50")
    assert quantize_money("2.675") == Decimal("2.68")
    assert quantize_money(Decimal("-1.005")) == Decimal("-1.01")


@pytest.mark.parametrize("bad", ["abc", "NaN", "Infinity", "1e20"])
def test_money_rejects_invalid_values(bad: str) -> None:
    with pytest.raises(PydanticValidationError):
        MoneyModel(amount=bad)


@pytest.mark.parametrize("bad", ["1e26", "1e30", -1e40, "10000000000.00", "9999999999.995"])
def test_money_rejects_amounts_beyond_numeric_12_2_without_crashing(bad: object) -> None:
    # decimal.InvalidOperation (not a ValueError) used to escape validation for >= 1e26 → HTTP 500
    with pytest.raises(PydanticValidationError):
        MoneyModel(amount=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw_json", ['{"amount": 1e26}', '{"amount": 1e30}', '{"amount": -1e40}'])
def test_money_rejects_huge_json_numbers(raw_json: str) -> None:
    with pytest.raises(PydanticValidationError):
        MoneyModel.model_validate_json(raw_json)


def test_quantize_money_raises_value_error_only() -> None:
    for bad in ("1e30", "abc", "NaN", None):
        with pytest.raises(ValueError):
            quantize_money(bad)  # type: ignore[arg-type]
    assert quantize_money("9999999999.99") == Decimal("9999999999.99")


def test_positive_and_non_negative_money() -> None:
    with pytest.raises(PydanticValidationError):
        MoneyModel(amount=1, price="0")
    with pytest.raises(PydanticValidationError):
        MoneyModel(amount=1, price="0.004")  # rounds to 0.00
    with pytest.raises(PydanticValidationError):
        MoneyModel(amount=1, paid="-0.01")
    assert MoneyModel(amount=1, price="0.01").price == Decimal("0.01")


def test_money_json_schema_is_number() -> None:
    properties = MoneyModel.model_json_schema()["properties"]
    assert properties["amount"]["type"] == "number"


def test_hhmm_parses_and_serializes() -> None:
    assert TimeModel(at="18:00").at == time(18, 0)
    assert TimeModel(at="09:30:45").model_dump_json() == '{"at":"09:30"}'
    assert TimeModel(at=time(7, 5, 9)).model_dump(mode="json") == {"at": "07:05"}
    assert TimeModel(at="23:59").model_dump()["at"] == time(23, 59)
    assert TimeModel.model_json_schema()["properties"]["at"]["type"] == "string"


@pytest.mark.parametrize("bad", ["24:00", "7:00", "18:60", "18-00", "", "18:00:00.5", "18:00Z", 1800])
def test_hhmm_rejects_invalid_values(bad: object) -> None:
    with pytest.raises(PydanticValidationError):
        TimeModel(at=bad)  # type: ignore[arg-type]


def test_page_is_generic() -> None:
    page = Page[TimeModel](items=[TimeModel(at="10:00")], total=3, page=1, page_size=1)
    assert json.loads(page.model_dump_json()) == {"items": [{"at": "10:00"}], "total": 3, "page": 1, "page_size": 1}
    params = PageParams(page=3, page_size=20)
    assert (params.offset, params.limit) == (40, 20)
    with pytest.raises(PydanticValidationError):
        PageParams(page=1, page_size=101)


# --------------------------------------------------------------------------- error handlers


@pytest.fixture
def errors_client() -> Iterator[TestClient]:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/not-found")
    def not_found() -> None:
        raise NotFoundError("Заказ не найден")

    @app.get("/business")
    def business() -> None:
        raise BusinessRuleError(code="order_incomplete", missing=["delivery_time"])

    @app.get("/conflict")
    def conflict() -> None:
        raise ConflictError("Окно 24 часа закрыто", code="messaging_window_closed")

    @app.get("/forbidden")
    def forbidden() -> None:
        raise PermissionDeniedError()

    @app.get("/integration")
    def integration() -> None:
        raise IntegrationError("Instagram недоступен")

    @app.get("/not-configured")
    def not_configured() -> None:
        raise IntegrationNotConfiguredError()

    @app.get("/gone")
    def gone() -> None:
        raise GoneError("Ссылка истекла")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("unexpected")

    @app.get("/validate")
    def validate(number: int) -> dict[str, int]:
        return {"number": number}

    @app.get("/domain-validation")
    def domain_validation() -> None:
        raise ValidationError("Дата доставки в прошлом")

    @app.post("/money", response_model=MoneyModel)
    def money(payload: MoneyModel) -> MoneyModel:
        return payload

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/not-found", 404, "not_found"),
        ("/business", 422, "order_incomplete"),
        ("/conflict", 409, "messaging_window_closed"),
        ("/forbidden", 403, "forbidden"),
        ("/integration", 502, "integration_error"),
        ("/not-configured", 503, "integration_not_configured"),
        ("/gone", 410, "gone"),
        ("/domain-validation", 422, "validation_error"),
        ("/boom", 500, "internal_error"),
        ("/missing-route", 404, "not_found"),
    ],
)
def test_error_responses_have_detail_and_code(errors_client: TestClient, path: str, status: int, code: str) -> None:
    response = errors_client.get(path)
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]


def test_business_rule_error_details_are_included(errors_client: TestClient) -> None:
    body = errors_client.get("/business").json()
    assert body == {
        "detail": "Не заполнены обязательные данные заказа",
        "code": "order_incomplete",
        "missing": ["delivery_time"],
    }


def test_request_validation_error_keeps_fastapi_detail(errors_client: TestClient) -> None:
    response = errors_client.get("/validate", params={"number": "abc"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["detail"], list) and body["detail"][0]["loc"] == ["query", "number"]


def test_business_rule_error_constructor_forms() -> None:
    error = BusinessRuleError("invalid_status_transition", details={"from": "NEW", "to": "COMPLETED"})
    assert error.status_code == 422
    assert error.to_dict() == {
        "detail": "Недопустимая смена статуса заказа",
        "code": "invalid_status_transition",
        "from": "NEW",
        "to": "COMPLETED",
    }
    assert BusinessRuleError("custom_rule", "Текст").to_dict() == {"detail": "Текст", "code": "custom_rule"}
    assert issubclass(ValidationError, BusinessRuleError)
    assert isinstance(IntegrationNotConfiguredError(), IntegrationError)


def test_business_rule_error_positional_message_is_detail_not_code() -> None:
    error = BusinessRuleError("Нельзя изменить заказ в производстве")
    assert error.to_dict() == {"detail": "Нельзя изменить заказ в производстве", "code": "business_rule_violation"}
    assert BusinessRuleError().to_dict() == {
        "detail": "Операция нарушает бизнес-правила",
        "code": "business_rule_violation",
    }


def test_validation_error_has_detail_first_signature_and_own_code() -> None:
    assert ValidationError("Дата доставки в прошлом").to_dict() == {
        "detail": "Дата доставки в прошлом",
        "code": "validation_error",
    }
    assert ValidationError().to_dict() == {"detail": "Ошибка валидации", "code": "validation_error"}
    assert ValidationError().status_code == 422
    assert ValidationError("order_incomplete", missing=["phone"]).to_dict() == {
        "detail": "Не заполнены обязательные данные заказа",
        "code": "order_incomplete",
        "missing": ["phone"],
    }
    assert ValidationError(code="bad_date", detail="Неверная дата").to_dict() == {
        "detail": "Неверная дата",
        "code": "bad_date",
    }
    with pytest.raises(BusinessRuleError):
        raise ValidationError("Неверный телефон")


def test_money_payload_too_large_is_422_not_500(errors_client: TestClient) -> None:
    for payload in ('{"amount": 1e30}', '{"amount": "1e27"}', '{"amount": 10000000000}'):
        response = errors_client.post("/money", content=payload, headers={"Content-Type": "application/json"})
        assert response.status_code == 422, payload
        assert response.json()["code"] == "validation_error"
    ok = errors_client.post("/money", json={"amount": "9999999999.99"})
    assert ok.status_code == 200
    assert ok.json() == {"amount": 9999999999.99, "price": None, "paid": 0.0}


# --------------------------------------------------------------------------- rate limiting


@limiter.limit("2/minute")
def limited_endpoint(request: Request) -> dict[str, bool]:
    return {"ok": True}


def ping_endpoint() -> dict[str, str]:
    return {"pong": "ok"}


def _router_like_api_router() -> APIRouter:
    from fastapi import Depends

    from app.core.rate_limit import default_rate_limit

    return APIRouter(dependencies=[Depends(default_rate_limit)])


def test_api_router_applies_default_rate_limit() -> None:
    from app.api.router import api_router
    from app.core.rate_limit import default_rate_limit

    assert any(dependency.dependency is default_rate_limit for dependency in api_router.dependencies)


def test_rate_limit_decorator_returns_rate_limited_code(app: FastAPI) -> None:
    router = _router_like_api_router()
    router.add_api_route("/api/test/limited", limited_endpoint, methods=["GET"])
    app.include_router(router)
    with TestClient(app) as test_client:
        # the route limit (2/min) replaces the default one and is still enforced by the decorator
        assert test_client.get("/api/test/limited").status_code == 200
        assert test_client.get("/api/test/limited").status_code == 200
        response = test_client.get("/api/test/limited")
    assert response.status_code == 429
    assert response.json() == {"detail": "Слишком много запросов, попробуйте позже", "code": "rate_limited"}
    assert int(response.headers["retry-after"]) > 0


def test_default_rate_limit_applies_to_api_routes(app: FastAPI) -> None:
    router = _router_like_api_router()
    router.add_api_route("/api/test/ping", ping_endpoint, methods=["GET"])
    app.include_router(router)
    with TestClient(app) as test_client:
        statuses = [test_client.get("/api/test/ping").status_code for _ in range(121)]
        exempt = test_client.get("/api/health")
    assert statuses[:120] == [200] * 120
    assert statuses[120] == 429
    assert exempt.status_code == 200


@limiter.limit("150/minute")
def generous_endpoint(request: Request) -> dict[str, bool]:
    return {"ok": True}


def test_route_limit_replaces_default_limit_instead_of_being_capped_by_it(app: FastAPI) -> None:
    # 04-api.md §0: webhook 600/min > default 120/min — the decorator limit must win, not min(both)
    from app.core.rate_limit import has_own_limits

    assert has_own_limits(generous_endpoint) is True
    assert has_own_limits(ping_endpoint) is False
    router = _router_like_api_router()
    router.add_api_route("/api/test/generous", generous_endpoint, methods=["POST"])
    app.include_router(router)
    with TestClient(app) as test_client:
        statuses = [test_client.post("/api/test/generous").status_code for _ in range(151)]
    assert statuses[:150] == [200] * 150
    assert statuses[150] == 429


def test_rate_limit_storage_selection() -> None:
    assert rate_limit_storage_uri(make_settings(APP_ENV="test", REDIS_URL="redis://redis:6379/0")) == MEMORY_STORAGE_URI
    assert rate_limit_storage_uri(make_settings(APP_ENV="development", REDIS_URL="")) == MEMORY_STORAGE_URI
    assert (
        rate_limit_storage_uri(make_settings(APP_ENV="development", REDIS_URL="redis://redis:6379/0"))
        == "redis://redis:6379/0"
    )


# --------------------------------------------------------------------------- celery


def test_celery_app_configuration() -> None:
    from app.tasks.celery_app import TASK_MODULES, build_beat_schedule, celery_app, discover_task_modules

    conf = celery_app.conf
    assert conf.task_always_eager is True
    assert conf.task_acks_late is True
    assert conf.timezone == "Asia/Dushanbe"
    assert conf.broker_url == "memory://"
    assert celery_app.main == "bakery"

    modules = discover_task_modules()
    assert "app.tasks.celery_app" not in modules
    assert all(importlib.util.find_spec(name) is not None for name in modules)
    assert len(TASK_MODULES) >= 4

    schedule = build_beat_schedule(get_settings(), ["app.tasks.reports"])
    assert set(schedule) == {"maybe-generate-daily-report"}
    entry = schedule["maybe-generate-daily-report"]
    # 06 §5: beat polls every 15 minutes; the due time is BusinessSettings.daily_report_time.
    assert entry["task"] == "app.tasks.reports.maybe_generate_daily_report"
    assert entry["schedule"].minute == {0, 15, 30, 45} and entry["schedule"].hour == set(range(24))
    assert build_beat_schedule(get_settings(), []) == {}


# --------------------------------------------------------------------------- scripts.create_admin


def test_create_admin_is_idempotent(db: Session) -> None:
    from scripts.create_admin import ensure_admin

    user, created = ensure_admin(db, "boss", "strong-password")
    db.commit()
    assert created is True
    assert user.role is UserRole.ADMIN
    assert verify_password("strong-password", user.password_hash)

    again, created_again = ensure_admin(db, " boss ", "")
    assert created_again is False
    assert again.id == user.id


@pytest.mark.parametrize("password", ["", "   ", "short"])
def test_create_admin_refuses_weak_password(db: Session, password: str) -> None:
    from scripts.create_admin import AdminCreationError, ensure_admin

    with pytest.raises(AdminCreationError):
        ensure_admin(db, "boss", password)


def test_create_admin_main_exit_codes(
    db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.create_admin as script

    def settings_with(password: str) -> Settings:
        return make_settings(FIRST_ADMIN_USERNAME="root", FIRST_ADMIN_PASSWORD=password, LOG_LEVEL="WARNING")

    monkeypatch.setattr(script, "get_settings", lambda: settings_with(""))
    assert script.main() == 1
    assert "refusing" in capsys.readouterr().err

    monkeypatch.setattr(script, "get_settings", lambda: settings_with("root-password-1"))
    assert script.main() == 0
    assert "created" in capsys.readouterr().out
    assert script.main() == 0
    assert "already exists" in capsys.readouterr().out
    assert "root-password-1" not in capsys.readouterr().out

    users = db.scalars(sa.select(User).where(User.username == "root")).all()
    assert len(users) == 1
