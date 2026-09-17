"""Health endpoints, app wiring (router modules, docs, CORS, request id, error shape)."""

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from app.api import router as router_module
from app.api.routes import health as health_routes
from app.core.config import Settings
from app.main import create_app

ROUTE_PREFIXES = {
    "auth": "/auth",
    "users": "/users",
    "customers": "/customers",
    "products": "/products",
    "orders": "/orders",
    "production": "/production",
    "statistics": "/statistics",
    "reports": "/reports",
    "expenses": "/expenses",
    "deliveries": "/deliveries",
    "faq": "/faq",
    "conversations": "/conversations",
    "test_chat": "/test-chat",
    "settings": "/settings",
    "webhooks": "/webhooks",
    "public": "/public",
    "media": "/media",
    "health": "/health",
}


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers["x-request-id"]) == 32


def test_request_id_is_propagated_or_generated(client: TestClient) -> None:
    response = client.get("/api/health", headers={"X-Request-ID": "front-123.abc"})
    assert response.headers["x-request-id"] == "front-123.abc"

    response = client.get("/api/health", headers={"X-Request-ID": "not valid id!"})
    assert response.headers["x-request-id"] != "not valid id!"
    assert len(response.headers["x-request-id"]) == 32


def test_readiness_ok_when_dependencies_are_available(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_routes, "check_redis", lambda settings: (True, "ok"))
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


def test_readiness_without_redis_outside_production(client: TestClient) -> None:
    # tests run with REDIS_URL="" → Redis is reported as not configured but not fatal
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "not_configured"}


def test_readiness_fails_when_redis_is_down(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_routes, "check_redis", lambda settings: (False, "error"))
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "ok", "redis": "error"}}


def test_readiness_fails_when_database_is_down(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_routes, "check_database", lambda db: (False, "error"))
    monkeypatch.setattr(health_routes, "check_redis", lambda settings: (True, "ok"))
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "error"


def test_check_database_reports_errors() -> None:
    class BrokenSession:
        def execute(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("connection refused")

        def rollback(self) -> None:
            raise RuntimeError("still broken")

    assert health_routes.check_database(BrokenSession()) == (False, "error")  # type: ignore[arg-type]


def test_check_redis_unreachable_server_fails_fast() -> None:
    started = time.perf_counter()
    result = health_routes.check_redis(_settings(APP_ENV="development", REDIS_URL="redis://127.0.0.1:1/0"))
    assert result == (False, "error")
    assert time.perf_counter() - started < 10


def test_check_redis_missing_url_is_fatal_in_production() -> None:
    settings = _settings(APP_ENV="production", REDIS_URL="", JWT_SECRET="p" * 40)
    assert health_routes.check_redis(settings) == (False, "not_configured")


def test_health_endpoints_are_not_rate_limited(client: TestClient) -> None:
    statuses = {client.get("/api/health").status_code for _ in range(130)}
    assert statuses == {200}


def test_openapi_and_docs_available_outside_production(client: TestClient) -> None:
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/health" in paths
    assert "/api/health/ready" in paths
    assert client.get("/api/docs").status_code == 200


def test_docs_disabled_in_production() -> None:
    settings = _settings(APP_ENV="production", JWT_SECRET="p" * 40, LOG_LEVEL="WARNING")
    with TestClient(create_app(settings)) as production_client:
        assert production_client.get("/api/openapi.json").status_code == 404
        assert production_client.get("/api/docs").status_code == 404
        assert production_client.get("/api/health").status_code == 200


def test_production_requires_strong_jwt_secret() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(_settings(APP_ENV="production", JWT_SECRET="short", LOG_LEVEL="WARNING"))


def test_api_router_includes_every_module_from_contract() -> None:
    assert len(router_module.ROUTE_MODULES) == len(ROUTE_PREFIXES)
    for name, prefix in ROUTE_PREFIXES.items():
        module = importlib.import_module(f"app.api.routes.{name}")
        assert module in router_module.ROUTE_MODULES
        assert module.router.prefix == prefix
        assert module.__doc__ is not None and "04-api.md" in module.__doc__
    assert router_module.api_router.prefix == "/api"


def test_unknown_route_returns_error_shape(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Не найдено", "code": "not_found"}


def test_cors_allows_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"

    denied = client.options(
        "/api/health",
        headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in denied.headers
