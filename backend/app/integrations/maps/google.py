"""Google Geocoding API v3 adapter (optional provider) — 06 §2, docs/research/geo.md §2.2.

``GET https://maps.googleapis.com/maps/api/geocode/json?address=...&components=country:TJ
&bounds=<lat_min>,<lng_min>|<lat_max>,<lng_max>&language=ru&key=...``

Status mapping (06 §2):

| provider ``status``                                    | result |
|---|---|
| ``ZERO_RESULTS``                                       | ``NOT_FOUND`` |
| ``OK``, one result, no ``partial_match``, ``location_type`` ROOFTOP/RANGE_INTERPOLATED, inside bbox | ``OK`` |
| ``OK`` otherwise                                       | ``AMBIGUOUS`` |
| ``OVER_QUERY_LIMIT`` / ``REQUEST_DENIED`` / ``INVALID_REQUEST`` / anything else | ``FAILED`` |

The last two rows are produced by ``decide_status``: a result that must not be auto-accepted
(partial match or a coarse ``location_type``) never gets ``house`` precision.

**Licence** (Service Specific Terms): Google coordinates may be cached for at most 30 days and must
not be shown on a non-Google map, so with this provider ``location_source=GEOCODER`` is a hint —
the trusted point is still the customer's pin or the operator's (03 §7).
"""

import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import IntegrationNotConfiguredError
from app.core.logging import get_logger, log_event
from app.integrations.maps.types import (
    DEFAULT_COUNTRY_CODE,
    MAX_CANDIDATES,
    PRECISION_CITY,
    PRECISION_DISTRICT,
    PRECISION_HOUSE,
    PRECISION_OTHER,
    PRECISION_STREET,
    Bbox,
    GeoCandidate,
    GeocodeResult,
    as_float,
    decide_status,
    failed_result,
)
from app.models.enums import GeocodeStatus

logger = get_logger(__name__)

PROVIDER = "google"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
NOT_CONFIGURED_MESSAGE = "Геокодер Google не настроен: не задан MAPS_API_KEY"

STATUS_OK = "OK"
STATUS_ZERO_RESULTS = "ZERO_RESULTS"
# Never retried (docs/research/geo.md §5): configuration or quota problems.
FATAL_STATUSES = frozenset({"OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT", "REQUEST_DENIED", "INVALID_REQUEST"})

# geometry.location_type → precision. Only the first two may be auto-accepted (geo.md §5).
_EXACT_LOCATION_TYPES = frozenset({"ROOFTOP", "RANGE_INTERPOLATED"})
_STREET_TYPES = frozenset({"route", "street_address", "intersection"})
_DISTRICT_TYPES = frozenset({"sublocality", "sublocality_level_1", "neighborhood"})
_CITY_TYPES = frozenset({"locality", "administrative_area_level_1", "administrative_area_level_2"})


def bounds_param(bbox: Bbox) -> str:
    """``CITY_BBOX`` → ``"southwest|northeast"`` as ``lat,lng|lat,lng`` (06 §2)."""
    lat_min, lng_min, lat_max, lng_max = bbox
    return f"{lat_min},{lng_min}|{lat_max},{lng_max}"


def precision_of(result: dict[str, Any]) -> str:
    """``location_type`` + ``types`` → precision; ``partial_match`` can never be a house (geo.md §5)."""
    geometry = result.get("geometry")
    location_type = ""
    if isinstance(geometry, dict):
        location_type = str(geometry.get("location_type") or "").strip().upper()
    types = {str(item).strip().lower() for item in result.get("types") or [] if isinstance(item, str)}

    if location_type in _EXACT_LOCATION_TYPES and not result.get("partial_match"):
        return PRECISION_HOUSE
    if types & _STREET_TYPES:
        return PRECISION_STREET
    if types & _DISTRICT_TYPES:
        return PRECISION_DISTRICT
    if types & _CITY_TYPES:
        return PRECISION_CITY
    return PRECISION_OTHER


def parse_candidates(payload: dict[str, Any]) -> list[GeoCandidate]:
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    candidates: list[GeoCandidate] = []
    for result in results[:MAX_CANDIDATES]:
        if not isinstance(result, dict):
            continue
        geometry = result.get("geometry")
        location = geometry.get("location") if isinstance(geometry, dict) else None
        if not isinstance(location, dict):
            continue
        lat = as_float(location.get("lat"))
        lng = as_float(location.get("lng"))
        if lat is None or lng is None:
            continue
        formatted = str(result.get("formatted_address") or "").strip()
        candidates.append(GeoCandidate(formatted=formatted, lat=lat, lng=lng, precision=precision_of(result)))
    return candidates


class GoogleGeocoder:
    """``Geocoder`` implementation. ``http`` (optional) is used as is and never closed here."""

    name = PROVIDER

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        *,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
    ) -> None:
        api_key = (settings.MAPS_API_KEY or "").strip()
        if not api_key:
            raise IntegrationNotConfiguredError(NOT_CONFIGURED_MESSAGE, provider=PROVIDER)
        self._api_key = api_key
        self._bbox: Bbox = settings.city_bbox
        self._http = http
        self._timeout = timeout

    def __repr__(self) -> str:
        return "GoogleGeocoder(api=geocode/json)"

    def geocode(self, query: str, *, city: str = "", country_code: str = DEFAULT_COUNTRY_CODE) -> GeocodeResult:
        text = (query or "").strip()
        if not text:
            return failed_result(PROVIDER, "empty_query")

        params = {
            "address": text,
            "components": f"country:{country_code.upper()}",
            "bounds": bounds_param(self._bbox),
            "language": "ru",
            "key": self._api_key,
        }
        started = time.monotonic()
        log_event(logger, "geocode.request", provider=PROVIDER, query_chars=len(text))
        try:
            response = self._get(params)
        except httpx.TimeoutException:
            return self._fail("timeout", started)
        except httpx.HTTPError as exc:
            return self._fail("network", started, error=type(exc).__name__)

        if not response.is_success:
            return self._fail("http_error", started, status=response.status_code)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return self._fail("invalid_response", started, status=response.status_code)

        provider_status = str(payload.get("status") or "").strip().upper()
        if provider_status == STATUS_ZERO_RESULTS:
            log_event(logger, "geocode.response", provider=PROVIDER, status=GeocodeStatus.NOT_FOUND.value, candidates=0)
            return GeocodeResult(status=GeocodeStatus.NOT_FOUND, candidates=[], provider=PROVIDER)
        if provider_status != STATUS_OK:
            return self._fail(
                "provider_error" if provider_status in FATAL_STATUSES else "unexpected_status",
                started,
                status=response.status_code,
                provider_status=provider_status or None,
            )

        candidates = parse_candidates(payload)
        status = decide_status(candidates, self._bbox)
        log_event(
            logger,
            "geocode.response",
            provider=PROVIDER,
            status=status.value,
            candidates=len(candidates),
            precision=candidates[0].precision if candidates else None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return GeocodeResult(status=status, candidates=candidates, provider=PROVIDER)

    # ------------------------------------------------------------------ internals

    def _get(self, params: dict[str, str]) -> httpx.Response:
        # follow_redirects stays off: the API key is a query parameter and must not be resent
        # to a redirect target.
        if self._http is not None:
            return self._http.get(GOOGLE_GEOCODE_URL, params=params, timeout=self._timeout, follow_redirects=False)
        with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
            return client.get(GOOGLE_GEOCODE_URL, params=params)

    def _fail(
        self,
        reason: str,
        started: float,
        *,
        status: int | None = None,
        error: str | None = None,
        provider_status: str | None = None,
    ) -> GeocodeResult:
        log_event(
            logger,
            "geocode.error",
            level=logging.WARNING,
            provider=PROVIDER,
            reason=reason,
            status=status,
            provider_status=provider_status,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return failed_result(PROVIDER, provider_status or reason)
