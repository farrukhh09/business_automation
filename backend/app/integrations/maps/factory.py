"""Maps adapter factories (06 §2): pick the implementation from settings.

``get_geocoder(settings)`` — ``GEOCODER_PROVIDER`` (``nominatim`` by default, ``google`` optional);
a provider that is not configured raises ``IntegrationNotConfiguredError`` and the caller degrades
gracefully (``geocode_status=FAILED`` + map link, 03 §7).

``get_distance_provider(settings, business_settings)`` — OSRM when ``OSRM_URL`` is set, wrapped so a
failing OSRM falls back to the haversine estimate (log ``routing.fallback``); haversine otherwise.
The courier speed comes from ``BusinessSettings.average_speed_kmh``.
"""

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import IntegrationNotConfiguredError
from app.integrations.maps.google import GoogleGeocoder
from app.integrations.maps.matrix import (
    DEFAULT_SPEED_KMH,
    FallbackDistanceProvider,
    HaversineMatrix,
    OsrmMatrix,
)
from app.integrations.maps.nominatim import NominatimGeocoder
from app.integrations.maps.types import DistanceMatrixProvider, Geocoder
from app.schemas.settings import BusinessSettings

GEOCODER_DISABLED = frozenset({"", "none", "disabled", "off"})


def get_geocoder(settings: Settings | None = None, *, http: httpx.Client | None = None) -> Geocoder:
    settings = settings or get_settings()
    provider = (settings.GEOCODER_PROVIDER or "").strip().lower()
    if provider in GEOCODER_DISABLED:
        raise IntegrationNotConfiguredError(
            "Геокодирование не настроено: не задан GEOCODER_PROVIDER", provider=provider or None
        )
    if provider == "nominatim":
        return NominatimGeocoder(settings, http=http)
    if provider == "google":
        return GoogleGeocoder(settings, http=http)
    raise IntegrationNotConfiguredError(f"Неизвестный провайдер геокодирования: {provider}", provider=provider)


def get_distance_provider(
    settings: Settings | None = None,
    business_settings: BusinessSettings | None = None,
    *,
    http: httpx.Client | None = None,
) -> DistanceMatrixProvider:
    settings = settings or get_settings()
    speed = business_settings.average_speed_kmh if business_settings else DEFAULT_SPEED_KMH
    haversine = HaversineMatrix(average_speed_kmh=speed)
    osrm_url = (settings.OSRM_URL or "").strip()
    if not osrm_url:
        return haversine
    return FallbackDistanceProvider(OsrmMatrix(osrm_url, http=http), haversine)
