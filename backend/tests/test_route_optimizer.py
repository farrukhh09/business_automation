"""``route_optimizer.py`` — pure solver (03-business-rules.md §7, SPEC §45 "Доставка")."""

import random
from datetime import time
from itertools import permutations

from app.services.route_optimizer import (
    ALGORITHM_BRUTE_FORCE,
    ALGORITHM_HEURISTIC,
    RoutePoint,
    RouteStopInput,
    optimize_route,
)


def _square_matrix(size: int, fill) -> list[list[float]]:
    return [[fill(i, j) for j in range(size)] for i in range(size)]


def test_empty_stop_list_returns_an_empty_plan() -> None:
    depot = RoutePoint(0.0, 0.0)
    result = optimize_route(depot, [], [[0.0]], [[0.0]], start_time=time(9, 0))
    assert result.stops == ()
    assert result.total_distance_m == 0
    assert result.total_duration_s == 0


def test_brute_force_visits_stops_in_distance_order_on_a_line() -> None:
    # Depot at 0, stops at 10, 20, 30 minutes away in a straight line: the optimum visits them in order.
    depot = RoutePoint(0.0, 0.0, name="Склад")
    stops = [RouteStopInput(key=100, latitude=0.0, longitude=0.0), RouteStopInput(key=200, latitude=0.0, longitude=0.0), RouteStopInput(key=300, latitude=0.0, longitude=0.0)]
    # Matrix indices: 0=depot, 1=stop100, 2=stop200, 3=stop300 (in that order in `stops`).
    minutes = {0: 0, 1: 30, 2: 10, 3: 20}  # deliberately out of order vs. `stops`
    size = 4
    durations = _square_matrix(size, lambda i, j: abs(minutes[i] - minutes[j]) * 60)
    distances = _square_matrix(size, lambda i, j: abs(minutes[i] - minutes[j]) * 1000)

    result = optimize_route(depot, stops, durations, distances, start_time=time(9, 0), service_time_min=0)
    assert result.algorithm == ALGORITHM_BRUTE_FORCE
    ordered_keys = [stop.key for stop in result.stops]
    assert ordered_keys == [200, 300, 100]  # nearest-first is optimal on a line
    assert result.total_duration_s == 30 * 60  # travel only, no backtracking


def test_heuristic_matches_brute_force_optimum_on_random_instances() -> None:
    random.seed(42)
    depot = RoutePoint(0.0, 0.0)
    for _ in range(5):
        n = 7
        points = [(random.uniform(-1, 1), random.uniform(-1, 1)) for _ in range(n)]
        stops = [RouteStopInput(key=i, latitude=lat, longitude=lng) for i, (lat, lng) in enumerate(points)]
        all_points = [(0.0, 0.0)] + points

        def dist(a, b):
            return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 * 100_000

        size = n + 1
        distances = [[dist(all_points[i], all_points[j]) for j in range(size)] for i in range(size)]
        durations = distances  # 1 m/s, so seconds == metres — keeps the comparison simple

        exact = optimize_route(depot, stops, durations, distances, start_time=time(9, 0), exact_limit=n)
        heuristic = optimize_route(depot, stops, durations, distances, start_time=time(9, 0), exact_limit=0)
        assert heuristic.algorithm == ALGORITHM_HEURISTIC
        assert exact.algorithm == ALGORITHM_BRUTE_FORCE
        # The heuristic must not beat the true optimum, and should be within a small margin of it.
        assert heuristic.cost >= exact.cost - 1e-6
        assert heuristic.cost <= exact.cost * 1.15 + 1e-6


def test_brute_force_is_exhaustive_reference() -> None:
    """Sanity-check the test helper itself: brute force beats every permutation's cost."""
    depot = RoutePoint(0.0, 0.0)
    n = 6
    random.seed(7)
    points = [(random.uniform(-1, 1), random.uniform(-1, 1)) for _ in range(n)]
    stops = [RouteStopInput(key=i, latitude=lat, longitude=lng) for i, (lat, lng) in enumerate(points)]
    all_points = [(0.0, 0.0)] + points
    size = n + 1
    matrix = [
        [((all_points[i][0] - all_points[j][0]) ** 2 + (all_points[i][1] - all_points[j][1]) ** 2) ** 0.5 for j in range(size)]
        for i in range(size)
    ]

    result = optimize_route(depot, stops, matrix, matrix, start_time=time(9, 0), exact_limit=n)

    def cost_of(order: tuple[int, ...]) -> float:
        total, previous = 0.0, 0
        for index in order:
            total += matrix[previous][index + 1]
            previous = index + 1
        return total

    best = min(cost_of(order) for order in permutations(range(n)))
    assert abs(result.cost - best) < 1e-9


def test_early_arrival_waits_without_penalty_and_late_arrival_is_penalised() -> None:
    depot = RoutePoint(0.0, 0.0)
    # One stop, 5 minutes away, desired at +30 minutes -> the courier must wait, no lateness.
    early = optimize_route(
        depot,
        [RouteStopInput(key=1, latitude=0.0, longitude=0.0, desired_time=time(9, 30))],
        [[0, 300], [300, 0]],
        [[0, 0], [0, 0]],
        start_time=time(9, 0),
        service_time_min=0,
        window_min=0,
    )
    assert early.stops[0].lateness_min == 0
    assert early.stops[0].eta == time(9, 30)

    # Same stop, desired at +2 minutes (before the courier can arrive) -> lateness is charged.
    late = optimize_route(
        depot,
        [RouteStopInput(key=1, latitude=0.0, longitude=0.0, desired_time=time(9, 2))],
        [[0, 300], [300, 0]],
        [[0, 0], [0, 0]],
        start_time=time(9, 0),
        service_time_min=0,
        window_min=0,
    )
    assert late.stops[0].lateness_min > 0


def test_deterministic_tie_break() -> None:
    """Equidistant stops must always resolve to the same order (smallest index wins ties)."""
    depot = RoutePoint(0.0, 0.0)
    stops = [RouteStopInput(key=1, latitude=0.0, longitude=0.0), RouteStopInput(key=2, latitude=0.0, longitude=0.0)]
    durations = [[0, 100, 100], [100, 0, 100], [100, 100, 0]]
    distances = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    first = optimize_route(depot, stops, durations, distances, start_time=time(9, 0))
    second = optimize_route(depot, stops, durations, distances, start_time=time(9, 0))
    assert [s.key for s in first.stops] == [s.key for s in second.stops] == [1, 2]
