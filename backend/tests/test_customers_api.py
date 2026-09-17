"""Customers API — docs/architecture/04-api.md §3 (SPEC §45 "Клиенты")."""


def test_create_and_get_customer(client, admin_headers):
    response = client.post(
        "/api/customers", json={"name": "Новый Клиент", "phone": "900000000"}, headers=admin_headers
    )
    assert response.status_code == 201
    body = response.json()
    assert body["phone"] == "+992900000000"  # normalized (03 §2)
    assert body["customer_type"] == "NEW"

    fetched = client.get(f"/api/customers/{body['id']}", headers=admin_headers)
    assert fetched.status_code == 200
    assert fetched.json()["orders"] == []


def test_create_customer_with_invalid_phone_is_rejected(client, admin_headers):
    response = client.post("/api/customers", json={"name": "Клиент", "phone": "123"}, headers=admin_headers)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_phone"


def test_update_customer_partial(client, admin_headers, make_customer):
    customer = make_customer("Клиент", phone="900000000", notes="старая заметка")
    response = client.patch(f"/api/customers/{customer.id}", json={"notes": "новая заметка"}, headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["notes"] == "новая заметка"
    assert body["name"] == "Клиент"  # untouched field stays as is


def test_list_customers_search_and_type_filter(client, admin_headers, make_customer):
    # is_new is denormalized and kept in sync by CustomerService (03 §2); the sync itself is
    # covered by test_orders_api.py::test_customer_becomes_regular_after_first_completed_order.
    regular = make_customer("Постоянный Клиент", phone="900000001", is_new=False)
    new_customer = make_customer("Новый Клиент", phone="900000002")

    regulars = client.get("/api/customers", params={"customer_type": "regular"}, headers=admin_headers)
    regular_ids = [item["id"] for item in regulars.json()["items"]]
    assert regular.id in regular_ids
    assert new_customer.id not in regular_ids

    found = client.get("/api/customers", params={"search": "Новый Клиент"}, headers=admin_headers)
    assert [item["id"] for item in found.json()["items"]] == [new_customer.id]


def test_customers_require_staff_role(client, make_customer):
    customer = make_customer()
    assert client.get(f"/api/customers/{customer.id}").status_code == 401
