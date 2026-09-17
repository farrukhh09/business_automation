"""Production route — docs/architecture/04-api.md §6 (ProductionService itself is covered by
test_production.py; this checks the HTTP layer: date default, role, response shape)."""

from app.core.time import business_today


def test_get_production_defaults_to_today_and_counts_confirmed_orders(client, admin_headers, make_order):
    order = make_order(status="CONFIRMED")
    response = client.get("/api/production", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == business_today().isoformat()
    assert body["orders_count"] >= 1
    names = [item["product_name"] for item in body["items"]]
    assert order.items[0].product_name in names


def test_get_production_excludes_draft_and_cancelled_orders(client, admin_headers, make_order):
    make_order(status="NEW")
    make_order(status="CANCELLED")
    response = client.get("/api/production", params={"date": business_today().isoformat()}, headers=admin_headers)
    assert response.json()["orders_count"] == 0


def test_production_requires_staff_role(client):
    assert client.get("/api/production").status_code == 401
