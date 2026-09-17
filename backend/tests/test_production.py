"""Production summary — 03-business-rules.md §4, 04-api.md §6, SPEC §19, §45 ("производственная сводка")."""

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.time import business_today
from app.models.enums import OrderStatus
from app.models.product import Product
from app.services.production_service import ProductionService

DAY = date(2026, 9, 16)


@pytest.fixture
def catalog(make_product: Callable[..., Product]) -> dict[str, Product]:
    """The SPEC §19 catalog with an explicit ``sort_order`` (production is sorted by it, then name)."""
    return {
        "velvet": make_product("Красный бархат", price="750.00", sort_order=1),
        "honey": make_product("Медовик", price="400.00", sort_order=2),
        "cheese": make_product("Чизкейк", price="500.00", sort_order=3),
        "cupcakes": make_product("Капкейки", price="250.00", sort_order=4, unit="шт."),
    }


class TestSummaryGrouping:
    def test_sums_quantities_of_the_same_product_across_orders(
        self, db: Session, catalog: dict[str, Product], make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[(catalog["velvet"], 2)], status=OrderStatus.CONFIRMED, delivery_date=DAY)
        make_order(items=[(catalog["velvet"], 3)], status=OrderStatus.PREPARING, delivery_date=DAY)

        summary = ProductionService(db).summary(DAY)

        assert summary.date == DAY
        assert summary.orders_count == 2
        assert [(item.product_name, item.quantity) for item in summary.items] == [("Красный бархат", 5)]
        assert summary.items[0].product_id == catalog["velvet"].id

    def test_sorts_by_product_sort_order_then_name(
        self, db: Session, catalog: dict[str, Product], make_order: Callable[..., Any]
    ) -> None:
        make_order(
            items=[(catalog["cupcakes"], 36), (catalog["cheese"], 7), (catalog["honey"], 4), (catalog["velvet"], 5)],
            status=OrderStatus.CONFIRMED,
            delivery_date=DAY,
        )

        items = ProductionService(db).summary(DAY).items

        assert [item.product_name for item in items] == ["Красный бархат", "Медовик", "Чизкейк", "Капкейки"]

    def test_equal_sort_order_falls_back_to_the_name(
        self, db: Session, make_product: Callable[..., Product], make_order: Callable[..., Any]
    ) -> None:
        beta = make_product("Эклер", price="100.00", sort_order=0)
        alpha = make_product("Багет", price="100.00", sort_order=0)
        make_order(items=[(beta, 1), (alpha, 1)], status=OrderStatus.CONFIRMED, delivery_date=DAY)

        items = ProductionService(db).summary(DAY).items

        assert [item.product_name for item in items] == ["Багет", "Эклер"]

    def test_uses_the_unit_of_the_product(
        self, db: Session, make_product: Callable[..., Product], make_order: Callable[..., Any]
    ) -> None:
        cake = make_product("Торт на вес", price="200.00", unit="кг")
        make_order(items=[(cake, 3)], status=OrderStatus.CONFIRMED, delivery_date=DAY)

        summary = ProductionService(db).summary(DAY)

        assert summary.items[0].unit == "кг"
        assert summary.text.endswith("Торт на вес — 3 кг")

    def test_items_without_a_product_are_grouped_by_name_with_the_default_unit(
        self, db: Session, make_order: Callable[..., Any]
    ) -> None:
        detached = {"product": None, "product_name": "Пирог", "quantity": 2, "unit_price": "300.00"}
        make_order(items=[detached], status=OrderStatus.CONFIRMED, delivery_date=DAY)
        make_order(items=[{**detached, "quantity": 1}], status=OrderStatus.READY, delivery_date=DAY)

        items = ProductionService(db).summary(DAY).items

        assert [(item.product_id, item.product_name, item.quantity, item.unit) for item in items] == [
            (None, "Пирог", 3, "шт.")
        ]

    def test_renamed_product_is_reported_under_its_current_name(
        self, db: Session, make_product: Callable[..., Product], make_order: Callable[..., Any]
    ) -> None:
        product = make_product("Медовик", price="400.00")
        make_order(items=[(product, 2)], status=OrderStatus.CONFIRMED, delivery_date=DAY)
        product.name = "Медовик классический"
        db.commit()

        items = ProductionService(db).summary(DAY).items

        assert [(item.product_name, item.quantity) for item in items] == [("Медовик классический", 2)]


class TestSummaryScope:
    @pytest.mark.parametrize(
        "status", [OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.COMPLETED]
    )
    def test_valid_statuses_are_counted(
        self, db: Session, catalog: dict[str, Product], make_order: Callable[..., Any], status: OrderStatus
    ) -> None:
        make_order(items=[(catalog["honey"], 1)], status=status, delivery_date=DAY)

        assert ProductionService(db).summary(DAY).orders_count == 1

    @pytest.mark.parametrize(
        "status", [OrderStatus.NEW, OrderStatus.WAITING_CONFIRMATION, OrderStatus.CANCELLED]
    )
    def test_draft_and_cancelled_orders_are_excluded(
        self, db: Session, catalog: dict[str, Product], make_order: Callable[..., Any], status: OrderStatus
    ) -> None:
        make_order(items=[(catalog["honey"], 4)], status=status, delivery_date=DAY)

        summary = ProductionService(db).summary(DAY)

        assert summary.orders_count == 0
        assert summary.items == []

    def test_only_the_requested_delivery_date(
        self, db: Session, catalog: dict[str, Product], make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[(catalog["honey"], 4)], status=OrderStatus.CONFIRMED, delivery_date=DAY)
        make_order(items=[(catalog["honey"], 9)], status=OrderStatus.CONFIRMED, delivery_date=DAY + timedelta(days=1))

        assert [item.quantity for item in ProductionService(db).summary(DAY).items] == [4]

    def test_empty_day(self, db: Session) -> None:
        summary = ProductionService(db).summary(DAY)

        assert summary.orders_count == 0
        assert summary.items == []
        assert summary.text == "Заказы на 16 сентября:"

    def test_defaults_to_business_today(
        self, db: Session, catalog: dict[str, Product], make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[(catalog["honey"], 2)], status=OrderStatus.CONFIRMED, delivery_date=business_today())

        summary = ProductionService(db).summary()

        assert summary.date == business_today()
        assert summary.orders_count == 1


class TestSummaryText:
    def test_matches_the_spec_example(
        self, db: Session, catalog: dict[str, Product], make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[(catalog["velvet"], 5), (catalog["honey"], 4)], status=OrderStatus.CONFIRMED, delivery_date=DAY)
        make_order(items=[(catalog["cheese"], 7), (catalog["cupcakes"], 36)], status=OrderStatus.READY, delivery_date=DAY)

        text = ProductionService(db).summary(DAY).text

        assert text == (
            "Заказы на 16 сентября:\n"
            "\n"
            "Красный бархат — 5 шт.\n"
            "Медовик — 4 шт.\n"
            "Чизкейк — 7 шт.\n"
            "Капкейки — 36 шт."
        )


class TestProductionApi:
    def test_staff_gets_the_summary_for_a_date(
        self,
        client: TestClient,
        operator_headers: dict[str, str],
        catalog: dict[str, Product],
        make_order: Callable[..., Any],
    ) -> None:
        make_order(items=[(catalog["velvet"], 5)], status=OrderStatus.CONFIRMED, delivery_date=DAY)

        response = client.get("/api/production", params={"date": DAY.isoformat()}, headers=operator_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["date"] == DAY.isoformat()
        assert body["orders_count"] == 1
        assert body["items"] == [
            {"product_id": catalog["velvet"].id, "product_name": "Красный бархат", "quantity": 5, "unit": "шт."}
        ]
        assert body["text"].startswith("Заказы на 16 сентября:")

    def test_defaults_to_today(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        catalog: dict[str, Product],
        make_order: Callable[..., Any],
    ) -> None:
        make_order(items=[(catalog["honey"], 2)], status=OrderStatus.CONFIRMED, delivery_date=business_today())

        response = client.get("/api/production", headers=admin_headers)

        assert response.status_code == 200
        assert response.json()["date"] == business_today().isoformat()
        assert response.json()["orders_count"] == 1

    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/production").status_code == 401

    def test_rejects_a_malformed_date(self, client: TestClient, admin_headers: dict[str, str]) -> None:
        response = client.get("/api/production", params={"date": "16-09-2026"}, headers=admin_headers)

        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
