"""Products API and ProductService (04-api.md §4, 03-business-rules.md §1.4, SPEC §15)."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models.enums import OrderStatus
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.services.product_service import ProductService, normalize_tags

URL = "/api/products"


def create_product(client: TestClient, headers: dict[str, str], **payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Медовик", "price": 150, **payload}
    response = client.post(URL, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def names(items: list[dict[str, Any]]) -> list[str]:
    return [item["name"] for item in items]


# --------------------------------------------------------------------------- auth & roles


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", URL, None),
        ("get", f"{URL}/1", None),
        ("post", URL, {"name": "Медовик", "price": 150}),
        ("patch", f"{URL}/1", {"price": 200}),
        ("delete", f"{URL}/1", None),
    ],
)
def test_requires_authentication(client: TestClient, method: str, path: str, body: Any) -> None:
    response = client.request(method, path, json=body)

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_operator_can_read_catalog(
    client: TestClient, operator_headers: dict[str, str], make_product: Callable[..., Product]
) -> None:
    product = make_product("Медовик", price="150.00")

    listed = client.get(URL, headers=operator_headers)
    detail = client.get(f"{URL}/{product.id}", headers=operator_headers)

    assert listed.status_code == 200
    assert names(listed.json()) == ["Медовик"]
    assert detail.status_code == 200
    assert detail.json()["id"] == product.id


@pytest.mark.parametrize(
    ("method", "body"),
    [("post", {"name": "Медовик", "price": 150}), ("patch", {"price": 200}), ("delete", None)],
)
def test_operator_cannot_write(
    client: TestClient,
    operator_headers: dict[str, str],
    make_product: Callable[..., Product],
    method: str,
    body: Any,
) -> None:
    product = make_product()
    path = URL if method == "post" else f"{URL}/{product.id}"

    response = client.request(method, path, json=body, headers=operator_headers)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


# --------------------------------------------------------------------------- create


def test_create_applies_contract_defaults(client: TestClient, admin_headers: dict[str, str]) -> None:
    data = create_product(client, admin_headers, name="Медовик", price=150.5)

    assert data["id"] > 0
    assert data["name"] == "Медовик"
    assert data["description"] is None
    assert data["price"] == 150.5
    assert data["currency"] == "TJS"
    assert data["unit"] == "шт."
    assert data["aliases"] == []
    assert data["is_active"] is True
    assert data["sort_order"] == 0
    assert data["created_at"] and data["updated_at"]


def test_create_trims_name_and_normalizes_aliases(client: TestClient, admin_headers: dict[str, str]) -> None:
    data = create_product(
        client,
        admin_headers,
        name="  Красный бархат  ",
        description="   ",
        aliases=["  Бархат ", "бархат", "", "   ", "Торти Сурх"],
        currency="tjs",
        unit=" кг ",
        sort_order=5,
    )

    assert data["name"] == "Красный бархат"
    assert data["description"] is None
    assert data["aliases"] == ["бархат", "торти сурх"]
    assert data["currency"] == "TJS"
    assert data["unit"] == "кг"
    assert data["sort_order"] == 5


def test_create_rounds_price_to_two_places(client: TestClient, admin_headers: dict[str, str]) -> None:
    data = create_product(client, admin_headers, price=1500.456)

    assert data["price"] == 1500.46


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Медовик"},  # price missing
        {"price": 150},  # name missing
        {"name": "   ", "price": 150},  # blank name
        {"name": "Медовик", "price": 0},
        {"name": "Медовик", "price": -10},
        {"name": "Медовик", "price": "дорого"},
        {"name": "Медовик", "price": 150, "currency": "RUBLE"},
        {"name": "Медовик", "price": 150, "unit": ""},
        {"name": "М" * 129, "price": 150},
    ],
)
def test_create_rejects_invalid_payload(
    client: TestClient, admin_headers: dict[str, str], payload: dict[str, Any]
) -> None:
    response = client.post(URL, json=payload, headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --------------------------------------------------------------------------- list & get


def test_list_hides_inactive_unless_requested(
    client: TestClient, admin_headers: dict[str, str], make_product: Callable[..., Product]
) -> None:
    make_product("Медовик")
    make_product("Чизкейк", is_active=False)

    default = client.get(URL, headers=admin_headers)
    with_inactive = client.get(URL, params={"include_inactive": True}, headers=admin_headers)

    assert names(default.json()) == ["Медовик"]
    assert sorted(names(with_inactive.json())) == ["Медовик", "Чизкейк"]


def test_list_is_ordered_by_sort_order_then_name(
    client: TestClient, admin_headers: dict[str, str], make_product: Callable[..., Product]
) -> None:
    make_product("Чизкейк", sort_order=1)
    make_product("Эклер", sort_order=0)
    make_product("Медовик", sort_order=0)

    response = client.get(URL, headers=admin_headers)

    assert names(response.json()) == ["Медовик", "Эклер", "Чизкейк"]


def test_list_search_matches_name_and_aliases_case_insensitively(
    client: TestClient, admin_headers: dict[str, str], make_product: Callable[..., Product]
) -> None:
    make_product("Медовик", aliases=["торти асал"])
    make_product("Чизкейк", aliases=["cheesecake"])

    by_name = client.get(URL, params={"search": "МЕДОВ"}, headers=admin_headers)
    by_alias = client.get(URL, params={"search": "асал"}, headers=admin_headers)
    nothing = client.get(URL, params={"search": "эклер"}, headers=admin_headers)

    assert names(by_name.json()) == ["Медовик"]
    assert names(by_alias.json()) == ["Медовик"]
    assert nothing.json() == []


def test_get_unknown_product_returns_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.get(f"{URL}/9999", headers=admin_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Товар не найден", "code": "not_found"}


# --------------------------------------------------------------------------- update


def test_update_changes_only_given_fields(
    client: TestClient, admin_headers: dict[str, str], make_product: Callable[..., Product]
) -> None:
    product = make_product("Медовик", price="150.00", description="Классический", sort_order=3)

    response = client.patch(f"{URL}/{product.id}", json={"price": 199.9}, headers=admin_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["price"] == 199.9
    assert data["name"] == "Медовик"
    assert data["description"] == "Классический"
    assert data["sort_order"] == 3


def test_update_normalizes_aliases_and_clears_description(
    client: TestClient, admin_headers: dict[str, str], make_product: Callable[..., Product]
) -> None:
    product = make_product("Медовик", description="Классический", aliases=["старый"])

    response = client.patch(
        f"{URL}/{product.id}",
        json={"description": None, "aliases": [" Мёд ", "МЁД", "асал"], "name": "  Медовик 2  "},
        headers=admin_headers,
    )

    data = response.json()
    assert data["description"] is None
    assert data["aliases"] == ["мёд", "асал"]
    assert data["name"] == "Медовик 2"


def test_update_can_deactivate_product(
    client: TestClient, admin_headers: dict[str, str], make_product: Callable[..., Product]
) -> None:
    product = make_product("Медовик")

    response = client.patch(f"{URL}/{product.id}", json={"is_active": False}, headers=admin_headers)

    assert response.json()["is_active"] is False
    assert names(client.get(URL, headers=admin_headers).json()) == []


def test_update_unknown_product_returns_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.patch(f"{URL}/9999", json={"price": 100}, headers=admin_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_update_rejects_non_positive_price(
    client: TestClient, admin_headers: dict[str, str], make_product: Callable[..., Product]
) -> None:
    product = make_product("Медовик", price="150.00")

    response = client.patch(f"{URL}/{product.id}", json={"price": 0}, headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --------------------------------------------------------------------------- soft delete


def test_delete_is_soft_and_hides_the_product(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_product: Callable[..., Product],
) -> None:
    product = make_product("Медовик")
    product_id = product.id

    response = client.delete(f"{URL}/{product_id}", headers=admin_headers)

    assert response.status_code == 204
    assert response.content == b""

    db.expire_all()
    stored = db.get(Product, product_id)
    assert stored is not None
    assert stored.deleted_at is not None
    assert stored.is_active is False

    assert client.get(URL, headers=admin_headers).json() == []
    assert client.get(URL, params={"include_inactive": True}, headers=admin_headers).json() == []


@pytest.mark.parametrize(("method", "body"), [("get", None), ("patch", {"price": 100}), ("delete", None)])
def test_deleted_product_is_gone(
    client: TestClient,
    admin_headers: dict[str, str],
    make_product: Callable[..., Product],
    method: str,
    body: Any,
) -> None:
    product = make_product("Медовик")
    assert client.delete(f"{URL}/{product.id}", headers=admin_headers).status_code == 204

    response = client.request(method, f"{URL}/{product.id}", json=body, headers=admin_headers)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# --------------------------------------------------------------------------- price snapshots (03 §1.4)


def test_price_change_does_not_touch_existing_order_items(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_product: Callable[..., Product],
    make_order: Callable[..., Order],
) -> None:
    product = make_product("Медовик", price="150.00")
    order = make_order(items=[(product, 2)], status=OrderStatus.CONFIRMED)
    order_id, item_id = order.id, order.items[0].id

    response = client.patch(f"{URL}/{product.id}", json={"price": 400}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["price"] == 400.0

    db.expire_all()
    item = db.get(OrderItem, item_id)
    assert item is not None
    assert item.unit_price == Decimal("150.00")
    assert item.total_price == Decimal("300.00")
    stored_order = db.get(Order, order_id)
    assert stored_order is not None
    assert stored_order.total_amount == Decimal("300.00")


def test_soft_delete_keeps_order_history(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_product: Callable[..., Product],
    make_order: Callable[..., Order],
) -> None:
    product = make_product("Медовик", price="150.00")
    order = make_order(items=[(product, 1)], status=OrderStatus.COMPLETED)
    item_id = order.items[0].id

    assert client.delete(f"{URL}/{product.id}", headers=admin_headers).status_code == 204

    db.expire_all()
    item = db.get(OrderItem, item_id)
    assert item is not None
    assert item.product_name == "Медовик"
    assert item.unit_price == Decimal("150.00")


# --------------------------------------------------------------------------- service unit tests


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([" Мёд ", "МЁД", "", "   ", "асал"], ["мёд", "асал"]),
        (None, []),
        (["Торти   Асал"], ["торти асал"]),
        ([], []),
    ],
)
def test_normalize_tags(raw: list[str] | None, expected: list[str]) -> None:
    assert normalize_tags(raw) == expected


def test_service_accepts_plain_mapping_and_reports_invalid_fields(db: Session) -> None:
    service = ProductService(db)

    product = service.create({"name": " Эклер ", "price": "12.345", "aliases": ["ЭКЛЕР"]})

    assert product.name == "Эклер"
    assert product.price == Decimal("12.35")
    assert product.aliases == ["эклер"]

    with pytest.raises(ValidationError) as excinfo:
        service.create({"name": "", "price": 0})
    assert excinfo.value.status_code == 422
    assert set(excinfo.value.details["fields"]) == {"name", "price"}
