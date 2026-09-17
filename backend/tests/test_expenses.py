"""Expenses — 03-business-rules.md §4 "Расходы и прибыль", 04-api.md §7a."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import business_today
from app.models.enums import ExpenseCategory
from app.models.expense import Expense
from app.models.user import User


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "expense_date": business_today().isoformat(),
        "category": "INGREDIENTS",
        "amount": 450.5,
        "comment": "Мука 10 кг, сливочный сыр",
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- create


def test_admin_creates_an_expense(
    client: TestClient, admin_headers: dict[str, str], admin_user: User, db: Session
) -> None:
    response = client.post("/api/expenses", json=_payload(), headers=admin_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["expense_date"] == business_today().isoformat()
    assert body["category"] == "INGREDIENTS"
    assert body["amount"] == 450.5
    assert body["comment"] == "Мука 10 кг, сливочный сыр"
    assert body["created_by_user_id"] == admin_user.id
    saved = db.get(Expense, body["id"])
    assert saved is not None and saved.amount == Decimal("450.50")


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": 0},
        {"amount": -10},
        {"category": "CAKES"},
        {"expense_date": "16.09.2026"},
        {"unknown_field": 1},
    ],
)
def test_invalid_payload_is_rejected(
    client: TestClient, admin_headers: dict[str, str], overrides: dict[str, object]
) -> None:
    response = client.post("/api/expenses", json=_payload(**overrides), headers=admin_headers)

    assert response.status_code == 422


def test_future_date_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    tomorrow = (business_today() + timedelta(days=1)).isoformat()

    response = client.post("/api/expenses", json=_payload(expense_date=tomorrow), headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "expense_date_in_future"


def test_blank_comment_is_stored_as_null(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post("/api/expenses", json=_payload(comment="   "), headers=admin_headers)

    assert response.status_code == 201
    assert response.json()["comment"] is None


def test_amount_must_be_positive_in_the_database(db: Session) -> None:
    db.add(Expense(expense_date=business_today(), category=ExpenseCategory.OTHER, amount=Decimal("0")))

    with pytest.raises(IntegrityError):
        db.commit()


# --------------------------------------------------------------------------- list


def test_list_is_newest_first_and_filterable(
    client: TestClient, operator_headers: dict[str, str], make_expense: Callable[..., Expense]
) -> None:
    today = business_today()
    old = make_expense("100.00", today - timedelta(days=10), ExpenseCategory.RENT)
    first_today = make_expense("200.00", today, ExpenseCategory.INGREDIENTS)
    second_today = make_expense("300.00", today, ExpenseCategory.PACKAGING)

    everything = client.get("/api/expenses", headers=operator_headers).json()
    assert [item["id"] for item in everything["items"]] == [second_today.id, first_today.id, old.id]
    assert everything["total"] == 3

    recent = client.get(
        "/api/expenses",
        params={"date_from": (today - timedelta(days=1)).isoformat(), "date_to": today.isoformat()},
        headers=operator_headers,
    ).json()
    assert [item["id"] for item in recent["items"]] == [second_today.id, first_today.id]

    rent = client.get("/api/expenses", params={"category": "RENT"}, headers=operator_headers).json()
    assert [item["id"] for item in rent["items"]] == [old.id]


def test_list_is_paginated(
    client: TestClient, admin_headers: dict[str, str], make_expense: Callable[..., Expense]
) -> None:
    for _ in range(3):
        make_expense()

    page = client.get("/api/expenses", params={"page": 2, "page_size": 2}, headers=admin_headers).json()

    assert (page["total"], page["page"], page["page_size"], len(page["items"])) == (3, 2, 2, 1)


def test_reversed_range_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    today = business_today()
    response = client.get(
        "/api/expenses",
        params={"date_from": today.isoformat(), "date_to": (today - timedelta(days=1)).isoformat()},
        headers=admin_headers,
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------- update / delete


def test_patch_changes_only_the_sent_fields_and_null_clears_the_comment(
    client: TestClient, admin_headers: dict[str, str], make_expense: Callable[..., Expense]
) -> None:
    expense = make_expense("100.00", comment="Коробки")

    response = client.patch(f"/api/expenses/{expense.id}", json={"amount": 125, "comment": None}, headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert (body["amount"], body["comment"], body["category"]) == (125.0, None, "INGREDIENTS")


def test_patch_rejects_a_future_date(
    client: TestClient, admin_headers: dict[str, str], make_expense: Callable[..., Expense]
) -> None:
    expense = make_expense()
    tomorrow = (business_today() + timedelta(days=1)).isoformat()

    response = client.patch(f"/api/expenses/{expense.id}", json={"expense_date": tomorrow}, headers=admin_headers)

    assert response.status_code == 422


def test_delete_removes_the_expense(
    client: TestClient, admin_headers: dict[str, str], make_expense: Callable[..., Expense], db: Session
) -> None:
    expense = make_expense()
    expense_id = expense.id

    assert client.delete(f"/api/expenses/{expense_id}", headers=admin_headers).status_code == 204
    db.expire_all()
    assert db.get(Expense, expense_id) is None
    assert client.delete(f"/api/expenses/{expense_id}", headers=admin_headers).status_code == 404


def test_operator_can_read_but_not_write(
    client: TestClient, operator_headers: dict[str, str], make_expense: Callable[..., Expense]
) -> None:
    expense = make_expense()

    assert client.get("/api/expenses", headers=operator_headers).status_code == 200
    assert client.post("/api/expenses", json=_payload(), headers=operator_headers).status_code == 403
    assert client.patch(f"/api/expenses/{expense.id}", json={"amount": 1}, headers=operator_headers).status_code == 403
    assert client.delete(f"/api/expenses/{expense.id}", headers=operator_headers).status_code == 403


def test_expenses_change_the_profit_in_statistics(client: TestClient, admin_headers: dict[str, str]) -> None:
    client.post("/api/expenses", json=_payload(amount=300, category="PACKAGING"), headers=admin_headers)

    body = client.get("/api/statistics", params={"period": "today"}, headers=admin_headers).json()

    assert body["profit_and_loss"]["expenses"] == 300.0
    assert body["profit_and_loss"]["profit"] == -300.0
    assert body["profit_and_loss"]["expenses_by_category"] == [{"category": "PACKAGING", "amount": 300.0}]
