"""Geocoding of delivery addresses (03-business-rules.md §7, 06-integrations.md §2).

The service owns the *decision*, the adapter only talks to the provider:

- a point set by a person (``CUSTOMER_PIN`` / ``OPERATOR`` / ``COURIER``) is never overwritten —
  ``GEOCODER`` is the least trusted source (03 §7), so such a delivery is skipped entirely;
- the customer's text is parsed into parts (``app.integrations.maps.address``) that are stored on the
  delivery for the courier, and geocoded through a **ladder** of queries: street + house, then the
  street alone; microdistrict + house, then the microdistrict alone; a landmark. The first query with
  usable candidates wins — OpenStreetMap knows Dushanbe's streets and microdistricts far better than
  its house numbers, so an address is rarely "not found" at all, only "not found to the house";
- candidates that contradict the query are dropped (a geocoder asked for the 18th microdistrict must
  not answer with the 91st), points of interest at a house are folded into that house; a point outside
  the city bbox is kept for the operator but never auto-accepted;
- exactly one house-precision candidate → ``OK`` and the coordinates are applied with
  ``location_source=GEOCODER``; several houses or only a street/microdistrict centroid → ``AMBIGUOUS``
  (candidates are stored so the bot can offer them and centre the map link on them); nothing at all →
  ``NOT_FOUND``; provider error or provider not configured → ``FAILED``. In every one of these the
  order flow continues (03 §1.3: the point is asked for, never a blocker), the map link is offered
  and the operator sees the delivery without coordinates. Geocoding never raises.
"""

import logging

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import BusinessRuleError, IntegrationError, IntegrationNotConfiguredError
from app.core.logging import get_logger, log_event
from app.integrations.maps.address import (
    LEVEL_HOUSE,
    AddressQuery,
    ParsedAddress,
    candidate_matches,
    geocode_queries,
    parse_address,
)
from app.integrations.maps.factory import get_geocoder
from app.integrations.maps.types import (
    PRECISION_HOUSE,
    Geocoder,
    GeocodeResult,
    decide_status,
    failed_result,
    rank_candidates,
)
from app.models.delivery import Delivery
from app.models.enums import GeocodeStatus, LocationSource
from app.services.delivery_service import DeliveryService

logger = get_logger(__name__)

SKIP_MANUAL = "manual_location"
SKIP_NO_ADDRESS = "no_address"

__all__ = ["GeocodingService", "SKIP_MANUAL", "SKIP_NO_ADDRESS"]


class GeocodingService:
    def __init__(self, db: Session, geocoder: Geocoder | None = None, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.deliveries = DeliveryService(db)
        self._geocoder = geocoder

    # ------------------------------------------------------------------ provider

    @property
    def geocoder(self) -> Geocoder:
        """Resolved lazily so a missing provider only matters when geocoding actually happens."""
        if self._geocoder is None:
            self._geocoder = get_geocoder(self.settings)
        return self._geocoder

    def parsed_address(self, delivery: Delivery) -> ParsedAddress:
        """Structured parts of the delivery address.

        Parts already stored (by staff in the admin panel, or by an earlier run) are used as they are;
        otherwise the customer's text is parsed and the parts are stored for the courier.
        """
        raw = (delivery.address_raw or "").strip()
        if any((delivery.street, delivery.microdistrict, delivery.house, delivery.district)):
            return ParsedAddress(
                raw=raw,
                district=delivery.district,
                microdistrict=delivery.microdistrict,
                street=delivery.street,
                house=delivery.house,
                landmark=delivery.landmark,
            )
        parsed = parse_address(raw)
        for field, value in parsed.delivery_fields().items():
            if not getattr(delivery, field):
                setattr(delivery, field, value)
        return parsed

    def queries_for(self, delivery: Delivery) -> list[AddressQuery]:
        """The geocoder query ladder for a delivery (06 §2); empty when there is nothing to geocode."""
        if not (delivery.address_raw or "").strip():
            return []
        return geocode_queries(self.parsed_address(delivery), delivery.city or self.settings.CITY_NAME)

    def query_for(self, delivery: Delivery) -> str:
        """The most precise query of the ladder (kept for the admin panel / logs)."""
        queries = self.queries_for(delivery)
        return queries[0].text if queries else ""

    # ------------------------------------------------------------------ geocoding

    def geocode_delivery(self, delivery: Delivery, force: bool = False) -> Delivery:
        """Geocode one delivery and store the outcome (commits).

        ``force=True`` is an explicit staff action (``POST /deliveries/{id}/geocode``) and may
        overwrite even a manually set point; automatic geocoding never does (03 §7).
        """
        if not force and DeliveryService.has_manual_location(delivery):
            log_event(
                logger,
                "delivery.geocode_skipped",
                delivery_id=delivery.id,
                order_id=delivery.order_id,
                reason=SKIP_MANUAL,
                location_source=delivery.location_source.value if delivery.location_source else None,
            )
            return delivery

        queries = self.queries_for(delivery)
        if not queries:
            log_event(
                logger,
                "delivery.geocode_skipped",
                delivery_id=delivery.id,
                order_id=delivery.order_id,
                reason=SKIP_NO_ADDRESS,
            )
            return delivery

        result, query = self._geocode_ladder(queries)
        self._apply(delivery, result)
        self.db.commit()
        log_event(
            logger,
            "delivery.geocoded",
            delivery_id=delivery.id,
            order_id=delivery.order_id,
            provider=result.provider,
            status=delivery.geocode_status.value,
            candidates=len(result.candidates),
            ladder_level=query.level if query is not None else None,
            queries=len(queries),
            error=result.error,
        )
        return delivery

    def _geocode_ladder(self, queries: list[AddressQuery]) -> tuple[GeocodeResult, AddressQuery | None]:
        """Run the ladder until a query yields usable candidates (or the provider fails)."""
        bbox = self.settings.city_bbox
        last: GeocodeResult | None = None
        last_query: AddressQuery | None = None
        for query in queries:
            result = self._geocode(query.text)
            if result.status == GeocodeStatus.FAILED:
                return result, query
            # Candidates that contradict the query are dropped; a point outside the city bbox is kept
            # (a suburb delivery is possible) but never auto-accepted — ``decide_status`` handles that.
            candidates = rank_candidates(
                [candidate for candidate in result.candidates if candidate_matches(query, candidate.formatted)]
            )
            houses = [candidate for candidate in candidates if candidate.precision == PRECISION_HOUSE]
            if len(houses) == 1 and query.level == LEVEL_HOUSE:
                # One house among streets/districts ("ТехМаркет, 12, улица Айни" next to "улица Айни"):
                # the coarser hits are the same place, not alternatives to choose from.
                candidates = houses
            filtered = GeocodeResult(
                status=decide_status(candidates, bbox), candidates=candidates, provider=result.provider
            )
            if candidates:
                return filtered, query
            last, last_query = filtered, query
        return last or failed_result(self.settings.GEOCODER_PROVIDER, "no_queries"), last_query

    def _geocode(self, query: str) -> GeocodeResult:
        """Adapter call; every failure becomes a ``FAILED`` result (03 §7)."""
        provider = self.settings.GEOCODER_PROVIDER
        try:
            geocoder = self.geocoder
            provider = geocoder.name
            return geocoder.geocode(query, city=self.settings.CITY_NAME)
        except IntegrationNotConfiguredError as exc:
            log_event(
                logger,
                "delivery.geocoder_not_configured",
                level=logging.WARNING,
                provider=provider,
                code=exc.code,
            )
            return failed_result(provider, "not_configured")
        except IntegrationError as exc:  # defensive: adapters return FAILED instead of raising
            log_event(logger, "geocode.error", level=logging.WARNING, provider=provider, reason=type(exc).__name__)
            return failed_result(provider, "integration_error")

    def _apply(self, delivery: Delivery, result: GeocodeResult) -> None:
        delivery.geocode_provider = result.provider or None
        delivery.geocode_candidates = result.candidate_dicts()
        best = result.best
        if result.status == GeocodeStatus.OK and best is not None:
            try:
                self.deliveries.apply_location(delivery, best.lat, best.lng, LocationSource.GEOCODER, best.formatted)
                return
            except BusinessRuleError as exc:  # coordinates outside Tajikistan — never auto-accept
                log_event(
                    logger,
                    "delivery.geocode_rejected",
                    level=logging.WARNING,
                    delivery_id=delivery.id,
                    provider=result.provider,
                    code=exc.code,
                )
                delivery.geocode_status = GeocodeStatus.AMBIGUOUS
                return
        delivery.geocode_status = result.status
        self.db.flush()
