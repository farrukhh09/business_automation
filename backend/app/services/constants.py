"""Shared domain constants (03-business-rules.md §1.1, §4, §7).

Status groups are ``frozenset`` so they support membership checks and set operations
(``VALID_ORDER_STATUSES | DRAFT_ORDER_STATUSES``); pass ``sorted(...)`` / ``tuple(...)`` to SQL ``IN``.
"""

from app.models.enums import OrderStatus

# "Учитываемые" orders: counted in revenue, production, customer stats (03 §4).
VALID_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.CONFIRMED,
        OrderStatus.PREPARING,
        OrderStatus.READY,
        OrderStatus.HANDED_TO_COURIER,
        OrderStatus.COMPLETED,
    }
)

# Drafts being assembled by the bot or an admin (03 §1.1).
DRAFT_ORDER_STATUSES: frozenset[OrderStatus] = frozenset({OrderStatus.NEW, OrderStatus.WAITING_CONFIRMATION})

# Everything that is not finished: all statuses except COMPLETED and CANCELLED.
ACTIVE_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(OrderStatus) - {
    OrderStatus.COMPLETED,
    OrderStatus.CANCELLED,
}

# Placed and not finished yet: active orders that are no longer drafts.
IN_PROGRESS_ORDER_STATUSES: frozenset[OrderStatus] = ACTIVE_ORDER_STATUSES - DRAFT_ORDER_STATUSES

# Bounding box of Tajikistan for coordinates entered by people (map pin, operator, courier):
# (lat_min, lng_min, lat_max, lng_max). The public map endpoint (04 §13) uses the same box.
TAJIKISTAN_BBOX: tuple[float, float, float, float] = (36.6, 67.3, 41.1, 75.2)


def sorted_statuses(statuses: frozenset[OrderStatus] | set[OrderStatus]) -> list[OrderStatus]:
    """Deterministic order for SQL ``IN (...)`` parameters."""
    return sorted(statuses, key=lambda status: status.value)


def is_inside_tajikistan(latitude: float, longitude: float) -> bool:
    lat_min, lng_min, lat_max, lng_max = TAJIKISTAN_BBOX
    return lat_min <= latitude <= lat_max and lng_min <= longitude <= lng_max
