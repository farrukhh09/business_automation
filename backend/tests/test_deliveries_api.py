"""Deliveries API — docs/architecture/04-api.md §9, 03-business-rules.md §7 (SPEC §45 "Доставка")."""

from app.core.time import business_today
from app.integrations.maps.types import GeoCandidate, GeocodeResult
from app.models.enums import DeliveryType


def _set_warehouse(client, admin_headers, lat="38.560000", lng="68.774000"):
    response = client.put(
        "/api/settings",
        json={"warehouse": {"name": "Кухня", "address": "ул. Складская, 1", "latitude": float(lat), "longitude": float(lng)}},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _delivery_order(make_order, status="CONFIRMED", **coords):
    # list_for_date/dispatch-sheet/optimize only ever look at VALID orders (03 §4, §7).
    return make_order(
        status=status,
        delivery_type=DeliveryType.DELIVERY,
        delivery=dict(
            address_raw="Худжанд, ул. Рудаки, 1",
            recipient_name="Получатель",
            recipient_phone="+992900000001",
            **coords,
        ),
    )


def test_list_deliveries_for_date(client, admin_headers, make_order):
    order = _delivery_order(make_order, latitude="38.560000", longitude="68.774000")
    response = client.get("/api/deliveries", params={"date": order.delivery_date.isoformat()}, headers=admin_headers)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert order.delivery.id in ids


def test_patch_delivery_updates_address_fields(client, admin_headers, make_order):
    order = _delivery_order(make_order)
    response = client.patch(
        f"/api/deliveries/{order.delivery.id}", json={"landmark": "рядом с рынком"}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["landmark"] == "рядом с рынком"


def test_patch_delivery_manual_fields_marks_dispatched(client, admin_headers, make_order):
    order = _delivery_order(make_order, latitude="38.560000", longitude="68.774000")
    response = client.patch(
        f"/api/deliveries/{order.delivery.id}",
        json={"external_id": "M-777", "courier_name": "Курьер"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["external_id"] == "M-777"
    assert body["status"] == "DISPATCHED"  # entering Maxim data without an explicit status (06 §4)


def test_patch_delivery_explicit_status_wins(client, admin_headers, make_order):
    order = _delivery_order(make_order, latitude="38.560000", longitude="68.774000")
    response = client.patch(
        f"/api/deliveries/{order.delivery.id}",
        json={"external_id": "M-1", "status": "DELIVERED"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "DELIVERED"


def test_geocode_endpoint_applies_the_provider_result(client, admin_headers, make_order, monkeypatch):
    order = _delivery_order(make_order)
    fake_result = GeocodeResult(
        status="OK",
        candidates=[GeoCandidate(formatted="Худжанд, ул. Рудаки, 1", lat=40.29, lng=69.62, precision="house")],
        provider="fake",
    )
    monkeypatch.setattr("app.services.geocoding_service.get_geocoder", lambda settings: type(
        "FakeGeocoder", (), {"name": "fake", "geocode": staticmethod(lambda query, **kw: fake_result)}
    )())
    response = client.post(f"/api/deliveries/{order.delivery.id}/geocode", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["geocode_status"] == "OK"
    assert body["latitude"] == 40.29


def test_select_candidate(client, admin_headers, make_order, db):
    order = _delivery_order(make_order)
    order.delivery.geocode_candidates = [
        {"formatted": "Вариант 1", "lat": 38.56, "lng": 68.78, "precision": "street"},
        {"formatted": "Вариант 2", "lat": 38.57, "lng": 68.79, "precision": "street"},
    ]
    db.commit()

    response = client.post(
        f"/api/deliveries/{order.delivery.id}/select-candidate", json={"index": 1}, headers=admin_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["latitude"] == 38.57
    assert body["geocode_status"] == "MANUAL"
    assert body["location_source"] == "OPERATOR"


def test_select_candidate_out_of_range_is_rejected(client, admin_headers, make_order):
    order = _delivery_order(make_order)
    response = client.post(
        f"/api/deliveries/{order.delivery.id}/select-candidate", json={"index": 5}, headers=admin_headers
    )
    assert response.status_code == 400


def test_location_link_can_be_reused_before_expiry(client, admin_headers, make_order):
    order = _delivery_order(make_order)
    first = client.post(f"/api/deliveries/{order.delivery.id}/location-link", headers=admin_headers)
    assert first.status_code == 200
    assert first.json()["url"].endswith(f"/l/{first.json()['url'].rsplit('/', 1)[-1]}")

    second = client.post(f"/api/deliveries/{order.delivery.id}/location-link", headers=admin_headers)
    assert second.json()["url"] == first.json()["url"]  # reused, not a new token


def test_dispatch_creates_manual_card_and_marks_awaiting(client, admin_headers, make_order):
    order = _delivery_order(make_order, latitude="38.560000", longitude="68.774000")
    response = client.post(f"/api/deliveries/{order.delivery.id}/dispatch", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["requires_operator"] is True
    assert body["provider"] == "MAXIM_MANUAL"
    assert f"Заказ №{order.id}" in body["copy_text"]
    assert body["delivery"]["status"] == "AWAITING_DISPATCH"


def test_dispatch_sheet_lists_deliveries_for_the_date(client, admin_headers, make_order):
    order = _delivery_order(make_order, latitude="38.560000", longitude="68.774000")
    response = client.get(
        "/api/deliveries/dispatch-sheet", params={"date": order.delivery_date.isoformat()}, headers=admin_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert any(stop["order_id"] == order.id for stop in body["stops"])
    assert f"Заказ №{order.id}" in body["text"]


def test_optimize_requires_warehouse_coordinates(client, admin_headers):
    response = client.post(
        "/api/deliveries/optimize", json={"date": business_today().isoformat()}, headers=admin_headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == "warehouse_not_configured"


def test_optimize_builds_a_route_and_get_routes_returns_it(client, admin_headers, make_order):
    _set_warehouse(client, admin_headers)
    order_a = _delivery_order(make_order, latitude="38.560000", longitude="68.774500")
    order_b = _delivery_order(make_order, latitude="38.561000", longitude="68.775000")
    day = order_a.delivery_date.isoformat()

    optimized = client.post("/api/deliveries/optimize", json={"date": day}, headers=admin_headers)
    assert optimized.status_code == 200, optimized.text
    body = optimized.json()
    assert body["distance_source"] == "haversine"  # OSRM_URL is empty by default
    stop_order_ids = {stop["order_id"] for stop in body["stops"]}
    assert stop_order_ids == {order_a.id, order_b.id}
    assert body["unlocated"] == []

    fetched = client.get("/api/deliveries/routes", params={"date": day}, headers=admin_headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_optimize_reports_unlocated_deliveries(client, admin_headers, make_order):
    _set_warehouse(client, admin_headers)
    located = _delivery_order(make_order, latitude="38.560000", longitude="68.774500")
    unlocated = _delivery_order(make_order)  # no coordinates
    day = located.delivery_date.isoformat()

    response = client.post("/api/deliveries/optimize", json={"date": day}, headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert [stop["order_id"] for stop in body["stops"]] == [located.id]
    assert [item["order_id"] for item in body["unlocated"]] == [unlocated.id]


def test_get_routes_without_a_plan_returns_null(client, admin_headers):
    response = client.get("/api/deliveries/routes", params={"date": business_today().isoformat()}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json() is None


def test_operator_can_manage_deliveries(client, operator_headers, make_order):
    order = _delivery_order(make_order)
    response = client.get(f"/api/deliveries/{order.delivery.id}", headers=operator_headers)
    assert response.status_code == 200
