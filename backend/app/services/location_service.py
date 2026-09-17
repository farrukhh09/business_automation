"""Customer map-pin link — "Отметьте точку на карте" (03-business-rules.md §7, 04-api.md §9, §13).

No authentication: the link identifies the delivery by an unguessable
``secrets.token_urlsafe(32)`` token with a 48h TTL (``LINK_TTL_HOURS``). Re-requesting a link before
the previous one expires reuses it. Submitting after expiry is a 410 ``GoneError``; submitting a
point outside Tajikistan goes through ``DeliveryService.apply_location`` and raises the same
``location_out_of_bounds`` error as everywhere else.
"""

import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import GoneError
from app.core.logging import get_logger, log_event
from app.core.time import now_utc
from app.integrations.maps.types import inside_bbox
from app.models.delivery import Delivery, LocationRequest
from app.models.enums import LocationSource
from app.repositories.deliveries import LocationRequestRepository
from app.schemas.public import LatLng, PublicLocationOut, PublicLocationSubmitOut
from app.services.constants import DRAFT_ORDER_STATUSES
from app.services.delivery_service import DeliveryService
from app.services.settings_service import SettingsService
from app.services.task_queue import TaskQueue, get_task_queue

logger = get_logger(__name__)

TOKEN_BYTES = 32
LINK_TTL_HOURS = 48
LINK_NOT_FOUND_DETAIL = "Ссылка не найдена или устарела"
LINK_EXPIRED_DETAIL = "Срок действия ссылки истёк — попросите менеджера прислать новую"


class LocationService:
    def __init__(self, db: Session, settings: Settings | None = None, queue: TaskQueue | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.repository = LocationRequestRepository(db)
        self.deliveries = DeliveryService(db)
        self._queue = queue

    # ------------------------------------------------------------------ staff side

    def create_link(self, delivery: Delivery) -> LocationRequest:
        """``POST /deliveries/{id}/location-link`` (04 §9). Reuses an unused, unexpired link."""
        existing = self.repository.latest_usable_for_delivery(delivery.id)
        if existing is not None:
            return existing
        request = LocationRequest(
            token=secrets.token_urlsafe(TOKEN_BYTES),
            delivery_id=delivery.id,
            expires_at=now_utc() + timedelta(hours=LINK_TTL_HOURS),
        )
        self.repository.add(request)
        self.db.commit()
        log_event(logger, "location_request.created", delivery_id=delivery.id, request_id=request.id)
        return request

    def link_url(self, request: LocationRequest) -> str:
        base = (self.settings.FRONTEND_PUBLIC_URL or "").rstrip("/")
        return f"{base}/l/{request.token}"

    # ------------------------------------------------------------------ public page (04 §13)

    def _get_or_raise(self, token: str) -> LocationRequest:
        request = self.repository.get_by_token(token)
        if request is None:
            raise GoneError(LINK_NOT_FOUND_DETAIL)
        return request

    def public_info(self, token: str) -> PublicLocationOut:
        """``GET /public/location/{token}``: only what the customer already wrote."""
        request = self._get_or_raise(token)
        delivery = request.delivery
        business_name = SettingsService(self.db).get().business_name
        lat, lng = self.settings.city_center
        return PublicLocationOut(
            business_name=business_name,
            address_raw=delivery.address_raw or None,
            latitude=float(delivery.latitude) if delivery.latitude is not None else None,
            longitude=float(delivery.longitude) if delivery.longitude is not None else None,
            city_center=LatLng(lat=lat, lng=lng),
            suggested=self._suggested_point(delivery),
            expired=request.expires_at <= now_utc(),
            used=request.used_at is not None,
        )

    def _suggested_point(self, delivery: Delivery) -> LatLng | None:
        """Where to centre the map before the customer taps: the geocoder's best guess (a street or a
        microdistrict centroid is close enough to start from), never pre-set as the pin itself."""
        for entry in delivery.geocode_candidates or []:
            try:
                lat, lng = float(entry["lat"]), float(entry["lng"])
            except (KeyError, TypeError, ValueError):
                continue
            if inside_bbox(lat, lng, self.settings.city_bbox):
                return LatLng(lat=lat, lng=lng)
        return None

    def submit(self, token: str, latitude: float, longitude: float) -> PublicLocationSubmitOut:
        """``POST /public/location/{token}``: save the pin (03 §7, ``location_source=CUSTOMER_PIN``).

        Re-submitting the same still-valid link is allowed (the customer may adjust the pin);
        an expired link is refused with 410 regardless of whether it was already used.
        """
        request = self._get_or_raise(token)
        if request.expires_at <= now_utc():
            raise GoneError(LINK_EXPIRED_DETAIL)

        delivery = self.deliveries.apply_location(request.delivery, latitude, longitude, LocationSource.CUSTOMER_PIN)
        request.latitude = delivery.latitude
        request.longitude = delivery.longitude
        request.used_at = now_utc()
        self.db.commit()
        log_event(
            logger,
            "location_request.submitted",
            request_id=request.id,
            delivery_id=delivery.id,
            order_id=delivery.order_id,
        )
        order = delivery.order
        if order is not None and order.conversation_id is not None and order.status in DRAFT_ORDER_STATUSES:
            # 03 §7: the bot re-checks the draft and sends the summary (or the next question).
            (self._queue or get_task_queue()).continue_after_location(order.id)
        return PublicLocationSubmitOut()
