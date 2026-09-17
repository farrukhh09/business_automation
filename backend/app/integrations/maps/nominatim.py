"""Nominatim geocoder (default provider) — 06 §2, docs/research/geo.md §2.5.

``GET {NOMINATIM_URL}/search?q=...&format=jsonv2&addressdetails=1&limit=5&countrycodes=tj
&accept-language=ru&viewbox=<west>,<north>,<east>,<south>&bounded=0`` with the mandatory
``User-Agent`` that identifies the application (osmfoundation usage policy).

The public server allows "an absolute maximum of 1 request per second", so every call goes through
a **process-wide** throttle (a module-level lock + the timestamp of the last request): several
Celery workers in one process, the API and the admin panel share it.

Failures never reach the caller as exceptions: a timeout, a network error, a non-2xx answer or a
malformed body become ``GeocodeResult(status=FAILED, error=...)`` (03 §7). The only exception is
``IntegrationNotConfiguredError`` when ``NOMINATIM_URL`` / ``NOMINATIM_USER_AGENT`` are empty.
"""

import logging
import threading
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
    rank_candidates,
)

logger = get_logger(__name__)

PROVIDER = "nominatim"
SEARCH_PATH = "/search"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
MIN_INTERVAL_SECONDS = 1.0
NOT_CONFIGURED_MESSAGE = "Геокодер Nominatim не настроен: не заданы NOMINATIM_URL и NOMINATIM_USER_AGENT"

# addresstype / type → precision (06 §2). Deliberately narrow: only an explicit building/house
# result may be auto-accepted, so "residential" (which Nominatim also uses for streets) is not here.
_HOUSE_TYPES = frozenset({"building", "house"})
_STREET_TYPES = frozenset({"road", "street", "pedestrian", "footway", "living_street"})
_DISTRICT_TYPES = frozenset({"suburb", "borough", "neighbourhood", "quarter", "city_district", "district"})
_CITY_TYPES = frozenset({"city", "town", "village", "municipality", "hamlet"})

_throttle_lock = threading.Lock()
_last_request_at: float = 0.0


def reset_throttle() -> None:
    """Forget the last request time (tests; also useful after a long idle period)."""
    global _last_request_at
    with _throttle_lock:
        _last_request_at = 0.0


def _throttle(min_interval: float) -> None:
    """Serialize requests process-wide, at most one per ``min_interval`` seconds."""
    global _last_request_at
    if min_interval <= 0:
        return
    with _throttle_lock:
        wait = _last_request_at + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def viewbox_param(bbox: Bbox) -> str:
    """``CITY_BBOX`` (lat_min, lng_min, lat_max, lng_max) → Nominatim ``x1,y1,x2,y2``."""
    lat_min, lng_min, lat_max, lng_max = bbox
    return f"{lng_min},{lat_max},{lng_max},{lat_min}"


#: OSM classes of points of interest (a shop or a café *at* an address, not the address itself).
_POI_CATEGORIES = frozenset({"shop", "amenity", "tourism", "office", "leisure", "craft", "healthcare"})
#: ``place_rank`` of house-level objects in Nominatim.
_HOUSE_RANK = 30


def is_poi(item: dict[str, Any]) -> bool:
    category = str(item.get("class") or item.get("category") or "").strip().lower()
    return category in _POI_CATEGORIES


def precision_of(item: dict[str, Any]) -> str:
    """``addresstype``/``type``/``category`` → precision bucket (06 §2).

    With ``addressdetails=1`` a result at a numbered house carries ``address.house_number``: a shop
    or a café at "12, улица Айни" has the coordinates of house 12 even though its ``addresstype`` is
    ``shop``, so such results count as house precision too (flagged ``is_poi`` by the caller).
    """
    address_type = str(item.get("addresstype") or "").strip().lower()
    osm_type = str(item.get("type") or "").strip().lower()
    category = str(item.get("class") or item.get("category") or "").strip().lower()
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    rank = item.get("place_rank")

    if address_type in _HOUSE_TYPES or osm_type == "house" or category == "building":
        return PRECISION_HOUSE
    if address.get("house_number") and isinstance(rank, int) and rank >= _HOUSE_RANK:
        return PRECISION_HOUSE
    if address_type in _STREET_TYPES or (category == "highway" and osm_type in _STREET_TYPES):
        return PRECISION_STREET
    if address_type in _DISTRICT_TYPES or osm_type in _DISTRICT_TYPES:
        return PRECISION_DISTRICT
    if address_type in _CITY_TYPES or osm_type in _CITY_TYPES:
        return PRECISION_CITY
    return PRECISION_OTHER


def parse_candidates(payload: Any) -> list[GeoCandidate]:
    """Nominatim ``jsonv2`` array → candidates (items without usable coordinates are dropped)."""
    if not isinstance(payload, list):
        return []
    candidates: list[GeoCandidate] = []
    for item in payload[:MAX_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        lat = as_float(item.get("lat"))
        lng = as_float(item.get("lon"))
        if lat is None or lng is None:
            continue
        formatted = str(item.get("display_name") or "").strip()
        candidates.append(
            GeoCandidate(formatted=formatted, lat=lat, lng=lng, precision=precision_of(item), is_poi=is_poi(item))
        )
    return candidates


class NominatimGeocoder:
    """``Geocoder`` implementation. ``http`` (optional) is used as is and never closed here."""

    name = PROVIDER

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        *,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
        min_interval: float = MIN_INTERVAL_SECONDS,
    ) -> None:
        base_url = (settings.NOMINATIM_URL or "").strip().rstrip("/")
        user_agent = (settings.NOMINATIM_USER_AGENT or "").strip()
        if not base_url or not user_agent:
            raise IntegrationNotConfiguredError(NOT_CONFIGURED_MESSAGE, provider=PROVIDER)
        self._base_url = base_url
        self._user_agent = user_agent
        self._bbox: Bbox = settings.city_bbox
        self._http = http
        self._timeout = timeout
        self._min_interval = min_interval

    def __repr__(self) -> str:
        return f"NominatimGeocoder(url={self._base_url!r})"

    def geocode(self, query: str, *, city: str = "", country_code: str = DEFAULT_COUNTRY_CODE) -> GeocodeResult:
        text = (query or "").strip()
        if not text:
            return failed_result(PROVIDER, "empty_query")

        params = {
            "q": text,
            "format": "jsonv2",
            "addressdetails": "1",
            "limit": str(MAX_CANDIDATES),
            "countrycodes": country_code,
            "accept-language": "ru",
            "viewbox": viewbox_param(self._bbox),
            "bounded": "0",
        }
        started = time.monotonic()
        log_event(logger, "geocode.request", provider=PROVIDER, query_chars=len(text))
        try:
            _throttle(self._min_interval)
            response = self._get(params)
        except httpx.TimeoutException:
            return self._fail("timeout", started)
        except httpx.HTTPError as exc:
            return self._fail("network", started, error=type(exc).__name__)

        if response.status_code == 403:
            # The public server rejects stock and placeholder User-Agents (anything with "example.com")
            # with 403 "Access denied": every address would look "not found" — make the cause visible.
            return self._fail("access_denied", started, status=403, error="user_agent_rejected")
        if not response.is_success:
            return self._fail("http_error", started, status=response.status_code)
        try:
            payload = response.json()
        except ValueError:
            return self._fail("invalid_response", started, status=response.status_code)

        candidates = rank_candidates(parse_candidates(payload))
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
        url = f"{self._base_url}{SEARCH_PATH}"
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        if self._http is not None:
            return self._http.get(url, params=params, headers=headers, timeout=self._timeout)
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            return client.get(url, params=params, headers=headers)

    def _fail(self, reason: str, started: float, *, status: int | None = None, error: str | None = None) -> GeocodeResult:
        log_event(
            logger,
            "geocode.error",
            level=logging.WARNING,
            provider=PROVIDER,
            reason=reason,
            status=status,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return failed_result(PROVIDER, reason)
