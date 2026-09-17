"""Contract check: every endpoint of 04-api.md §1-§13 exists with the right method, there are no
undocumented routes, and the STAFF/ADMIN/PUBLIC split is enforced everywhere (04 §0).
"""

from collections.abc import Iterable

import pytest

from app.main import create_app

# (method, path) exactly as in 04-api.md. FastAPI path params use "{name}", matching the contract.
CONTRACT_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PATCH", "/api/users/{user_id}"),
    ("DELETE", "/api/users/{user_id}"),
    ("GET", "/api/customers"),
    ("GET", "/api/customers/{customer_id}"),
    ("POST", "/api/customers"),
    ("PATCH", "/api/customers/{customer_id}"),
    ("GET", "/api/products"),
    ("GET", "/api/products/{product_id}"),
    ("POST", "/api/products"),
    ("PATCH", "/api/products/{product_id}"),
    ("DELETE", "/api/products/{product_id}"),
    ("GET", "/api/orders"),
    ("POST", "/api/orders"),
    ("GET", "/api/orders/{order_id}"),
    ("PATCH", "/api/orders/{order_id}"),
    ("POST", "/api/orders/{order_id}/status"),
    ("POST", "/api/orders/{order_id}/payments"),
    ("POST", "/api/orders/{order_id}/cancel"),
    ("GET", "/api/orders/{order_id}/events"),
    ("GET", "/api/production"),
    ("GET", "/api/statistics"),
    ("GET", "/api/statistics/dashboard"),
    ("GET", "/api/statistics/timeseries"),
    ("GET", "/api/reports/daily"),
    ("POST", "/api/reports/daily/generate"),
    ("GET", "/api/reports/daily/history"),
    ("GET", "/api/expenses"),
    ("POST", "/api/expenses"),
    ("PATCH", "/api/expenses/{expense_id}"),
    ("DELETE", "/api/expenses/{expense_id}"),
    ("GET", "/api/deliveries"),
    ("GET", "/api/deliveries/{delivery_id}"),
    ("PATCH", "/api/deliveries/{delivery_id}"),
    ("POST", "/api/deliveries/{delivery_id}/geocode"),
    ("POST", "/api/deliveries/{delivery_id}/select-candidate"),
    ("POST", "/api/deliveries/{delivery_id}/location-link"),
    ("POST", "/api/deliveries/optimize"),
    ("GET", "/api/deliveries/routes"),
    ("POST", "/api/deliveries/{delivery_id}/dispatch"),
    ("GET", "/api/deliveries/dispatch-sheet"),
    ("GET", "/api/faq"),
    ("POST", "/api/faq"),
    ("PATCH", "/api/faq/{faq_id}"),
    ("DELETE", "/api/faq/{faq_id}"),
    ("GET", "/api/conversations"),
    ("GET", "/api/conversations/{conversation_id}"),
    ("POST", "/api/conversations/{conversation_id}/messages"),
    ("POST", "/api/conversations/{conversation_id}/handoff"),
    ("POST", "/api/conversations/{conversation_id}/resume"),
    ("POST", "/api/conversations/{conversation_id}/read"),
    ("GET", "/api/test-chat/{customer_key}"),
    ("POST", "/api/test-chat/{customer_key}/messages"),
    ("GET", "/api/settings"),
    ("PUT", "/api/settings"),
    ("GET", "/api/settings/integrations"),
    ("GET", "/api/webhooks/instagram"),
    ("POST", "/api/webhooks/instagram"),
    ("GET", "/api/public/location/{token}"),
    ("POST", "/api/public/location/{token}"),
    ("GET", "/api/media/{filename}"),
    ("GET", "/api/health"),
    ("GET", "/api/health/ready"),
)

#: ADMIN-only endpoints (everything else that needs auth at all is STAFF: ADMIN or OPERATOR).
ADMIN_ONLY: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/users"),
        ("POST", "/api/users"),
        ("PATCH", "/api/users/{user_id}"),
        ("DELETE", "/api/users/{user_id}"),
        ("POST", "/api/products"),
        ("PATCH", "/api/products/{product_id}"),
        ("DELETE", "/api/products/{product_id}"),
        ("POST", "/api/orders"),
        ("POST", "/api/reports/daily/generate"),
        ("POST", "/api/expenses"),
        ("PATCH", "/api/expenses/{expense_id}"),
        ("DELETE", "/api/expenses/{expense_id}"),
        ("POST", "/api/faq"),
        ("PATCH", "/api/faq/{faq_id}"),
        ("DELETE", "/api/faq/{faq_id}"),
        ("PUT", "/api/settings"),
        ("GET", "/api/settings/integrations"),
    }
)

#: Endpoints reachable without a token (04 §13).
PUBLIC: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/refresh"),
        ("GET", "/api/webhooks/instagram"),
        ("POST", "/api/webhooks/instagram"),
        ("GET", "/api/public/location/{token}"),
        ("POST", "/api/public/location/{token}"),
        ("GET", "/api/media/{filename}"),
        ("GET", "/api/health"),
        ("GET", "/api/health/ready"),
    }
)

# A body-less "smoke" payload per prefix, just enough to pass request validation before the
# auth/role dependency is expected to reject the call. Real behaviour is covered by the
# domain-specific test files; this file only checks that the door is locked.
BODY_BY_PREFIX: tuple[tuple[str, dict], ...] = (
    ("/api/auth/login", {"username": "x", "password": "x"}),
    ("/api/auth/refresh", {"refresh_token": "x"}),
    ("/api/auth/logout", {"refresh_token": "x"}),
    ("/api/users", {"username": "x", "password": "password123", "role": "OPERATOR"}),
    ("/api/customers", {"name": "x"}),
    ("/api/products", {"name": "x", "price": 1}),
    ("/api/orders/{order_id}/status", {"status": "CONFIRMED"}),
    ("/api/orders/{order_id}/payments", {"kind": "PAYMENT", "amount": 1}),
    ("/api/orders/{order_id}/cancel", {}),
    ("/api/orders", {"customer_id": 1, "items": [], "delivery_type": "PICKUP", "delivery_date": "2026-01-01", "delivery_time": "10:00"}),
    ("/api/deliveries/{delivery_id}/select-candidate", {"index": 0}),
    ("/api/deliveries/optimize", {"date": "2026-01-01"}),
    ("/api/expenses", {"expense_date": "2026-01-01", "category": "INGREDIENTS", "amount": 1}),
    ("/api/faq", {"question": "x", "answer": "x"}),
    ("/api/conversations/{conversation_id}/messages", {"text": "x"}),
    ("/api/test-chat/{customer_key}/messages", {"text": "x"}),
    ("/api/conversations/{conversation_id}/handoff", {"reason": "x"}),
    ("/api/settings", {}),
    ("/api/public/location/{token}", {"latitude": 38.56, "longitude": 68.78}),
)


def _fake_id_path(path: str) -> str:
    """Replace every ``{param}`` with a syntactically valid placeholder (404 is an acceptable
    outcome here — only a raw 401/403 vs. "got past auth" distinction matters)."""
    result = path
    id_params = (
        "user_id",
        "customer_id",
        "product_id",
        "order_id",
        "faq_id",
        "delivery_id",
        "conversation_id",
        "expense_id",
    )
    for name in id_params:
        result = result.replace(f"{{{name}}}", "1")
    return result.replace("{token}", "x").replace("{filename}", "x").replace("{customer_key}", "x")


def _body_for(path: str) -> dict:
    for prefix, body in BODY_BY_PREFIX:
        if path == prefix:
            return body
    return {}


def _all_registered_routes(app) -> set[tuple[str, str]]:
    # FastAPI 0.141 keeps included routers lazy on `app.routes` (see app/core/rate_limit.py's own
    # note on this); `app.openapi()` is what actually resolves them, so it is the reliable source.
    schema = app.openapi()
    return {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method.upper() not in ("HEAD", "OPTIONS")
    }


@pytest.fixture(scope="module")
def registered_routes() -> set[tuple[str, str]]:
    return _all_registered_routes(create_app())


@pytest.mark.parametrize("method,path", CONTRACT_ENDPOINTS)
def test_endpoint_is_registered(registered_routes: set[tuple[str, str]], method: str, path: str) -> None:
    assert (method, path) in registered_routes, f"{method} {path} is missing from the app"


def test_contract_has_no_undocumented_extra_routes(registered_routes: set[tuple[str, str]]) -> None:
    """Catches copy-paste path typos: every API route except the docs must be in CONTRACT_ENDPOINTS."""
    excluded_prefixes = ("/api/docs", "/api/openapi.json", "/api/redoc")
    extra = {
        (method, path)
        for method, path in registered_routes
        if path.startswith("/api/") and not any(path.startswith(p) for p in excluded_prefixes)
    } - set(CONTRACT_ENDPOINTS)
    assert extra == set(), f"routes not listed in 04-api.md (or in this test's CONTRACT_ENDPOINTS): {extra}"


@pytest.mark.parametrize("method,path", [ep for ep in CONTRACT_ENDPOINTS if ep not in PUBLIC])
def test_requires_authentication(client, method: str, path: str) -> None:
    response = client.request(method, _fake_id_path(path), json=_body_for(path) or None)
    assert response.status_code == 401, f"{method} {path} did not require authentication: {response.status_code}"


@pytest.mark.parametrize("method,path", sorted(ADMIN_ONLY))
def test_admin_only_endpoint_rejects_operator(client, operator_headers, method: str, path: str) -> None:
    response = client.request(method, _fake_id_path(path), json=_body_for(path) or None, headers=operator_headers)
    assert response.status_code == 403, f"{method} {path} did not reject an operator: {response.status_code}"


@pytest.mark.parametrize(
    "method,path", sorted(set(CONTRACT_ENDPOINTS) - ADMIN_ONLY - PUBLIC - {("PATCH", "/api/orders/{order_id}")})
)
def test_staff_endpoint_accepts_operator_past_the_role_check(
    client, operator_headers, method: str, path: str
) -> None:
    """An operator must never get 401/403 here — a 4xx/2xx past that point is a data/validation
    matter covered by the domain test files, not a role-check regression."""
    response = client.request(method, _fake_id_path(path), json=_body_for(path) or None, headers=operator_headers)
    assert response.status_code not in (401, 403), f"{method} {path} wrongly rejected an operator"


def test_operator_patch_order_role_check_runs_before_field_rejection(client, operator_headers, make_order) -> None:
    """PATCH /orders/{id} is STAFF, but 04 §5 lets the operator send only a subset of fields —
    covered in full by test_orders_api.py. Here: a field-agnostic call still passes the role gate."""
    order = make_order()
    response = client.patch(f"/api/orders/{order.id}", json={"comment": "x"}, headers=operator_headers)
    assert response.status_code == 200


def _fixture_names(items: Iterable[tuple[str, str]]) -> Iterable[str]:
    return (f"{method}:{path}" for method, path in items)
