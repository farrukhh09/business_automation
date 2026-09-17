"""Route solver (03-business-rules.md §7, docs/research/geo.md §4.5) — **pure functions only**.

No database, no HTTP, no clock: everything comes in as arguments, so the solver is deterministic and
cheap to test. Index ``0`` of both matrices is the depot (warehouse); stop ``i`` is index ``i + 1``.

Model (03 §7):

- open route: warehouse → stop → stop → … (the courier does not have to come back);
- cost = travel seconds + ``lateness_weight`` × seconds late beyond the delivery window
  (``delivery_time ± window_min``, 60 minutes by default);
- arriving before the window opens means waiting, which delays the following stops but costs nothing;
- every stop takes ``service_time_min`` minutes;
- ``n ≤ exact_limit`` (8) stops → exact brute force; more → nearest neighbour + 2-opt + Or-opt
  until no move improves the cost.

Ties are always broken by the smallest index, so the same input yields the same plan.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time
from itertools import permutations

SECONDS_PER_MINUTE = 60
SECONDS_PER_DAY = 24 * 3600

ALGORITHM_BRUTE_FORCE = "brute_force"
ALGORITHM_HEURISTIC = "nn_2opt_oropt"

# Up to this many stops the optimum is found by enumerating every order (8! = 40320).
EXACT_LIMIT = 8
# Weight of one late second relative to one travel second (03 §7 "штрафуется").
DEFAULT_LATENESS_WEIGHT = 3.0
DEFAULT_SERVICE_TIME_MIN = 5
DEFAULT_WINDOW_MIN = 60
# Longest segment Or-opt relocates.
OR_OPT_MAX_SEGMENT = 3
MAX_PASSES = 50


@dataclass(frozen=True, slots=True)
class RoutePoint:
    """Depot / warehouse: matrix index 0."""

    latitude: float
    longitude: float
    name: str = ""


@dataclass(frozen=True, slots=True)
class RouteStopInput:
    """One delivery to visit. ``key`` is the caller's id (delivery id) and is echoed back."""

    key: int
    latitude: float
    longitude: float
    desired_time: time | None = None


@dataclass(frozen=True, slots=True)
class PlannedStop:
    sequence: int  # 1..n
    index: int  # position in the input ``stops`` sequence
    key: int
    eta: time
    arrival_s: int  # seconds after the start when the courier arrives (before any waiting)
    eta_s: int  # seconds after the start when service begins (arrival + waiting)
    lateness_min: int
    distance_from_prev_m: int
    duration_from_prev_s: int


@dataclass(frozen=True, slots=True)
class RoutePlanResult:
    algorithm: str
    order: tuple[int, ...]  # indices into the input ``stops``
    stops: tuple[PlannedStop, ...]
    total_distance_m: int
    total_duration_s: int  # start → end of service at the last stop (travel + waiting + service)
    travel_duration_s: int
    total_lateness_min: int
    cost: float


def seconds_of(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def time_of(seconds: int) -> time:
    """Seconds since midnight → ``time`` rounded to the nearest minute (wraps at midnight)."""
    minutes = int((seconds + SECONDS_PER_MINUTE // 2) // SECONDS_PER_MINUTE)
    minutes %= SECONDS_PER_DAY // SECONDS_PER_MINUTE
    return time(minutes // 60, minutes % 60)


def _windows(stops: Sequence[RouteStopInput], window_s: int) -> list[tuple[int, int] | None]:
    """Absolute ``(earliest, latest)`` seconds since midnight per stop; ``None`` = no desired time."""
    result: list[tuple[int, int] | None] = []
    for stop in stops:
        if stop.desired_time is None:
            result.append(None)
            continue
        desired = seconds_of(stop.desired_time)
        result.append((desired - window_s, desired + window_s))
    return result


def _validate(matrix: Sequence[Sequence[float]], size: int, name: str) -> None:
    if len(matrix) != size or any(len(row) != size for row in matrix):
        raise ValueError(f"{name} must be a {size}x{size} matrix")


class _Evaluator:
    """Cost of a visiting order; shared by the exact and the heuristic search."""

    def __init__(
        self,
        durations: Sequence[Sequence[float]],
        windows: Sequence[tuple[int, int] | None],
        *,
        start_s: int,
        service_s: int,
        lateness_weight: float,
    ) -> None:
        self.durations = durations
        self.windows = windows
        self.start_s = start_s
        self.service_s = service_s
        self.lateness_weight = lateness_weight

    def cost(self, order: Sequence[int]) -> float:
        elapsed = 0.0
        travel = 0.0
        late = 0.0
        previous = 0
        for index in order:
            node = index + 1
            leg = self.durations[previous][node]
            travel += leg
            elapsed += leg
            window = self.windows[index]
            if window is not None:
                earliest, latest = window
                arrival = self.start_s + elapsed
                if arrival < earliest:
                    elapsed = earliest - self.start_s
                elif arrival > latest:
                    late += arrival - latest
            elapsed += self.service_s
            previous = node
        return travel + self.lateness_weight * late


def _nearest_neighbour(durations: Sequence[Sequence[float]], count: int) -> list[int]:
    """Greedy start: always the closest unvisited stop (ties → smallest index)."""
    remaining = set(range(count))
    order: list[int] = []
    current = 0
    while remaining:
        best = min(remaining, key=lambda index: (durations[current][index + 1], index))
        order.append(best)
        remaining.discard(best)
        current = best + 1
    return order


def _two_opt(order: list[int], evaluator: _Evaluator) -> list[int]:
    """Reverse a segment while it lowers the cost (open route: every position may move)."""
    best = order
    best_cost = evaluator.cost(best)
    improved = True
    passes = 0
    while improved and passes < MAX_PASSES:
        improved = False
        passes += 1
        for i in range(len(best) - 1):
            for k in range(i + 1, len(best)):
                candidate = best[:i] + best[i : k + 1][::-1] + best[k + 1 :]
                candidate_cost = evaluator.cost(candidate)
                if candidate_cost < best_cost - 1e-9:
                    best, best_cost, improved = candidate, candidate_cost, True
    return best


def _or_opt(order: list[int], evaluator: _Evaluator) -> list[int]:
    """Move a segment of 1..3 stops to another position while it lowers the cost."""
    best = order
    best_cost = evaluator.cost(best)
    improved = True
    passes = 0
    while improved and passes < MAX_PASSES:
        improved = False
        passes += 1
        for length in range(1, min(OR_OPT_MAX_SEGMENT, len(best)) + 1):
            for start in range(len(best) - length + 1):
                segment = best[start : start + length]
                rest = best[:start] + best[start + length :]
                for position in range(len(rest) + 1):
                    if position == start:
                        continue
                    candidate = rest[:position] + segment + rest[position:]
                    candidate_cost = evaluator.cost(candidate)
                    if candidate_cost < best_cost - 1e-9:
                        best, best_cost, improved = candidate, candidate_cost, True
    return best


def _brute_force(count: int, evaluator: _Evaluator) -> list[int]:
    best: tuple[int, ...] = tuple(range(count))
    best_cost = evaluator.cost(best)
    for candidate in permutations(range(count)):
        candidate_cost = evaluator.cost(candidate)
        if candidate_cost < best_cost - 1e-9:
            best, best_cost = candidate, candidate_cost
    return list(best)


def _build(
    order: Sequence[int],
    stops: Sequence[RouteStopInput],
    durations: Sequence[Sequence[float]],
    distances: Sequence[Sequence[float]],
    windows: Sequence[tuple[int, int] | None],
    *,
    start_s: int,
    service_s: int,
) -> tuple[list[PlannedStop], int, int, int, int]:
    """Turn a visiting order into stops with ETA; returns (stops, distance, duration, travel, lateness)."""
    planned: list[PlannedStop] = []
    elapsed = 0.0
    travel = 0.0
    total_distance = 0.0
    total_lateness = 0
    previous = 0
    for sequence, index in enumerate(order, start=1):
        node = index + 1
        leg_duration = durations[previous][node]
        leg_distance = distances[previous][node]
        travel += leg_duration
        elapsed += leg_duration
        arrival = elapsed
        lateness_s = 0.0
        window = windows[index]
        if window is not None:
            earliest, latest = window
            absolute = start_s + elapsed
            if absolute < earliest:
                elapsed = earliest - start_s
            elif absolute > latest:
                lateness_s = absolute - latest
        lateness_min = int(math.ceil(lateness_s / SECONDS_PER_MINUTE)) if lateness_s > 0 else 0
        total_lateness += lateness_min
        total_distance += leg_distance
        planned.append(
            PlannedStop(
                sequence=sequence,
                index=index,
                key=stops[index].key,
                eta=time_of(start_s + int(round(elapsed))),
                arrival_s=int(round(arrival)),
                eta_s=int(round(elapsed)),
                lateness_min=lateness_min,
                distance_from_prev_m=int(round(leg_distance)),
                duration_from_prev_s=int(round(leg_duration)),
            )
        )
        elapsed += service_s
        previous = node
    return planned, int(round(total_distance)), int(round(elapsed)), int(round(travel)), total_lateness


def optimize_route(
    depot: RoutePoint,
    stops: Sequence[RouteStopInput],
    durations_s: Sequence[Sequence[float]],
    distances_m: Sequence[Sequence[float]],
    *,
    start_time: time,
    service_time_min: int = DEFAULT_SERVICE_TIME_MIN,
    window_min: int = DEFAULT_WINDOW_MIN,
    lateness_weight: float = DEFAULT_LATENESS_WEIGHT,
    exact_limit: int = EXACT_LIMIT,
) -> RoutePlanResult:
    """Order the stops and compute ETA per stop (03 §7).

    ``durations_s`` / ``distances_m`` are ``(len(stops) + 1)`` square matrices whose index 0 is
    ``depot``. ``exact_limit`` is the largest number of stops still solved by brute force (tests
    pass ``0`` to force the heuristic and compare it with the optimum).
    """
    count = len(stops)
    size = count + 1
    _validate(durations_s, size, "durations_s")
    _validate(distances_m, size, "distances_m")

    service_s = max(int(service_time_min), 0) * SECONDS_PER_MINUTE
    window_s = max(int(window_min), 0) * SECONDS_PER_MINUTE
    start_s = seconds_of(start_time)
    windows = _windows(stops, window_s)

    if count == 0:
        return RoutePlanResult(
            algorithm=ALGORITHM_BRUTE_FORCE,
            order=(),
            stops=(),
            total_distance_m=0,
            total_duration_s=0,
            travel_duration_s=0,
            total_lateness_min=0,
            cost=0.0,
        )

    evaluator = _Evaluator(
        durations_s,
        windows,
        start_s=start_s,
        service_s=service_s,
        lateness_weight=float(lateness_weight),
    )
    if count <= exact_limit:
        order = _brute_force(count, evaluator)
        algorithm = ALGORITHM_BRUTE_FORCE
    else:
        order = _or_opt(_two_opt(_nearest_neighbour(durations_s, count), evaluator), evaluator)
        algorithm = ALGORITHM_HEURISTIC

    planned, distance, duration, travel, lateness = _build(
        order,
        stops,
        durations_s,
        distances_m,
        windows,
        start_s=start_s,
        service_s=service_s,
    )
    return RoutePlanResult(
        algorithm=algorithm,
        order=tuple(order),
        stops=tuple(planned),
        total_distance_m=distance,
        total_duration_s=duration,
        travel_duration_s=travel,
        total_lateness_min=lateness,
        cost=evaluator.cost(order),
    )
