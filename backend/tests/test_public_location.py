"""Public map-pin page — docs/architecture/04-api.md §13, 03-business-rules.md §7 (SPEC §24-27)."""

from datetime import timedelta

from app.core.time import now_utc
from app.models.enums import DeliveryType
from app.services.location_service import LocationService


def _delivery_order(make_order):
    return make_order(
        delivery_type=DeliveryType.DELIVERY,
        delivery=dict(address_raw="Душанбе, ул. Рудаки, 1"),
    )


def test_public_info_shows_business_name_and_written_address(client, make_order, db):
    order = _delivery_order(make_order)
    link = LocationService(db).create_link(order.delivery)

    response = client.get(f"/api/public/location/{link.token}")
    assert response.status_code == 200
    body = response.json()
    assert body["business_name"]  # default from BusinessSettings
    assert body["address_raw"] == "Душанбе, ул. Рудаки, 1"
    assert body["expired"] is False
    assert body["used"] is False
    assert body["latitude"] is None


def test_submit_saves_the_pin_and_syncs_the_order(client, make_order, db):
    order = _delivery_order(make_order)
    link = LocationService(db).create_link(order.delivery)

    response = client.post(f"/api/public/location/{link.token}", json={"latitude": 38.56, "longitude": 68.78})
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    db.refresh(order.delivery)
    assert float(order.delivery.latitude) == 38.56
    assert order.delivery.location_source.value == "CUSTOMER_PIN"
    assert order.delivery.geocode_status.value == "MANUAL"
    db.refresh(order)
    assert order.delivery_latitude is not None  # order copy synced (03 §7)


def test_submit_rejects_a_point_outside_tajikistan(client, make_order, db):
    order = _delivery_order(make_order)
    link = LocationService(db).create_link(order.delivery)
    response = client.post(f"/api/public/location/{link.token}", json={"latitude": 55.75, "longitude": 37.61})
    assert response.status_code == 422
    assert response.json()["code"] == "location_out_of_bounds"


def test_unknown_token_is_gone(client):
    response = client.get("/api/public/location/does-not-exist")
    assert response.status_code == 410


def test_expired_link_cannot_be_submitted(client, make_order, db):
    order = _delivery_order(make_order)
    link = LocationService(db).create_link(order.delivery)
    link.expires_at = now_utc() - timedelta(hours=1)
    db.commit()

    info = client.get(f"/api/public/location/{link.token}")
    assert info.status_code == 200
    assert info.json()["expired"] is True

    submit = client.post(f"/api/public/location/{link.token}", json={"latitude": 38.56, "longitude": 68.78})
    assert submit.status_code == 410
