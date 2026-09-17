"""Geocoders and distance-matrix providers (06 §2)."""

from app.integrations.maps.factory import get_distance_provider, get_geocoder
from app.integrations.maps.google import GoogleGeocoder
from app.integrations.maps.matrix import (
    ROAD_FACTOR,
    DistanceMatrixError,
    FallbackDistanceProvider,
    HaversineMatrix,
    OsrmMatrix,
    haversine_m,
)
from app.integrations.maps.nominatim import NominatimGeocoder
from app.integrations.maps.query import build_query
from app.integrations.maps.types import (
    PRECISION_CITY,
    PRECISION_DISTRICT,
    PRECISION_HOUSE,
    PRECISION_OTHER,
    PRECISION_STREET,
    DistanceMatrixProvider,
    GeoCandidate,
    Geocoder,
    GeocodeResult,
    decide_status,
    failed_result,
    inside_bbox,
)

__all__ = [
    "PRECISION_CITY",
    "PRECISION_DISTRICT",
    "PRECISION_HOUSE",
    "PRECISION_OTHER",
    "PRECISION_STREET",
    "ROAD_FACTOR",
    "DistanceMatrixError",
    "DistanceMatrixProvider",
    "FallbackDistanceProvider",
    "GeoCandidate",
    "GeocodeResult",
    "Geocoder",
    "GoogleGeocoder",
    "HaversineMatrix",
    "NominatimGeocoder",
    "OsrmMatrix",
    "build_query",
    "decide_status",
    "failed_result",
    "get_distance_provider",
    "get_geocoder",
    "haversine_m",
    "inside_bbox",
]
