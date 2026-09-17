"""``GeocodingService`` — 03-business-rules.md §7, 06-integrations.md §2."""

from app.integrations.maps.types import GeoCandidate, GeocodeResult, decide_status, failed_result
from app.models.enums import DeliveryType, GeocodeStatus, LocationSource
from app.services.geocoding_service import GeocodingService

KHUJAND_BBOX = (40.2623896, 69.5612523, 40.3316825, 69.6740681)


def test_decide_status_auto_accepts_only_a_single_house_candidate_inside_the_bbox() -> None:
    house = GeoCandidate(formatted="Дом", lat=40.29, lng=69.62, precision="house")
    street = GeoCandidate(formatted="Улица", lat=40.29, lng=69.62, precision="street")
    outside = GeoCandidate(formatted="Далеко", lat=41.5, lng=69.62, precision="house")

    assert decide_status([], KHUJAND_BBOX) == GeocodeStatus.NOT_FOUND
    assert decide_status([house], KHUJAND_BBOX) == GeocodeStatus.OK
    assert decide_status([street], KHUJAND_BBOX) == GeocodeStatus.AMBIGUOUS
    assert decide_status([outside], KHUJAND_BBOX) == GeocodeStatus.AMBIGUOUS
    assert decide_status([house, house], KHUJAND_BBOX) == GeocodeStatus.AMBIGUOUS


class _FakeGeocoder:
    name = "fake"

    def __init__(self, result: GeocodeResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def geocode(self, query: str, *, city: str) -> GeocodeResult:
        self.calls.append(query)
        return self.result


def _delivery_order(make_order, **delivery_fields):
    return make_order(
        delivery_type=DeliveryType.DELIVERY,
        delivery=dict(address_raw="Худжанд, ул. Рудаки, 1", **delivery_fields),
    )


def test_single_house_candidate_is_accepted(db, make_order):
    order = _delivery_order(make_order)
    result = GeocodeResult(
        status=GeocodeStatus.OK,
        candidates=[GeoCandidate(formatted="Худжанд, ул. Рудаки, 1", lat=40.29, lng=69.62, precision="house")],
        provider="fake",
    )
    GeocodingService(db, geocoder=_FakeGeocoder(result)).geocode_delivery(order.delivery)
    assert order.delivery.geocode_status == GeocodeStatus.OK
    assert order.delivery.location_source == LocationSource.GEOCODER
    assert order.delivery_latitude is not None  # order copy synced (03 §7)


def test_no_candidates_is_not_found(db, make_order):
    order = _delivery_order(make_order)
    GeocodingService(db, geocoder=_FakeGeocoder(failed_result("fake", "not_found"))).geocode_delivery(order.delivery)
    assert order.delivery.geocode_status in (GeocodeStatus.NOT_FOUND, GeocodeStatus.FAILED)


def test_multiple_candidates_are_ambiguous_and_stored(db, make_order):
    order = _delivery_order(make_order)
    result = GeocodeResult(
        status=GeocodeStatus.AMBIGUOUS,
        candidates=[
            GeoCandidate(formatted="Вариант 1", lat=40.29, lng=69.62, precision="street"),
            GeoCandidate(formatted="Вариант 2", lat=40.30, lng=69.63, precision="street"),
        ],
        provider="fake",
    )
    GeocodingService(db, geocoder=_FakeGeocoder(result)).geocode_delivery(order.delivery)
    assert order.delivery.geocode_status == GeocodeStatus.AMBIGUOUS
    assert len(order.delivery.geocode_candidates) == 2
    assert order.delivery.latitude is None  # nothing auto-applied


def test_manual_location_is_never_overwritten_automatically(db, make_order):
    order = _delivery_order(
        make_order, latitude="40.290000", longitude="69.620000", location_source=LocationSource.CUSTOMER_PIN
    )
    geocoder = _FakeGeocoder(
        GeocodeResult(
            status=GeocodeStatus.OK,
            candidates=[GeoCandidate(formatted="Другой адрес", lat=1.0, lng=1.0, precision="house")],
            provider="fake",
        )
    )
    GeocodingService(db, geocoder=geocoder).geocode_delivery(order.delivery)
    assert geocoder.calls == []  # skipped before even calling the provider
    assert float(order.delivery.latitude) == 40.29


def test_force_bypasses_the_manual_location_guard(db, make_order):
    order = _delivery_order(
        make_order, latitude="40.290000", longitude="69.620000", location_source=LocationSource.CUSTOMER_PIN
    )
    geocoder = _FakeGeocoder(
        GeocodeResult(
            status=GeocodeStatus.OK,
            candidates=[GeoCandidate(formatted="Новый адрес", lat=40.30, lng=69.63, precision="house")],
            provider="fake",
        )
    )
    GeocodingService(db, geocoder=geocoder).geocode_delivery(order.delivery, force=True)
    assert geocoder.calls  # the explicit staff action did call the provider
    assert float(order.delivery.latitude) == 40.30


def test_coordinates_outside_tajikistan_are_rejected_as_ambiguous(db, make_order):
    order = _delivery_order(make_order)
    result = GeocodeResult(
        status=GeocodeStatus.OK,
        candidates=[GeoCandidate(formatted="Где-то далеко", lat=55.75, lng=37.61, precision="house")],  # Moscow
        provider="fake",
    )
    GeocodingService(db, geocoder=_FakeGeocoder(result)).geocode_delivery(order.delivery)
    assert order.delivery.geocode_status == GeocodeStatus.AMBIGUOUS
    assert order.delivery.latitude is None
