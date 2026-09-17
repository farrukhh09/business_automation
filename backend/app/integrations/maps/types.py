"""Geocoding / routing value objects and protocols (docs/architecture/06-integrations.md §2).

``decide_status`` is the single auto-accept rule shared by every geocoder (06 §2, 03 §7):
a point is accepted automatically only when the provider returned **one** candidate, that
candidate has house/building precision and it lies inside the city bbox. Anything else needs
a human: several candidates or low precision → ``AMBIGUOUS`` (the bot offers the variants and
the map link), nothing at all → ``NOT_FOUND``, a provider failure → ``FAILED``.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.models.enums import GeocodeStatus

# Precision buckets (06 §2). Only ``house`` may be auto-accepted.
PRECISION_HOUSE = "house"
PRECISION_STREET = "street"
PRECISION_DISTRICT = "district"
PRECISION_CITY = "city"
PRECISION_OTHER = "other"
PRECISIONS: tuple[str, ...] = (
    PRECISION_HOUSE,
    PRECISION_STREET,
    PRECISION_DISTRICT,
    PRECISION_CITY,
    PRECISION_OTHER,
)

DEFAULT_COUNTRY_CODE = "tj"
MAX_CANDIDATES = 5

Bbox = tuple[float, float, float, float]  # (lat_min, lng_min, lat_max, lng_max)


@dataclass(frozen=True, slots=True)
class GeoCandidate:
    """One address variant returned by a geocoder (stored in ``deliveries.geocode_candidates``).

    ``is_poi`` marks a shop/amenity found *at* an address ("ТехМаркет, 12, улица Айни") — it carries
    the house coordinates but is not the address itself; not part of the stored JSON.
    """

    formatted: str
    lat: float
    lng: float
    precision: str = PRECISION_OTHER
    is_poi: bool = False

    def as_dict(self) -> dict[str, Any]:
        """JSON shape fixed by 02-data-model.md (``geocode_candidates``)."""
        return {"formatted": self.formatted, "lat": self.lat, "lng": self.lng, "precision": self.precision}


_PRECISION_RANK = {precision: index for index, precision in enumerate(PRECISIONS)}


def rank_candidates(candidates: Sequence[GeoCandidate]) -> list[GeoCandidate]:
    """Most precise first, addresses before points of interest; the provider order otherwise.

    A point of interest at a house that is also present as a plain address is dropped: "ТехМаркет,
    12, улица Айни" and "12, улица Айни" are one place, not two variants to choose from.
    """
    ordered = sorted(
        enumerate(candidates),
        key=lambda pair: (_PRECISION_RANK.get(pair[1].precision, len(PRECISIONS)), pair[1].is_poi, pair[0]),
    )
    result: list[GeoCandidate] = []
    has_plain_house = any(c.precision == PRECISION_HOUSE and not c.is_poi for c in candidates)
    for _, candidate in ordered:
        if candidate.is_poi and candidate.precision == PRECISION_HOUSE and has_plain_house:
            continue
        result.append(candidate)
    return result


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    status: GeocodeStatus
    candidates: list[GeoCandidate] = field(default_factory=list)
    provider: str = ""
    error: str | None = None

    @property
    def best(self) -> GeoCandidate | None:
        return self.candidates[0] if self.candidates else None

    def candidate_dicts(self) -> list[dict[str, Any]]:
        return [candidate.as_dict() for candidate in self.candidates]


@runtime_checkable
class Geocoder(Protocol):
    name: str

    def geocode(self, query: str, *, city: str, country_code: str = DEFAULT_COUNTRY_CODE) -> GeocodeResult: ...


@runtime_checkable
class DistanceMatrixProvider(Protocol):
    name: str

    def matrix(self, points: Sequence[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
        """``points`` are ``(lat, lng)``; returns ``(durations_s, distances_m)`` as square matrices."""
        ...


def inside_bbox(lat: float, lng: float, bbox: Bbox) -> bool:
    """Inclusive bounds; ``bbox`` is ``(lat_min, lng_min, lat_max, lng_max)`` as in ``CITY_BBOX``."""
    lat_min, lng_min, lat_max, lng_max = bbox
    return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max


def decide_status(candidates: Sequence[GeoCandidate], bbox: Bbox) -> GeocodeStatus:
    """Auto-accept rule shared by all providers (06 §2 "Правило автопринятия", 03 §7).

    Exactly one candidate, of ``house`` precision, inside the city bbox → ``OK``;
    no candidates → ``NOT_FOUND``; anything else (several variants, low precision, a point
    outside the city) → ``AMBIGUOUS``.
    """
    if not candidates:
        return GeocodeStatus.NOT_FOUND
    if len(candidates) == 1:
        only = candidates[0]
        if only.precision == PRECISION_HOUSE and inside_bbox(only.lat, only.lng, bbox):
            return GeocodeStatus.OK
    return GeocodeStatus.AMBIGUOUS


def failed_result(provider: str, error: str) -> GeocodeResult:
    """Provider error → ``FAILED`` (03 §7: the bot sends the map link, the operator checks)."""
    return GeocodeResult(status=GeocodeStatus.FAILED, candidates=[], provider=provider, error=error)


def as_float(value: Any) -> float | None:
    """Provider payloads carry coordinates as strings (Nominatim) or numbers (Google)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number
