"""Daily report and its Celery tasks — 03-business-rules.md §4, 04-api.md §8, 06 §5, SPEC §22, §45."""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import time as app_time
from app.models.customer import Customer
from app.models.enums import DeliveryType, OrderStatus
from app.models.product import Product
from app.repositories.reports import DailyReportRepository
from app.services.report_service import ReportService, daily_report_text
from app.services.settings_service import SettingsService
from app.tasks.celery_app import celery_app
from app.tasks.reports import (
    generate_daily_report,
    maybe_generate_daily_report,
    run_generate_daily_report,
    run_maybe_generate_daily_report,
)

REPORT_DATE = date(2026, 9, 15)
BUSINESS_OFFSET = timedelta(hours=5)  # Asia/Dushanbe = UTC+5

#: SPEC §22 example: 18 orders / 12 500 revenue / 10 000 paid / 2 500 unpaid / 7 new / 11 regular /
#: 13 delivery / 5 pickup, production "Красный бархат — 4, Медовик — 5, Чизкейк — 3, Капкейки — 24".
SPEC_REPORT_TEXT = """ОТЧЁТ ЗА 15.09.2026

Заказов: 18

Выручка: 12 500 сомони

Оплачено: 10 000 сомони
Не оплачено: 2 500 сомони

Новые клиенты: 7
Постоянные клиенты: 11

Доставка: 13
Самовывоз: 5

ПРОИЗВОДСТВО:

Красный бархат — 4
Медовик — 5
Чизкейк — 3
Капкейки — 24"""


def business_instant(day: date, hour: int, minute: int = 0) -> datetime:
    """UTC instant of a business-local wall clock time (Asia/Dushanbe)."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC) - BUSINESS_OFFSET


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    """Freeze ``app.core.time.now_utc``; every other time helper derives from it."""

    def _freeze(moment: datetime) -> None:
        monkeypatch.setattr(app_time, "now_utc", lambda: moment)

    return _freeze


@pytest.fixture
def spec_day(
    db: Session,
    make_product: Callable[..., Product],
    make_customer: Callable[..., Customer],
    make_order: Callable[..., Any],
) -> Iterator[None]:
    """Build exactly the SPEC §22 numbers on ``REPORT_DATE``."""
    velvet = make_product("Красный бархат", price="750.00", sort_order=1)
    honey = make_product("Медовик", price="400.00", sort_order=2)
    cheesecake = make_product("Чизкейк", price="500.00", sort_order=3)
    cupcakes = make_product("Капкейки", price="250.00", sort_order=4)

    # (items, paid) per order: 4×750 + 5×400 + 3×500 + 6×1000 = 12 500; paid 3000+800+200+6000 = 10 000.
    specs: list[tuple[list[Any], str]] = (
        [([(velvet, 1)], "750.00")] * 4
        + [([(honey, 1)], "400.00")] * 2
        + [([(honey, 1)], "200.00")]
        + [([(honey, 1)], "0")] * 2
        + [([(cheesecake, 1)], "0")] * 3
        + [([(cupcakes, 4)], "1000.00")] * 6
    )
    assert len(specs) == 18
    for index, (items, paid) in enumerate(specs):
        make_order(
            customer=make_customer(f"Клиент {index + 1}"),
            items=items,
            status=OrderStatus.CONFIRMED,
            delivery_date=REPORT_DATE,
            delivery_type=DeliveryType.DELIVERY if index < 13 else DeliveryType.PICKUP,
            paid_amount=paid,
            is_repeat_customer=index >= 7,
        )
    yield


# --------------------------------------------------------------------------- build


class TestBuildDaily:
    @pytest.mark.usefixtures("spec_day")
    def test_data_blocks_match_the_spec_example(self, db: Session) -> None:
        data = ReportService(db).build_daily(REPORT_DATE).data

        assert data.finance.orders_count == 18
        assert data.finance.revenue == Decimal("12500.00")
        assert data.finance.paid_amount == Decimal("10000.00")
        assert data.finance.unpaid_amount == Decimal("2500.00")
        assert data.finance.paid_orders_count == 12
        assert data.finance.partially_paid_orders_count == 1
        assert data.finance.unpaid_orders_count == 5
        assert data.customers.new_customers == 7
        assert data.customers.regular_customers == 11
        assert data.delivery.delivery_orders == 13
        assert data.delivery.pickup_orders == 5
        assert [(item.product_name, item.quantity) for item in data.production.items] == [
            ("Красный бархат", 4),
            ("Медовик", 5),
            ("Чизкейк", 3),
            ("Капкейки", 24),
        ]

    @pytest.mark.usefixtures("spec_day")
    def test_text_matches_the_spec_layout_exactly(self, db: Session) -> None:
        content = ReportService(db).build_daily(REPORT_DATE)

        assert content.text == SPEC_REPORT_TEXT

    @pytest.mark.usefixtures("spec_day")
    def test_text_is_rendered_from_the_stored_data(self, db: Session) -> None:
        content = ReportService(db).build_daily(REPORT_DATE)

        assert daily_report_text(REPORT_DATE, content.data) == SPEC_REPORT_TEXT

    def test_empty_day_has_zeros_and_no_production_lines(self, db: Session) -> None:
        content = ReportService(db).build_daily(REPORT_DATE)

        assert content.data.finance.orders_count == 0
        assert content.data.production.items == []
        assert content.text.endswith("ПРОИЗВОДСТВО:")
        assert "Заказов: 0" in content.text
        assert "Выручка: 0 сомони" in content.text

    def test_only_valid_orders_of_that_date(
        self, db: Session, make_product: Callable[..., Product], make_order: Callable[..., Any]
    ) -> None:
        product = make_product("Медовик", price="500.00")
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=REPORT_DATE)
        make_order(items=[(product, 1)], status=OrderStatus.CANCELLED, delivery_date=REPORT_DATE)
        make_order(items=[(product, 1)], status=OrderStatus.NEW, delivery_date=REPORT_DATE)
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=REPORT_DATE + timedelta(days=1))

        data = ReportService(db).build_daily(REPORT_DATE).data

        assert data.finance.orders_count == 1
        assert data.finance.revenue == Decimal("500.00")


# --------------------------------------------------------------------------- storage


class TestStorage:
    @pytest.mark.usefixtures("spec_day")
    def test_get_or_build_stores_the_report_once(self, db: Session) -> None:
        service = ReportService(db)

        first = service.get_or_build(REPORT_DATE)
        generated_at = first.generated_at
        second = service.get_or_build(REPORT_DATE)

        assert first.id == second.id
        assert second.generated_at == generated_at
        assert DailyReportRepository(db).get_by_date(REPORT_DATE) is not None
        assert first.text == SPEC_REPORT_TEXT
        assert set(first.data) == {"finance", "customers", "delivery", "production"}

    def test_regenerate_overwrites_the_stored_row(
        self,
        db: Session,
        make_product: Callable[..., Product],
        make_order: Callable[..., Any],
        frozen_now: Callable[[datetime], None],
    ) -> None:
        product = make_product("Медовик", price="500.00")
        service = ReportService(db)
        frozen_now(business_instant(REPORT_DATE, 21, 0))
        first = service.get_or_build(REPORT_DATE)
        assert first.data["finance"]["orders_count"] == 0

        make_order(items=[(product, 2)], status=OrderStatus.CONFIRMED, delivery_date=REPORT_DATE)
        frozen_now(business_instant(REPORT_DATE, 22, 0))
        second = service.regenerate(REPORT_DATE)

        assert second.id == first.id
        assert second.data["finance"]["orders_count"] == 1
        assert second.data["finance"]["revenue"] == 1000.0
        assert second.generated_at == business_instant(REPORT_DATE, 22, 0)
        assert DailyReportRepository(db).history() == [second]

    def test_daily_recomputes_a_day_that_is_not_over_yet(
        self,
        db: Session,
        make_product: Callable[..., Product],
        make_order: Callable[..., Any],
        frozen_now: Callable[[datetime], None],
    ) -> None:
        product = make_product("Медовик", price="500.00")
        today = date(2026, 9, 16)
        frozen_now(business_instant(today, 12, 0))
        service = ReportService(db)

        first = service.daily(today)
        assert first.data["finance"]["orders_count"] == 0
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=today)
        second = service.daily(today)
        assert second.id == first.id and second.data["finance"]["orders_count"] == 1  # live, not a stale snapshot

        yesterday = today - timedelta(days=1)
        stored = service.daily(yesterday)
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=yesterday)
        assert service.daily(yesterday).data["finance"]["orders_count"] == stored.data["finance"]["orders_count"]

    def test_history_is_newest_first_and_limited(self, db: Session) -> None:
        service = ReportService(db)
        for offset in range(4):
            service.regenerate(REPORT_DATE - timedelta(days=offset))

        history = service.history(limit=3)

        assert [report.report_date for report in history] == [
            REPORT_DATE,
            REPORT_DATE - timedelta(days=1),
            REPORT_DATE - timedelta(days=2),
        ]

    @pytest.mark.parametrize(
        ("hour", "minute", "expected_offset"),
        [(10, 0, 1), (20, 59, 1), (21, 0, 0), (23, 30, 0)],
    )
    def test_default_report_date_follows_the_report_time(
        self, db: Session, hour: int, minute: int, expected_offset: int
    ) -> None:
        today = date(2026, 9, 16)
        now = datetime(today.year, today.month, today.day, hour, minute, tzinfo=app_time.business_tz())

        assert ReportService(db).default_report_date(now) == today - timedelta(days=expected_offset)

    def test_default_report_date_uses_the_admin_setting(self, db: Session) -> None:
        SettingsService(db).update({"daily_report_time": "18:00"})
        today = date(2026, 9, 16)
        now = datetime(today.year, today.month, today.day, 19, 0, tzinfo=app_time.business_tz())

        assert ReportService(db).default_report_date(now) == today


# --------------------------------------------------------------------------- tasks (06 §5)


class TestDailyReportTasks:
    def test_nothing_happens_before_the_report_time(
        self, db: Session, frozen_now: Callable[[datetime], None]
    ) -> None:
        today = app_time.business_today()
        frozen_now(business_instant(today, 12, 0))

        assert run_maybe_generate_daily_report(db) is None
        assert DailyReportRepository(db).get_by_date(today) is None

    def test_generates_after_the_report_time(self, db: Session, frozen_now: Callable[[datetime], None]) -> None:
        today = app_time.business_today()
        frozen_now(business_instant(today, 21, 30))

        generated = run_maybe_generate_daily_report(db)

        assert generated == today
        stored = DailyReportRepository(db).get_by_date(today)
        assert stored is not None
        assert stored.generated_at == business_instant(today, 21, 30)

    def test_is_idempotent_within_the_same_evening(
        self, db: Session, frozen_now: Callable[[datetime], None]
    ) -> None:
        today = app_time.business_today()
        frozen_now(business_instant(today, 21, 30))
        run_maybe_generate_daily_report(db)
        first_generated_at = DailyReportRepository(db).get_by_date(today).generated_at

        frozen_now(business_instant(today, 22, 45))
        assert run_maybe_generate_daily_report(db) is None
        assert DailyReportRepository(db).get_by_date(today).generated_at == first_generated_at

    def test_regenerates_a_report_built_before_the_report_time(
        self,
        db: Session,
        frozen_now: Callable[[datetime], None],
        make_product: Callable[..., Product],
        make_order: Callable[..., Any],
    ) -> None:
        today = app_time.business_today()
        product = make_product("Медовик", price="500.00")
        frozen_now(business_instant(today, 12, 0))
        ReportService(db).get_or_build(today)  # e.g. opened in the admin panel at noon
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=today)

        frozen_now(business_instant(today, 21, 5))
        assert run_maybe_generate_daily_report(db) == today

        stored = DailyReportRepository(db).get_by_date(today)
        assert stored.generated_at == business_instant(today, 21, 5)
        assert stored.data["finance"]["orders_count"] == 1

    def test_follows_the_report_time_from_settings(
        self, db: Session, frozen_now: Callable[[datetime], None]
    ) -> None:
        SettingsService(db).update({"daily_report_time": "18:00"})
        today = app_time.business_today()

        frozen_now(business_instant(today, 17, 30))
        assert run_maybe_generate_daily_report(db) is None

        frozen_now(business_instant(today, 18, 15))
        assert run_maybe_generate_daily_report(db) == today

    def test_explicit_now_overrides_the_clock(self, db: Session) -> None:
        today = app_time.business_today()
        noon = datetime(today.year, today.month, today.day, 12, 0, tzinfo=app_time.business_tz())

        assert run_maybe_generate_daily_report(db, now=noon) is None

    def test_generate_for_an_explicit_date(self, db: Session) -> None:
        assert run_generate_daily_report(db, REPORT_DATE) == REPORT_DATE
        assert DailyReportRepository(db).get_by_date(REPORT_DATE) is not None

    def test_generate_without_a_date_uses_the_default_rule(
        self, db: Session, frozen_now: Callable[[datetime], None]
    ) -> None:
        today = app_time.business_today()
        frozen_now(business_instant(today, 10, 0))

        assert run_generate_daily_report(db) == today - timedelta(days=1)


class TestCeleryWiring:
    """06 §5: beat polls every 15 minutes because the report time is an editable setting."""

    def test_tasks_are_registered_under_their_contract_names(self) -> None:
        assert "app.tasks.reports.generate_daily_report" in celery_app.tasks
        assert "app.tasks.reports.maybe_generate_daily_report" in celery_app.tasks

    def test_beat_runs_the_check_every_fifteen_minutes(self) -> None:
        entry = celery_app.conf.beat_schedule["maybe-generate-daily-report"]

        assert entry["task"] == "app.tasks.reports.maybe_generate_daily_report"
        assert entry["schedule"].minute == {0, 15, 30, 45}

    def test_task_bodies_open_their_own_session(
        self, db: Session, frozen_now: Callable[[datetime], None]
    ) -> None:
        today = app_time.business_today()
        frozen_now(business_instant(today, 21, 30))

        assert maybe_generate_daily_report() == today.isoformat()
        assert generate_daily_report(REPORT_DATE.isoformat()) == REPORT_DATE.isoformat()

        db.expire_all()
        assert DailyReportRepository(db).get_by_date(today) is not None
        assert DailyReportRepository(db).get_by_date(REPORT_DATE) is not None


# --------------------------------------------------------------------------- API


class TestReportsApi:
    @pytest.mark.usefixtures("spec_day")
    def test_staff_reads_a_report_by_date(self, client: TestClient, operator_headers: dict[str, str]) -> None:
        response = client.get(
            "/api/reports/daily", params={"date": REPORT_DATE.isoformat()}, headers=operator_headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["date"] == REPORT_DATE.isoformat()
        assert body["text"] == SPEC_REPORT_TEXT
        assert body["data"]["finance"]["revenue"] == 12500.0
        assert body["data"]["customers"]["new_customers"] == 7
        assert body["data"]["delivery"] == {"delivery_orders": 13, "pickup_orders": 5}
        assert body["data"]["production"]["date"] == REPORT_DATE.isoformat()
        assert body["data"]["production"]["items"][0] == {
            "product_id": body["data"]["production"]["items"][0]["product_id"],
            "product_name": "Красный бархат",
            "quantity": 4,
            "unit": "шт.",
        }
        assert body["generated_at"]

    def test_missing_report_is_built_and_stored(
        self, client: TestClient, admin_headers: dict[str, str], db: Session
    ) -> None:
        assert DailyReportRepository(db).get_by_date(REPORT_DATE) is None

        response = client.get("/api/reports/daily", params={"date": REPORT_DATE.isoformat()}, headers=admin_headers)

        assert response.status_code == 200
        assert DailyReportRepository(db).get_by_date(REPORT_DATE) is not None

    def test_default_date_follows_the_report_time(
        self, client: TestClient, admin_headers: dict[str, str], frozen_now: Callable[[datetime], None]
    ) -> None:
        today = date(2026, 9, 16)
        frozen_now(business_instant(today, 10, 0))

        response = client.get("/api/reports/daily", headers=admin_headers)

        assert response.status_code == 200
        assert response.json()["date"] == (today - timedelta(days=1)).isoformat()

    def test_default_date_is_today_after_the_report_time(
        self, client: TestClient, admin_headers: dict[str, str], frozen_now: Callable[[datetime], None]
    ) -> None:
        today = date(2026, 9, 16)
        frozen_now(business_instant(today, 21, 30))

        response = client.get("/api/reports/daily", headers=admin_headers)

        assert response.json()["date"] == today.isoformat()

    def test_admin_regenerates_a_report(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        db: Session,
        make_product: Callable[..., Product],
        make_order: Callable[..., Any],
    ) -> None:
        product = make_product("Медовик", price="500.00")
        ReportService(db).get_or_build(REPORT_DATE)
        make_order(items=[(product, 3)], status=OrderStatus.CONFIRMED, delivery_date=REPORT_DATE)

        response = client.post(
            "/api/reports/daily/generate", json={"date": REPORT_DATE.isoformat()}, headers=admin_headers
        )

        assert response.status_code == 200
        assert response.json()["data"]["finance"]["revenue"] == 1500.0
        assert "Медовик — 3" in response.json()["text"]

    def test_operator_cannot_regenerate(self, client: TestClient, operator_headers: dict[str, str]) -> None:
        response = client.post(
            "/api/reports/daily/generate", json={"date": REPORT_DATE.isoformat()}, headers=operator_headers
        )

        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    def test_history_has_no_data_block(
        self, client: TestClient, operator_headers: dict[str, str], db: Session
    ) -> None:
        service = ReportService(db)
        for offset in range(3):
            service.regenerate(REPORT_DATE - timedelta(days=offset))

        response = client.get("/api/reports/daily/history", params={"limit": 2}, headers=operator_headers)

        assert response.status_code == 200
        items = response.json()
        assert [item["date"] for item in items] == [
            REPORT_DATE.isoformat(),
            (REPORT_DATE - timedelta(days=1)).isoformat(),
        ]
        assert all("data" not in item for item in items)
        assert all(set(item) == {"date", "generated_at", "text"} for item in items)

    @pytest.mark.parametrize("path", ["/api/reports/daily", "/api/reports/daily/history"])
    def test_authentication_is_required(self, client: TestClient, path: str) -> None:
        assert client.get(path).status_code == 401

    def test_generate_requires_a_date(self, client: TestClient, admin_headers: dict[str, str]) -> None:
        response = client.post("/api/reports/daily/generate", json={}, headers=admin_headers)

        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
