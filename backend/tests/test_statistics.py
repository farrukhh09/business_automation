"""Statistics, dashboard and time series — 03-business-rules.md §4, 04-api.md §7, SPEC §20–21, §32, §45."""

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ValidationError
from app.core.time import business_today
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.enums import DeliveryType, ExpenseCategory, OrderStatus, PaymentStatus
from app.models.expense import Expense
from app.models.product import Product
from app.services.statistics_service import (
    MAX_PERIOD_DAYS,
    Period,
    StatisticsService,
    profit_and_loss,
    resolve_date_basis,
    resolve_period,
)

TODAY = date(2026, 9, 16)
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture
def product(make_product: Callable[..., Product]) -> Product:
    return make_product("Медовик", price="500.00")


@pytest.fixture
def service(db: Session) -> StatisticsService:
    return StatisticsService(db)


def period(date_from: date, date_to: date | None = None) -> Period:
    return Period("custom", date_from, date_to or date_from)


# --------------------------------------------------------------------------- periods


class TestResolvePeriod:
    def test_today(self) -> None:
        resolved = resolve_period("today", today=TODAY)

        assert (resolved.name, resolved.date_from, resolved.date_to) == ("today", TODAY, TODAY)
        assert resolved.days == 1

    def test_yesterday(self) -> None:
        resolved = resolve_period("yesterday", today=TODAY)

        assert (resolved.date_from, resolved.date_to) == (YESTERDAY, YESTERDAY)

    def test_week_is_the_last_seven_days_including_today(self) -> None:
        resolved = resolve_period("week", today=TODAY)

        assert (resolved.date_from, resolved.date_to) == (date(2026, 9, 10), TODAY)
        assert resolved.days == 7

    def test_month_starts_on_the_first(self) -> None:
        resolved = resolve_period("month", today=TODAY)

        assert (resolved.date_from, resolved.date_to) == (date(2026, 9, 1), TODAY)

    def test_custom_range(self) -> None:
        resolved = resolve_period("custom", date(2026, 1, 1), date(2026, 1, 31), today=TODAY)

        assert (resolved.name, resolved.date_from, resolved.date_to, resolved.days) == (
            "custom",
            date(2026, 1, 1),
            date(2026, 1, 31),
            31,
        )

    def test_defaults_to_the_current_business_date(self) -> None:
        assert resolve_period().date_to == business_today()

    @pytest.mark.parametrize("name", ["TODAY", " week ", "Month"])
    def test_name_is_case_and_space_insensitive(self, name: str) -> None:
        assert resolve_period(name, today=TODAY).name == name.strip().lower()

    def test_custom_without_dates_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            resolve_period("custom", today=TODAY)

        assert exc.value.status_code == 422
        assert "date_from" in exc.value.detail

    def test_reversed_range_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            resolve_period("custom", date(2026, 2, 1), date(2026, 1, 1))

        assert exc.value.status_code == 422

    def test_range_longer_than_the_limit_is_rejected(self) -> None:
        start = date(2025, 1, 1)
        with pytest.raises(ValidationError):
            resolve_period("custom", start, start + timedelta(days=MAX_PERIOD_DAYS))

        assert resolve_period("custom", start, start + timedelta(days=MAX_PERIOD_DAYS - 1)).days == MAX_PERIOD_DAYS

    def test_unknown_period_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            resolve_period("quarter", today=TODAY)

    def test_date_basis(self) -> None:
        assert resolve_date_basis(None) == "delivery"
        assert resolve_date_basis("created") == "created"
        with pytest.raises(BadRequestError):
            resolve_date_basis("confirmed")


# --------------------------------------------------------------------------- finance


class TestFinance:
    def test_revenue_orders_count_and_average_check(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[(product, 2)], status=OrderStatus.CONFIRMED, delivery_date=TODAY)  # 1000
        make_order(items=[(product, 1)], status=OrderStatus.COMPLETED, delivery_date=TODAY)  # 500
        make_order(items=[(product, 3)], status=OrderStatus.READY, delivery_date=TODAY)  # 1500

        finance = service.statistics(period(TODAY)).finance

        assert finance.revenue == Decimal("3000.00")
        assert finance.orders_count == 3
        assert finance.average_check == Decimal("1000.00")

    def test_average_check_is_zero_without_orders(self, service: StatisticsService) -> None:
        finance = service.statistics(period(TODAY)).finance

        assert finance.orders_count == 0
        assert finance.revenue == Decimal("0.00")
        assert finance.average_check == Decimal("0.00")

    def test_average_check_is_rounded_half_up(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[{"product": product, "quantity": 1, "unit_price": "100.00"}], status=OrderStatus.CONFIRMED, delivery_date=TODAY)
        make_order(items=[{"product": product, "quantity": 1, "unit_price": "100.01"}], status=OrderStatus.CONFIRMED, delivery_date=TODAY)
        make_order(items=[{"product": product, "quantity": 1, "unit_price": "100.00"}], status=OrderStatus.CONFIRMED, delivery_date=TODAY)

        finance = service.statistics(period(TODAY)).finance

        assert finance.revenue == Decimal("300.01")
        assert finance.average_check == Decimal("100.00")

    def test_payment_counts_and_amounts(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[(product, 2)], status=OrderStatus.CONFIRMED, delivery_date=TODAY, paid_amount="1000.00")
        make_order(items=[(product, 2)], status=OrderStatus.CONFIRMED, delivery_date=TODAY, paid_amount="400.00")
        make_order(items=[(product, 2)], status=OrderStatus.CONFIRMED, delivery_date=TODAY)

        finance = service.statistics(period(TODAY)).finance

        assert finance.revenue == Decimal("3000.00")
        assert finance.paid_orders_count == 1
        assert finance.partially_paid_orders_count == 1
        assert finance.unpaid_orders_count == 1
        assert finance.paid_amount == Decimal("1400.00")
        assert finance.unpaid_amount == Decimal("1600.00")

    def test_refunded_orders_are_not_counted_as_paid_or_unpaid(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(
            items=[(product, 1)],
            status=OrderStatus.CONFIRMED,
            delivery_date=TODAY,
            payment_status=PaymentStatus.REFUNDED,
        )

        finance = service.statistics(period(TODAY)).finance

        assert (finance.paid_orders_count, finance.partially_paid_orders_count, finance.unpaid_orders_count) == (0, 0, 0)
        assert finance.paid_amount == Decimal("0.00")

    def test_overpayment_does_not_inflate_the_paid_amount(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=TODAY, paid_amount="900.00")

        finance = service.statistics(period(TODAY)).finance

        assert finance.revenue == Decimal("500.00")
        assert finance.paid_amount == Decimal("500.00")
        assert finance.unpaid_amount == Decimal("0.00")

    @pytest.mark.parametrize(
        "status",
        [OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.HANDED_TO_COURIER, OrderStatus.COMPLETED],
    )
    def test_valid_statuses_are_counted(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any], status: OrderStatus
    ) -> None:
        make_order(items=[(product, 1)], status=status, delivery_date=TODAY, delivery_type=DeliveryType.DELIVERY)

        assert service.statistics(period(TODAY)).finance.orders_count == 1

    @pytest.mark.parametrize("status", [OrderStatus.NEW, OrderStatus.WAITING_CONFIRMATION, OrderStatus.CANCELLED])
    def test_draft_and_cancelled_orders_are_excluded(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any], status: OrderStatus
    ) -> None:
        make_order(items=[(product, 4)], status=status, delivery_date=TODAY)

        finance = service.statistics(period(TODAY)).finance

        assert finance.orders_count == 0
        assert finance.revenue == Decimal("0.00")

    def test_orders_outside_the_period_are_excluded(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=TODAY - timedelta(days=1))
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=TODAY + timedelta(days=1))

        assert service.statistics(period(TODAY)).finance.orders_count == 0
        assert service.statistics(period(TODAY - timedelta(days=1), TODAY + timedelta(days=1))).finance.orders_count == 2


# --------------------------------------------------------------------------- expenses and profit


class TestProfitAndLoss:
    def test_profit_is_revenue_minus_expenses_of_the_period(
        self,
        service: StatisticsService,
        product: Product,
        make_order: Callable[..., Any],
        make_expense: Callable[..., Expense],
    ) -> None:
        make_order(items=[(product, 6)], status=OrderStatus.CONFIRMED, delivery_date=TODAY)  # 3000
        make_expense("800.00", TODAY, ExpenseCategory.INGREDIENTS)
        make_expense("200.00", TODAY, ExpenseCategory.PACKAGING)
        make_expense("5000.00", YESTERDAY, ExpenseCategory.RENT)  # another period

        pnl = service.statistics(period(TODAY)).profit_and_loss

        assert pnl.revenue == Decimal("3000.00")
        assert pnl.expenses == Decimal("1000.00")
        assert pnl.profit == Decimal("2000.00")
        assert pnl.margin_percent == 66.7

    def test_loss_gives_a_negative_profit_and_margin(
        self,
        service: StatisticsService,
        product: Product,
        make_order: Callable[..., Any],
        make_expense: Callable[..., Expense],
    ) -> None:
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=TODAY)  # 500
        make_expense("750.00", TODAY)

        pnl = service.statistics(period(TODAY)).profit_and_loss

        assert pnl.profit == Decimal("-250.00")
        assert pnl.margin_percent == -50.0

    def test_margin_is_null_without_revenue(
        self, service: StatisticsService, make_expense: Callable[..., Expense]
    ) -> None:
        make_expense("120.00", TODAY)

        pnl = service.statistics(period(TODAY)).profit_and_loss

        assert (pnl.revenue, pnl.expenses, pnl.profit) == (Decimal("0.00"), Decimal("120.00"), Decimal("-120.00"))
        assert pnl.margin_percent is None

    def test_empty_period_is_all_zeros(self, service: StatisticsService) -> None:
        pnl = service.statistics(period(TODAY)).profit_and_loss

        assert (pnl.revenue, pnl.expenses, pnl.profit) == (Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))
        assert pnl.margin_percent is None
        assert pnl.expenses_by_category == []

    def test_categories_are_summed_and_sorted_largest_first(
        self, service: StatisticsService, make_expense: Callable[..., Expense]
    ) -> None:
        make_expense("100.00", TODAY, ExpenseCategory.PACKAGING)
        make_expense("300.00", TODAY, ExpenseCategory.INGREDIENTS)
        make_expense("150.00", TODAY, ExpenseCategory.PACKAGING)
        make_expense("250.00", TODAY, ExpenseCategory.DELIVERY)

        totals = service.statistics(period(TODAY)).profit_and_loss.expenses_by_category

        assert [(item.category, item.amount) for item in totals] == [
            (ExpenseCategory.INGREDIENTS, Decimal("300.00")),
            (ExpenseCategory.PACKAGING, Decimal("250.00")),
            (ExpenseCategory.DELIVERY, Decimal("250.00")),  # a tie keeps the category order
        ]

    def test_expenses_follow_their_own_date_whatever_the_basis(
        self,
        service: StatisticsService,
        product: Product,
        make_order: Callable[..., Any],
        make_expense: Callable[..., Expense],
    ) -> None:
        make_order(
            items=[(product, 2)],
            status=OrderStatus.CONFIRMED,
            delivery_date=date(2026, 10, 1),
            confirmed_at=datetime(2026, 9, 16, 8, 0, tzinfo=UTC),
        )
        make_expense("400.00", TODAY)

        pnl = service.statistics(period(TODAY), "created").profit_and_loss

        assert (pnl.revenue, pnl.expenses, pnl.profit) == (Decimal("1000.00"), Decimal("400.00"), Decimal("600.00"))

    def test_margin_is_rounded_half_up_to_one_decimal(self) -> None:
        pnl = profit_and_loss(Decimal("3000.00"), {ExpenseCategory.OTHER: Decimal("1000.50")})

        assert pnl.profit == Decimal("1999.50")
        assert pnl.margin_percent == 66.7  # 66.65 → 66.7


# --------------------------------------------------------------------------- date basis


class TestDateBasis:
    def test_delivery_basis_uses_the_delivery_date(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(
            items=[(product, 1)],
            status=OrderStatus.CONFIRMED,
            delivery_date=TODAY,
            confirmed_at=datetime(2026, 9, 10, 8, 0, tzinfo=UTC),
        )

        assert service.statistics(period(TODAY), "delivery").finance.orders_count == 1
        assert service.statistics(period(date(2026, 9, 10)), "delivery").finance.orders_count == 0

    def test_created_basis_uses_the_confirmation_date(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(
            items=[(product, 1)],
            status=OrderStatus.CONFIRMED,
            delivery_date=TODAY,
            confirmed_at=datetime(2026, 9, 10, 8, 0, tzinfo=UTC),
        )

        assert service.statistics(period(date(2026, 9, 10)), "created").finance.orders_count == 1
        assert service.statistics(period(TODAY), "created").finance.orders_count == 0

    def test_created_basis_converts_to_the_business_timezone(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        # 2026-09-15 20:00 UTC is 2026-09-16 01:00 in Asia/Dushanbe (UTC+5).
        make_order(
            items=[(product, 1)],
            status=OrderStatus.CONFIRMED,
            delivery_date=date(2026, 10, 1),
            confirmed_at=datetime(2026, 9, 15, 20, 0, tzinfo=UTC),
        )

        assert service.statistics(period(date(2026, 9, 15)), "created").finance.orders_count == 0
        assert service.statistics(period(TODAY), "created").finance.orders_count == 1

    def test_echoes_the_period_block(self, service: StatisticsService) -> None:
        out = service.statistics("week", "created", today=TODAY)

        assert out.period.name == "week"
        assert out.period.date_from == date(2026, 9, 10)
        assert out.period.date_to == TODAY
        assert out.period.date_basis == "created"

    def test_unknown_basis_is_rejected(self, service: StatisticsService) -> None:
        with pytest.raises(BadRequestError):
            service.statistics(period(TODAY), "confirmed")


# --------------------------------------------------------------------------- customers


class TestCustomers:
    def test_new_and_regular_customers_are_split_by_the_order_snapshot(
        self,
        service: StatisticsService,
        product: Product,
        make_customer: Callable[..., Customer],
        make_order: Callable[..., Any],
    ) -> None:
        for _ in range(2):
            make_order(
                customer=make_customer(),
                items=[(product, 1)],
                status=OrderStatus.CONFIRMED,
                delivery_date=TODAY,
                is_repeat_customer=False,
            )
        for _ in range(3):
            make_order(
                customer=make_customer(),
                items=[(product, 1)],
                status=OrderStatus.CONFIRMED,
                delivery_date=TODAY,
                is_repeat_customer=True,
            )

        customers = service.statistics(period(TODAY)).customers

        assert customers.new_customers == 2
        assert customers.regular_customers == 3
        assert customers.new_customer_orders == 2
        assert customers.regular_customer_orders == 3

    def test_a_customer_is_counted_once_in_the_group_of_the_first_order(
        self,
        service: StatisticsService,
        product: Product,
        make_customer: Callable[..., Customer],
        make_order: Callable[..., Any],
    ) -> None:
        customer = make_customer("Алия")
        make_order(
            customer=customer,
            items=[(product, 1)],
            status=OrderStatus.CONFIRMED,
            delivery_date=TODAY,
            delivery_time=time(10, 0),
            is_repeat_customer=False,
        )
        make_order(
            customer=customer,
            items=[(product, 1)],
            status=OrderStatus.COMPLETED,
            delivery_date=TODAY + timedelta(days=1),
            is_repeat_customer=True,
        )

        customers = service.statistics(period(TODAY, TODAY + timedelta(days=1))).customers

        assert customers.new_customers == 1
        assert customers.regular_customers == 0
        assert customers.new_customer_orders == 2
        assert customers.regular_customer_orders == 0

    def test_total_customers_counts_the_whole_database(
        self,
        service: StatisticsService,
        product: Product,
        make_customer: Callable[..., Customer],
        make_order: Callable[..., Any],
    ) -> None:
        make_order(customer=make_customer(), items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=TODAY)
        make_customer("Без заказов")

        assert service.statistics(period(TODAY)).customers.total_customers == 2

    def test_customers_registered_counts_the_period_only(
        self, service: StatisticsService, make_customer: Callable[..., Customer], db: Session
    ) -> None:
        old = make_customer("Старый")
        old.created_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        fresh = make_customer("Новый")
        fresh.created_at = datetime(2026, 9, 16, 6, 0, tzinfo=UTC)
        db.commit()

        assert service.statistics(period(TODAY)).customers.customers_registered == 1
        assert service.statistics(period(date(2026, 9, 1), TODAY)).customers.customers_registered == 2


# --------------------------------------------------------------------------- delivery


class TestDeliverySplit:
    def test_delivery_and_pickup_orders(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        for _ in range(3):
            make_order(
                items=[(product, 1)],
                status=OrderStatus.CONFIRMED,
                delivery_date=TODAY,
                delivery_type=DeliveryType.DELIVERY,
            )
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=TODAY, delivery_type=DeliveryType.PICKUP)
        make_order(items=[(product, 1)], status=OrderStatus.CANCELLED, delivery_date=TODAY, delivery_type=DeliveryType.DELIVERY)

        delivery = service.statistics(period(TODAY)).delivery

        assert delivery.delivery_orders == 3
        assert delivery.pickup_orders == 1


# --------------------------------------------------------------------------- dashboard


class TestDashboard:
    def test_summarizes_today(
        self,
        service: StatisticsService,
        db: Session,
        product: Product,
        make_customer: Callable[..., Customer],
        make_order: Callable[..., Any],
    ) -> None:
        today = business_today()
        make_order(
            customer=make_customer(),
            items=[(product, 2)],
            status=OrderStatus.CONFIRMED,
            delivery_date=today,
            delivery_type=DeliveryType.DELIVERY,
            paid_amount="1000.00",
            is_repeat_customer=False,
        )
        make_order(
            customer=make_customer(),
            items=[(product, 1)],
            status=OrderStatus.READY,
            delivery_date=today,
            delivery_type=DeliveryType.PICKUP,
            is_repeat_customer=True,
        )
        waiting = make_order(items=[(product, 1)], status=OrderStatus.WAITING_CONFIRMATION, delivery_date=today)
        db.add(Conversation(customer_id=waiting.customer_id, needs_attention=True))
        db.commit()

        dashboard = service.dashboard()

        assert dashboard.date == today
        assert dashboard.orders_today == 2
        assert dashboard.revenue_today == Decimal("1500.00")
        assert dashboard.unpaid_orders_count == 1
        assert dashboard.new_customers_today == 1
        assert dashboard.regular_customers_today == 1
        assert dashboard.delivery_orders_today == 1
        assert dashboard.pickup_orders_today == 1
        assert dashboard.waiting_confirmation_count == 1
        assert dashboard.conversations_needing_attention == 1

    def test_recent_orders_are_the_ten_newest_in_list_form(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        orders = [
            make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=business_today())
            for _ in range(12)
        ]

        recent = service.dashboard().recent_orders

        assert len(recent) == 10
        assert recent[0].id == orders[-1].id
        assert recent[0].items_summary == "Медовик ×1"
        assert recent[0].items_count == 1
        assert recent[0].customer.id == orders[-1].customer_id

    def test_old_unpaid_orders_are_not_counted(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(
            items=[(product, 1)],
            status=OrderStatus.CONFIRMED,
            delivery_date=business_today() - timedelta(days=45),
        )

        assert service.dashboard().unpaid_orders_count == 0


# --------------------------------------------------------------------------- time series


class TestTimeseries:
    def test_every_day_of_the_range_is_present_with_zeros(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(items=[(product, 2)], status=OrderStatus.CONFIRMED, delivery_date=TODAY, paid_amount="400.00")

        points = service.timeseries(TODAY - timedelta(days=2), TODAY)

        assert [point.date for point in points] == [TODAY - timedelta(days=2), YESTERDAY, TODAY]
        assert [point.orders_count for point in points] == [0, 0, 1]
        assert [point.revenue for point in points] == [Decimal("0.00"), Decimal("0.00"), Decimal("1000.00")]
        assert points[-1].paid_amount == Decimal("400.00")

    def test_created_basis_buckets_by_confirmation_day(
        self, service: StatisticsService, product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(
            items=[(product, 1)],
            status=OrderStatus.CONFIRMED,
            delivery_date=date(2026, 10, 1),
            confirmed_at=datetime(2026, 9, 15, 20, 0, tzinfo=UTC),
        )

        points = service.timeseries(YESTERDAY, TODAY, "created")

        assert [(point.date, point.orders_count) for point in points] == [(YESTERDAY, 0), (TODAY, 1)]

    def test_expenses_and_profit_per_day(
        self,
        service: StatisticsService,
        product: Product,
        make_order: Callable[..., Any],
        make_expense: Callable[..., Expense],
    ) -> None:
        make_order(items=[(product, 2)], status=OrderStatus.CONFIRMED, delivery_date=TODAY)  # 1000
        make_expense("300.00", TODAY)
        make_expense("50.00", TODAY, ExpenseCategory.PACKAGING)
        make_expense("200.00", YESTERDAY)

        points = service.timeseries(YESTERDAY, TODAY)

        assert [(point.expenses, point.profit) for point in points] == [
            (Decimal("200.00"), Decimal("-200.00")),
            (Decimal("350.00"), Decimal("650.00")),
        ]

    def test_defaults_to_the_last_thirty_days(self, service: StatisticsService) -> None:
        points = service.timeseries(today=TODAY)

        assert len(points) == 30
        assert points[0].date == TODAY - timedelta(days=29)
        assert points[-1].date == TODAY

    def test_range_is_validated(self, service: StatisticsService) -> None:
        with pytest.raises(ValidationError):
            service.timeseries(TODAY, YESTERDAY)


# --------------------------------------------------------------------------- API


class TestStatisticsApi:
    def test_statistics_for_staff(
        self, client: TestClient, operator_headers: dict[str, str], product: Product, make_order: Callable[..., Any]
    ) -> None:
        make_order(
            items=[(product, 2)],
            status=OrderStatus.CONFIRMED,
            delivery_date=business_today(),
            delivery_type=DeliveryType.DELIVERY,
            paid_amount="1000.00",
        )

        response = client.get("/api/statistics", params={"period": "today"}, headers=operator_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["period"] == {
            "name": "today",
            "date_from": business_today().isoformat(),
            "date_to": business_today().isoformat(),
            "date_basis": "delivery",
        }
        assert body["finance"]["revenue"] == 1000.0
        assert body["finance"]["paid_amount"] == 1000.0
        assert body["finance"]["average_check"] == 1000.0
        assert body["delivery"] == {"delivery_orders": 1, "pickup_orders": 0}
        assert body["profit_and_loss"] == {
            "revenue": 1000.0,
            "expenses": 0.0,
            "profit": 1000.0,
            "margin_percent": 100.0,
            "expenses_by_category": [],
        }
        assert set(body["customers"]) == {
            "new_customers",
            "regular_customers",
            "total_customers",
            "new_customer_orders",
            "regular_customer_orders",
            "customers_registered",
        }

    def test_custom_period_requires_dates(self, client: TestClient, admin_headers: dict[str, str]) -> None:
        response = client.get("/api/statistics", params={"period": "custom"}, headers=admin_headers)

        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"

    def test_custom_period_range_is_validated(self, client: TestClient, admin_headers: dict[str, str]) -> None:
        response = client.get(
            "/api/statistics",
            params={"period": "custom", "date_from": "2026-09-20", "date_to": "2026-09-01"},
            headers=admin_headers,
        )

        assert response.status_code == 422

    def test_unknown_period_is_rejected_by_validation(self, client: TestClient, admin_headers: dict[str, str]) -> None:
        response = client.get("/api/statistics", params={"period": "quarter"}, headers=admin_headers)

        assert response.status_code == 422

    def test_dashboard_for_staff(self, client: TestClient, operator_headers: dict[str, str]) -> None:
        response = client.get("/api/statistics/dashboard", headers=operator_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["date"] == business_today().isoformat()
        assert body["recent_orders"] == []
        assert body["conversations_needing_attention"] == 0

    def test_timeseries_for_staff(
        self, client: TestClient, admin_headers: dict[str, str], product: Product, make_order: Callable[..., Any]
    ) -> None:
        today = business_today()
        make_order(items=[(product, 1)], status=OrderStatus.CONFIRMED, delivery_date=today)

        response = client.get(
            "/api/statistics/timeseries",
            params={"date_from": (today - timedelta(days=1)).isoformat(), "date_to": today.isoformat()},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert response.json() == [
            {
                "date": (today - timedelta(days=1)).isoformat(),
                "revenue": 0.0,
                "orders_count": 0,
                "paid_amount": 0.0,
                "expenses": 0.0,
                "profit": 0.0,
            },
            {
                "date": today.isoformat(),
                "revenue": 500.0,
                "orders_count": 1,
                "paid_amount": 0.0,
                "expenses": 0.0,
                "profit": 500.0,
            },
        ]

    @pytest.mark.parametrize("path", ["/api/statistics", "/api/statistics/dashboard", "/api/statistics/timeseries"])
    def test_authentication_is_required(self, client: TestClient, path: str) -> None:
        assert client.get(path).status_code == 401
