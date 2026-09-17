"""Building the query string sent to a geocoder (06 §2, 03 §7).

``Душанбе, Сино, 82 мкр, дом 5`` — city first, then district, microdistrict/street and house.
Apartment, entrance and floor are **never** sent: they cannot improve the coordinates and are
personal data. Without structured fields the customer's own text (``address_raw``) is used, with
the city prefixed when it is not mentioned there.
"""

import re

# Parts that refine an address inside a building and must not reach the geocoder.
EXCLUDED_FIELDS: tuple[str, ...] = ("apartment", "entrance", "floor")

_SPACES_RE = re.compile(r"\s+")


def _clean(value: str | None) -> str:
    return _SPACES_RE.sub(" ", value.strip()) if value else ""


def _mentions_city(text: str, city: str) -> bool:
    if not city:
        return False
    return city.casefold() in text.casefold()


def build_query(
    city: str,
    *,
    district: str | None = None,
    microdistrict: str | None = None,
    street: str | None = None,
    house: str | None = None,
    address_raw: str | None = None,
) -> str:
    """Geocoder query; empty string when there is nothing to geocode."""
    city_part = _clean(city)
    parts = [_clean(part) for part in (district, microdistrict, street, house)]
    structured = [part for part in parts if part]
    if structured:
        return ", ".join(([city_part] if city_part else []) + structured)

    raw = _clean(address_raw)
    if not raw:
        return ""
    if city_part and not _mentions_city(raw, city_part):
        return f"{city_part}, {raw}"
    return raw
