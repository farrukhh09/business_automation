"""Distance / duration matrices for route planning (06 §2, docs/research/geo.md §3.3).

- ``OsrmMatrix`` — ``GET {OSRM_URL}/table/v1/driving/{lng,lat;...}?annotations=duration,distance``
  (self-hosted OSRM, compose profile ``routing``). A failure raises ``IntegrationError`` so the
  caller can fall back.
- ``HaversineMatrix`` — always available: great-circle distance × ``ROAD_FACTOR`` (1.3), duration =
  distance / ``average_speed_kmh``. This is a **documented estimate**, not a simulated provider:
  the answer carries ``distance_source="haversine"`` so the operator sees where numbers come from.
- ``FallbackDistanceProvider`` — OSRM first, haversine when it fails (log ``routing.fallback``).
  After a call, ``name`` is the provider that actually produced the matrix.
"""

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.exceptions import IntegrationError
from app.core.logging import get_logger, log_event
from app.integrations.maps.types import EARTH_RADIUS_M, DistanceMatrixProvider, haversine_m

logger = get_logger(__name__)

__all__ = [
    "EARTH_RADIUS_M",
    "HAVERSINE",
    "OSRM",
    "DistanceMatrixError",
    "FallbackDistanceProvider",
    "HaversineMatrix",
    "OsrmMatrix",
    "haversine_m",
]

HAVERSINE = "haversine"
OSRM = "osrm"

# Straight line → street network correction (06 §2).
ROAD_FACTOR = 1.3
DEFAULT_SPEED_KMH = 25.0
MIN_SPEED_KMH = 1.0

OSRM_TABLE_PATH = "/table/v1/driving/"
OSRM_DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
# osrm-routed --max-table-size defaults to 100 locations (geo.md §3.3).
OSRM_MAX_POINTS = 100


class DistanceMatrixError(IntegrationError):
    """The routing provider could not produce a matrix (the caller falls back to haversine)."""

    code = "routing_error"
    default_detail = "Сервис маршрутизации недоступен"


class HaversineMatrix:
    """``DistanceMatrixProvider`` that needs no external service."""

    name = HAVERSINE

    def __init__(self, average_speed_kmh: float = DEFAULT_SPEED_KMH, road_factor: float = ROAD_FACTOR) -> None:
        self.average_speed_kmh = max(float(average_speed_kmh or DEFAULT_SPEED_KMH), MIN_SPEED_KMH)
        self.road_factor = max(float(road_factor or ROAD_FACTOR), 1.0)

    def __repr__(self) -> str:
        return f"HaversineMatrix(speed_kmh={self.average_speed_kmh}, road_factor={self.road_factor})"

    @property
    def speed_ms(self) -> float:
        return self.average_speed_kmh * 1000.0 / 3600.0

    def matrix(self, points: Sequence[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
        size = len(points)
        distances = [[0.0] * size for _ in range(size)]
        durations = [[0.0] * size for _ in range(size)]
        speed = self.speed_ms
        for i in range(size):
            for j in range(i + 1, size):
                metres = haversine_m(points[i], points[j]) * self.road_factor
                seconds = metres / speed
                distances[i][j] = distances[j][i] = metres
                durations[i][j] = durations[j][i] = seconds
        return durations, distances


class OsrmMatrix:
    """``DistanceMatrixProvider`` backed by an OSRM ``table`` service."""

    name = OSRM

    def __init__(
        self,
        base_url: str,
        http: httpx.Client | None = None,
        *,
        timeout: httpx.Timeout | float = OSRM_DEFAULT_TIMEOUT,
    ) -> None:
        url = (base_url or "").strip().rstrip("/")
        if not url:
            raise ValueError("OsrmMatrix needs a base URL (OSRM_URL)")
        self._base_url = url
        self._http = http
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"OsrmMatrix(url={self._base_url!r})"

    def matrix(self, points: Sequence[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
        size = len(points)
        if size == 0:
            return [], []
        if size > OSRM_MAX_POINTS:
            raise DistanceMatrixError("Слишком много точек для расчёта маршрута", provider=OSRM, points=size)
        # OSRM takes coordinates as lng,lat.
        coordinates = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
        url = f"{self._base_url}{OSRM_TABLE_PATH}{coordinates}"
        try:
            response = self._get(url)
        except httpx.TimeoutException as exc:
            raise self._fail("timeout") from exc
        except httpx.HTTPError as exc:
            raise self._fail("network", error=type(exc).__name__) from exc

        if not response.is_success:
            raise self._fail("http_error", status=response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise self._fail("invalid_response", status=response.status_code) from exc
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            raise self._fail(
                "provider_error", provider_code=str(payload.get("code")) if isinstance(payload, dict) else None
            )

        durations = _square_matrix(payload.get("durations"), size)
        distances = _square_matrix(payload.get("distances"), size)
        if durations is None or distances is None:
            raise self._fail("invalid_matrix")
        log_event(logger, "routing.matrix", provider=OSRM, points=size)
        return durations, distances

    # ------------------------------------------------------------------ internals

    def _get(self, url: str) -> httpx.Response:
        params = {"annotations": "duration,distance"}
        if self._http is not None:
            return self._http.get(url, params=params, timeout=self._timeout)
        with httpx.Client(timeout=self._timeout) as client:
            return client.get(url, params=params)

    def _fail(
        self,
        reason: str,
        *,
        status: int | None = None,
        error: str | None = None,
        provider_code: str | None = None,
    ) -> DistanceMatrixError:
        log_event(
            logger,
            "routing.error",
            level=logging.WARNING,
            provider=OSRM,
            reason=reason,
            status=status,
            error=error,
            provider_code=provider_code,
        )
        return DistanceMatrixError(provider=OSRM, reason=reason, status=status)


def _square_matrix(value: Any, size: int) -> list[list[float]] | None:
    """Validate an OSRM matrix; ``null`` cells (unreachable) become ``0``."""
    if not isinstance(value, list) or len(value) != size:
        return None
    matrix: list[list[float]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != size:
            return None
        cells: list[float] = []
        for cell in row:
            if isinstance(cell, bool) or not isinstance(cell, (int, float)):
                cells.append(0.0)
            else:
                cells.append(float(cell))
        matrix.append(cells)
    return matrix


class FallbackDistanceProvider:
    """Primary provider with a local fallback (06 §2: OSRM error → haversine, log ``routing.fallback``)."""

    def __init__(self, primary: DistanceMatrixProvider, fallback: DistanceMatrixProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name

    def __repr__(self) -> str:
        return f"FallbackDistanceProvider(primary={self.primary.name!r}, fallback={self.fallback.name!r})"

    def matrix(self, points: Sequence[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
        try:
            result = self.primary.matrix(points)
        except IntegrationError as exc:
            log_event(
                logger,
                "routing.fallback",
                level=logging.WARNING,
                provider=self.primary.name,
                fallback=self.fallback.name,
                error=type(exc).__name__,
                points=len(points),
            )
            self.name = self.fallback.name
            return self.fallback.matrix(points)
        self.name = self.primary.name
        return result
