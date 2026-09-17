"""Orders API — docs/architecture/04-api.md §5 and 03-business-rules.md §1-§3 (SPEC §45 "Заказы")."""

from datetime import timedelta
from decimal import Decimal

from app.core.time import business_today


def _iso_tomorrow():
    return (business_today() + timedelta(days=1)).isoformat()


def test_create_order_snapshots_price_and_computes_total(client, admin_headers, make_customer, make_product):
    customer = make_customer("Клиент", phone="900000000")
    product = make_product("Медовик", price="150.00")
    payload = {
        "customer_id": customer.id,
        "items": [{"product_id": product.id, "quantity": 2}],
        "delivery_type": "PICKUP",
        "delivery_date": _iso_tomorrow(),
        "delivery_time": "18:00",
        "confirm": False,
    }
    response = client.post("/api/orders", json=payload, headers=admin_headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "NEW"
    assert body["total_amount"] == 300.0
    assert body["items"][0]["unit_price"] == 150.0

    # A later price change never touches an already-placed order (03 §1.4).
    client.patch(f"/api/products/{product.id}", json={"price": 999.0}, headers=admin_headers)
    detail = client.get(f"/api/orders/{body['id']}", headers=admin_headers).json()
    assert detail["items"][0]["unit_price"] == 150.0
    assert detail["total_amount"] == 300.0


def test_create_order_rejects_client_supplied_amounts(client, admin_headers, make_customer, make_product):
    customer = make_customer()
    product = make_product(price="10.00")
    payload = {
        "customer_id": customer.id,
        "items": [{"product_id": product.id, "quantity": 1, "unit_price": 999.0}],
        "delivery_type": "PICKUP",
        "delivery_date": _iso_tomorrow(),
        "delivery_time": "18:00",
    }
    response = client.post("/api/orders", json=payload, headers=admin_headers)
    assert response.status_code == 422


def test_confirm_incomplete_order_is_rejected(client, admin_headers, make_customer, make_product):
    customer = make_customer()
    product = make_product(price="10.00")
    payload = {
        "customer_id": customer.id,
        "items": [{"product_id": product.id, "quantity": 1}],
        "delivery_type": "DELIVERY",
        "delivery_date": _iso_tomorrow(),
        "delivery_time": "18:00",
        "confirm": True,
    }
    response = client.post("/api/orders", json=payload, headers=admin_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "NEW"  # confirm=true did not force it: address/location are missing
    assert "address" in body["missing_fields"]
    assert "location" in body["missing_fields"]


def test_status_transition_endpoint_enforces_the_table(client, admin_headers, make_order):
    order = make_order(status="CONFIRMED")
    forbidden = client.post(f"/api/orders/{order.id}/status", json={"status": "COMPLETED"}, headers=admin_headers)
    assert forbidden.status_code == 422
    assert forbidden.json()["code"] == "invalid_status_transition"

    allowed = client.post(f"/api/orders/{order.id}/status", json={"status": "PREPARING"}, headers=admin_headers)
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "PREPARING"


def test_handed_to_courier_requires_delivery_type(client, admin_headers, make_order):
    pickup_order = make_order(status="READY", delivery_type="PICKUP")
    response = client.post(
        f"/api/orders/{pickup_order.id}/status", json={"status": "HANDED_TO_COURIER"}, headers=admin_headers
    )
    assert response.status_code == 422


def test_operator_cannot_change_restricted_fields(client, operator_headers, make_order):
    order = make_order(status="NEW")
    response = client.patch(
        f"/api/orders/{order.id}",
        json={"delivery_date": _iso_tomorrow()},
        headers=operator_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_operator_can_change_comment_and_payment_fields(client, operator_headers, make_order):
    order = make_order(status="CONFIRMED")
    response = client.patch(
        f"/api/orders/{order.id}",
        json={"comment": "Позвонить перед доставкой"},
        headers=operator_headers,
    )
    assert response.status_code == 200
    assert response.json()["comment"] == "Позвонить перед доставкой"


def test_replacing_items_recalculates_total_and_payment_status(client, admin_headers, make_order, make_product):
    order = make_order(status="CONFIRMED", paid_amount="100.00")
    assert order.payment_status.value == "PAID"  # 1 item at 100.00 by default (conftest factory)
    extra = make_product("Дополнительный торт", price="50.00")
    response = client.patch(
        f"/api/orders/{order.id}",
        json={"items": [{"product_id": order.items[0].product_id, "quantity": 1}, {"product_id": extra.id, "quantity": 1}]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_amount"] == 150.0
    assert body["payment_status"] == "PARTIALLY_PAID"  # 03 §3: total change re-evaluates payment


def test_payments_flow_paid_partially_paid_refunded(client, admin_headers, make_order):
    order = make_order(status="CONFIRMED")  # total = 100.00, unpaid
    total = float(order.total_amount)

    paid = client.post(
        f"/api/orders/{order.id}/payments", json={"kind": "PAYMENT", "amount": total}, headers=admin_headers
    )
    assert paid.status_code == 200
    assert paid.json()["payment_status"] == "PAID"
    assert paid.json()["paid_amount"] == total

    refund = client.post(
        f"/api/orders/{order.id}/payments", json={"kind": "REFUND", "amount": total}, headers=admin_headers
    )
    assert refund.status_code == 200
    # 03 §3: paid == 0 with a refund on the ledger is REFUNDED, not UNPAID.
    assert refund.json()["payment_status"] == "REFUNDED"
    assert refund.json()["paid_amount"] == 0.0


def test_payment_status_partially_paid_requires_amount(client, admin_headers, make_order):
    order = make_order(status="CONFIRMED")
    response = client.patch(
        f"/api/orders/{order.id}", json={"payment_status": "PARTIALLY_PAID"}, headers=admin_headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == "paid_amount_required"

    ok = client.patch(
        f"/api/orders/{order.id}",
        json={"payment_status": "PARTIALLY_PAID", "paid_amount": float(order.total_amount) / 2},
        headers=admin_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["payment_status"] == "PARTIALLY_PAID"


def test_refund_exceeding_paid_amount_is_rejected(client, admin_headers, make_order):
    order = make_order(status="CONFIRMED", paid_amount="50.00")
    response = client.post(
        f"/api/orders/{order.id}/payments", json={"kind": "REFUND", "amount": 999.0}, headers=admin_headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == "refund_exceeds_paid"


def test_cancel_order_sets_reason_and_status(client, admin_headers, make_order):
    order = make_order(status="NEW")
    response = client.post(f"/api/orders/{order.id}/cancel", json={"reason": "Клиент передумал"}, headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert body["cancel_reason"] == "Клиент передумал"


def test_events_journal_records_every_change(client, admin_headers, make_order, make_customer):
    customer = make_customer("Клиент", phone="900000000")
    order = make_order(customer=customer, status="NEW")
    comment_response = client.patch(
        f"/api/orders/{order.id}", json={"comment": "Комментарий"}, headers=admin_headers
    )
    assert comment_response.status_code == 200
    status_response = client.post(
        f"/api/orders/{order.id}/status", json={"status": "WAITING_CONFIRMATION"}, headers=admin_headers
    )
    assert status_response.status_code == 200

    events = client.get(f"/api/orders/{order.id}/events", headers=admin_headers)
    assert events.status_code == 200
    types = [event["event_type"] for event in events.json()]
    assert "CREATED" not in types  # make_order inserts directly via the ORM, bypassing the service
    assert "UPDATED" in types
    assert "STATUS_CHANGED" in types


def test_list_orders_filters_by_status_and_search(client, admin_headers, make_order, make_customer):
    findme = make_customer("Уникальное Имя")
    target = make_order(customer=findme, status="CONFIRMED")
    make_order(status="NEW")

    by_status = client.get("/api/orders", params={"status": "CONFIRMED"}, headers=admin_headers)
    assert by_status.status_code == 200
    ids = [item["id"] for item in by_status.json()["items"]]
    assert target.id in ids

    by_search = client.get("/api/orders", params={"search": "Уникальное"}, headers=admin_headers)
    assert [item["id"] for item in by_search.json()["items"]] == [target.id]


def test_customer_becomes_regular_after_first_completed_order(client, admin_headers, make_order):
    order = make_order(status="READY", delivery_type="PICKUP")
    customer_id = order.customer_id

    before = client.get(f"/api/customers/{customer_id}", headers=admin_headers).json()
    assert before["customer_type"] == "NEW"

    client.post(f"/api/orders/{order.id}/status", json={"status": "COMPLETED"}, headers=admin_headers)
    after = client.get(f"/api/customers/{customer_id}", headers=admin_headers).json()
    assert after["customer_type"] == "REGULAR"
    assert after["orders_count"] == 1
    assert after["total_spent"] == float(order.total_amount)

    # Reverting the mistake makes the customer new again (03 §2).
    client.post(f"/api/orders/{order.id}/status", json={"status": "READY"}, headers=admin_headers)
    reverted = client.get(f"/api/customers/{customer_id}", headers=admin_headers).json()
    assert reverted["customer_type"] == "NEW"


def test_get_order_requires_authentication(client, make_order):
    order = make_order()
    response = client.get(f"/api/orders/{order.id}")
    assert response.status_code == 401
