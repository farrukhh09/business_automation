"""Khujand address parsing and the geocoder query ladder (06-integrations.md §2, docs/research/geo.md §2.1).

Customers write addresses the way they say them: "18 мкр дом 7 кв 6", "Себзор, 82-й микрорайон, 5",
"ул. Айни 12, подъезд 2", "кӯчаи Рӯдакӣ 10", "возле рынка Корвон". A geocoder wants
"Худжанд, улица Айни, 12" — and OpenStreetMap knows Khujand's microdistricts and streets far better
than its house numbers. So instead of one query with the whole text:

1. :func:`parse_address` splits the text into structured parts (district, microdistrict number,
   street with a normalized type word, house, apartment/entrance/floor, landmark). Apartment,
   entrance and floor are kept for the courier and never sent to a geocoder (personal data, and
   they cannot improve coordinates).
2. :func:`geocode_queries` builds a ladder of queries from the most to the least precise: street +
   house → street only; microdistrict + house → microdistrict only; landmark; the raw text as the
   last resort. The caller stops at the first query that yields usable candidates.
3. :func:`candidate_matches` rejects a geocoder's fuzzy guesses: asked for the 18th microdistrict,
   Nominatim happily returns the 91st, the 112th and the 11th — those are not the address.

Tajik address words are mapped to their Russian counterparts (кӯча → улица, хиёбон → проспект,
ноҳия → район), Tajik letters are folded, so "кӯчаи Рӯдакӣ" and "улица Рудаки" are the same query.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.text_normalize import normalize_fold, tajik_fold

__all__ = [
    "MAX_QUERIES",
    "AddressQuery",
    "ParsedAddress",
    "candidate_matches",
    "geocode_queries",
    "parse_address",
]

#: How many geocoder calls one address may cost (the public Nominatim allows one per second).
MAX_QUERIES = 3

LEVEL_HOUSE = "house"
LEVEL_STREET = "street"
LEVEL_MICRODISTRICT = "microdistrict"
LEVEL_DISTRICT = "district"
LEVEL_LANDMARK = "landmark"
LEVEL_RAW = "raw"

# Khujand has no formal city-district split like Dushanbe's; customers instead name historical
# neighbourhoods/mahallas. Folded lower-case spelling → canonical Russian name.
_DISTRICTS: dict[str, str] = {
    "себзор": "Себзор",
    "пахтакор": "Пахтакор",
    "разок": "Разок",
    "раззок": "Разок",
    "масчиди сурх": "Масчиди Сурх",
    "дачаи ветеран": "Дачаи Ветеран",
}
_DISTRICT_RE = re.compile(
    r"(?:\b(?:район|р-н|р-он|нохия|нохияи)\s+)?"
    r"\b(себзор|пахтакор|раз{1,2}ок|масчиди сурх|дачаи ветеран)\b",
    re.IGNORECASE,
)

_CITY_RE = re.compile(r"\b(?:г\.?|ш\.?|шахри|город)?\s*(?:худжанд|хучанд)\b[,\s]*", re.IGNORECASE)

_APARTMENT_RE = re.compile(r"\b(?:кв\.?|квартира|квартире|хучра(?:и)?|утоки)\s*№?\s*(\d+\s*[а-яa-z]?)\b", re.IGNORECASE)
_ENTRANCE_RE = re.compile(r"\b(?:подъезд|под\.|даромадгох(?:и)?)\s*№?\s*(\d+)\b", re.IGNORECASE)
_FLOOR_RE = re.compile(
    r"\b(?:(\d+)\s*(?:-?й\s*)?(?:этаж|эт\.)|(?:этаж|эт\.|ошена(?:и)?|кабат(?:и)?)\s*(\d+))\b", re.IGNORECASE
)
_LANDMARK_RE = re.compile(
    r"\b(?:ориентир[а-я]*|рядом с[о]?|возле|напротив|около|недалеко от|за|наздики|назди|дар назди|ру ба руи)\b"
    r"\s*[:\-–—]?\s*(.+)$",
    re.IGNORECASE,
)
_MICRODISTRICT_RE = re.compile(
    r"(?:\b(\d{1,3})\s*-?\s*(?:й|ый|ой|и|ум|юм)?\s*(?:мкр\.?|мкрн\.?|мкр-н|м/р|микр\.?|микрорайон[а-я]*|микрорайони|махалла)\b"
    r"|\b(?:мкр\.?|мкрн\.?|мкр-н|м/р|микр\.?|микрорайон[а-я]*|микрорайони)\s*№?\s*(\d{1,3})\b)",
    re.IGNORECASE,
)
_HOUSE_RE = re.compile(
    r"\b(?:дом|д\.|хонаи|хона|уй)\s*№?\s*(\d+\s*[а-яa-z]?(?:\s*/\s*\d+)?(?:\s*(?:корп\.?|корпус|к\.)\s*\d+)?)",
    re.IGNORECASE,
)
_STREET_TYPES: dict[str, str] = {
    "ул": "улица",
    "ул.": "улица",
    "улица": "улица",
    "улице": "улица",
    "улицы": "улица",
    "куча": "улица",
    "кучаи": "улица",
    "к.": "улица",
    "пр": "проспект",
    "пр.": "проспект",
    "просп": "проспект",
    "просп.": "проспект",
    "пр-т": "проспект",
    "пр-кт": "проспект",
    "проспект": "проспект",
    "проспекте": "проспект",
    "хиебон": "проспект",
    "хиебони": "проспект",
    "пер": "переулок",
    "пер.": "переулок",
    "переулок": "переулок",
    "проезд": "проезд",
    "бульвар": "бульвар",
    "шоссе": "шоссе",
    "тупик": "тупик",
}
_STREET_RE = re.compile(
    r"\b(ул\.?|улиц[аеы]|куча(?:и)?|к\.|пр\.?|просп\.?|пр-к?т|проспект[ае]?|хиебон(?:и)?|пер\.?|переулок|проезд|бульвар|шоссе|тупик)"
    r"\s+([^,;\d]+?)(?=\s*,|\s+\d|\s*$)",
    re.IGNORECASE,
)
#: "Рудаки 10", "Айни, 12" — a capitalised name right before a number, when no street type was written.
_BARE_STREET_RE = re.compile(
    r"(?:^|,\s*)([А-ЯЁӢӮҲҚҒҶ][^\d,;]{1,40}?)\s*,?\s+(\d+\s*[а-яa-z]?(?:\s*/\s*\d+)?)\s*(?=,|$)"
)
_TRAILING_NUMBER_RE = re.compile(r"(?:^|[,\s])(\d{1,4}\s*[а-яa-z]?(?:\s*/\s*\d+)?)\s*$")
_SPACES_RE = re.compile(r"\s+")
_PUNCT_EDGES = " \t,;.:-–—"
_NUMBER_RE = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class ParsedAddress:
    raw: str
    district: str | None = None
    microdistrict: str | None = None  # the number only: "18"
    street: str | None = None  # "улица Айни", "проспект Рудаки"
    house: str | None = None  # "7", "7/1", "12а"
    apartment: str | None = None
    entrance: str | None = None
    floor: str | None = None
    landmark: str | None = None
    rest: str = ""  # what was not understood (kept for the raw query)

    def delivery_fields(self) -> dict[str, Any]:
        """Structured columns of ``deliveries`` (``DeliveryService.TEXT_FIELDS``) — only what was found."""
        values = {
            "district": self.district,
            "microdistrict": self.microdistrict,
            "street": self.street,
            "house": self.house,
            "apartment": self.apartment,
            "entrance": self.entrance,
            "floor": self.floor,
            "landmark": self.landmark,
        }
        return {key: value for key, value in values.items() if value}


@dataclass(frozen=True, slots=True)
class AddressQuery:
    text: str
    level: str  # one of the LEVEL_* constants
    parts: dict[str, str] = field(default_factory=dict)


def _clean(value: str | None) -> str:
    return _SPACES_RE.sub(" ", (value or "").strip(_PUNCT_EDGES)).strip()


def _title(value: str) -> str:
    """``"РУДАКИ"`` / ``"рудаки"`` → ``"Рудаки"``; multi-word names keep every word capitalised."""
    words = [word[:1].upper() + word[1:] if word and not word[:1].isupper() else word for word in value.split()]
    return " ".join(words)


def _normalize_house(value: str) -> str:
    text = _clean(value).lower()
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s*(?:корп\.?|корпус|к\.)\s*", " к", text)
    return _SPACES_RE.sub(" ", text).strip()


def _normalize_street(type_word: str, name: str) -> str:
    kind = _STREET_TYPES.get(tajik_fold(type_word).lower().rstrip(",")) or _STREET_TYPES.get(
        tajik_fold(type_word).lower()
    )
    kind = kind or "улица"
    folded = tajik_fold(_clean(name))
    # Tajik genitive "кӯчаи Рӯдакӣ" — the name itself often ends with "-и" ("Рӯдакӣ" → "Рудаки" is fine).
    return f"{kind} {_title(folded)}"


def parse_address(raw: str | None) -> ParsedAddress:
    """Split a customer's address into parts. Never raises; unknown text ends up in ``rest``."""
    text = _clean(raw)
    if not text:
        return ParsedAddress(raw="")
    original = text
    work = tajik_fold(text).replace("ё", "е").replace("Ё", "Е")

    work = _CITY_RE.sub(" ", work)

    landmark: str | None = None
    match = _LANDMARK_RE.search(work)
    if match and _clean(match.group(1)):
        landmark = _clean(original[match.start(1) :]) if len(original) == len(work) else _clean(match.group(1))
        landmark = _title(landmark) if landmark and landmark.islower() else landmark
        work = work[: match.start()]

    def take(pattern: re.Pattern[str], text_value: str) -> tuple[str | None, str]:
        found = pattern.search(text_value)
        if not found:
            return None, text_value
        value = next((group for group in found.groups() if group), None)
        return (_clean(value) if value else None), text_value[: found.start()] + " " + text_value[found.end() :]

    apartment, work = take(_APARTMENT_RE, work)
    entrance, work = take(_ENTRANCE_RE, work)
    floor, work = take(_FLOOR_RE, work)

    microdistrict, work = take(_MICRODISTRICT_RE, work)
    house, work = take(_HOUSE_RE, work)
    if house:
        house = _normalize_house(house)

    district: str | None = None
    match = _DISTRICT_RE.search(work)
    if match:
        key = re.sub(r"[.\s]+", " ", match.group(1).lower()).strip()
        district = _DISTRICTS.get(key)
        work = work[: match.start()] + " " + work[match.end() :]

    street: str | None = None
    match = _STREET_RE.search(work)
    if match:
        street = _normalize_street(match.group(1), match.group(2))
        work = work[: match.start()] + " " + work[match.end() :]
    elif microdistrict is None:
        bare = _BARE_STREET_RE.search(_clean(work))
        if bare:
            street = f"улица {_title(_clean(bare.group(1)))}"
            if house is None:
                house = _normalize_house(bare.group(2))
            cleaned = _clean(work)
            work = cleaned[: bare.start()] + " " + cleaned[bare.end() :]

    if house is None and (street or microdistrict):
        trailing = _TRAILING_NUMBER_RE.search(_clean(work))
        if trailing:
            house = _normalize_house(trailing.group(1))
            cleaned = _clean(work)
            work = cleaned[: trailing.start()]

    rest = _clean(re.sub(r"[,;]\s*[,;]", ",", work))
    return ParsedAddress(
        raw=original,
        district=district,
        microdistrict=microdistrict,
        street=street,
        house=house,
        apartment=apartment,
        entrance=entrance,
        floor=floor,
        landmark=landmark,
        rest=rest,
    )


def geocode_queries(parsed: ParsedAddress, city: str) -> list[AddressQuery]:
    """The query ladder for one address, most precise first (at most :data:`MAX_QUERIES` entries)."""
    city_part = _clean(city)
    prefix = f"{city_part}, " if city_part else ""
    queries: list[AddressQuery] = []

    def add(text: str, level: str, **parts: str) -> None:
        if len(queries) < MAX_QUERIES and all(query.text != text for query in queries):
            queries.append(AddressQuery(text=text, level=level, parts=parts))

    if parsed.street:
        if parsed.house:
            add(f"{prefix}{parsed.street}, {parsed.house}", LEVEL_HOUSE, street=parsed.street, house=parsed.house)
        add(f"{prefix}{parsed.street}", LEVEL_STREET, street=parsed.street)
    if parsed.microdistrict:
        # Khujand in OpenStreetMap (checked 17.09.2026): buildings carry addr:street "31 мкр" / "34 МКР",
        # the places are "28 микрорайон" / "29-й мкр". "28-й микрорайон" finds other microdistricts only,
        # and "31 микрорайон, 28" finds the microdistrict but not its house 28 — hence "мкр" with a house.
        number = parsed.microdistrict
        if parsed.house and not parsed.street:
            add(f"{prefix}{number} мкр, {parsed.house}", LEVEL_HOUSE, microdistrict=number, house=parsed.house)
        add(f"{prefix}{number} микрорайон", LEVEL_MICRODISTRICT, microdistrict=number)
        add(f"{prefix}{number} мкр", LEVEL_MICRODISTRICT, microdistrict=number)
    if not parsed.street and not parsed.microdistrict and parsed.rest:
        # "Испечак, дом 3", "Зарафшон 12": a locality or a street written without its type word.
        if parsed.house:
            add(f"{prefix}{parsed.rest}, {parsed.house}", LEVEL_HOUSE, house=parsed.house)
        add(f"{prefix}{parsed.rest}", LEVEL_RAW)
    if parsed.landmark:
        add(f"{prefix}{parsed.landmark}", LEVEL_LANDMARK, landmark=parsed.landmark)
    if not queries:
        if parsed.district:
            add(f"{prefix}{parsed.district}", LEVEL_DISTRICT, district=parsed.district)
        else:
            raw = _clean(_CITY_RE.sub(" ", parsed.raw))
            if raw:
                add(f"{prefix}{raw}", LEVEL_RAW)
    return queries


def _street_core(street: str) -> str:
    words = [word for word in normalize_fold(street).split() if word not in ("улица", "проспект", "переулок", "проезд")]
    return words[-1] if words else ""


_MICRODISTRICT_IN_RESULT_RE = re.compile(r"\b(\d{1,3})\s*(?:й\s*)?(?:микрорайон|мкр)")
_STREET_IN_RESULT_RE = re.compile(r"\b(?:улица|проспект|переулок|проезд|кучаи?|хиебони?)\b")


def candidate_matches(query: AddressQuery, formatted: str) -> bool:
    """Reject a geocoder's guess that *contradicts* the query (a fuzzy match on another place).

    - a microdistrict query: a candidate that names a different microdistrict number is out
      (asked for the 18th, Nominatim offers the 91st); a candidate without any number (the city, a
      district) stays — it is a coarse hit the caller grades by precision;
    - a street query: a candidate that is a street with another name is out; a district or city
      centroid stays.

    Contradiction, not presence, is checked so that providers with their own address wording
    (Google's formatted addresses, self-hosted data) are not rejected for spelling.
    """
    text = normalize_fold(formatted)
    if not text:
        return False
    number = query.parts.get("microdistrict")
    if number:
        found = {match.group(1).lstrip("0") for match in _MICRODISTRICT_IN_RESULT_RE.finditer(text)}
        if found and number.lstrip("0") not in found:
            return False
    street = query.parts.get("street")
    if street and _STREET_IN_RESULT_RE.search(text):
        core = _street_core(street)
        words = text.split()
        if core and core not in words and not any(word.startswith(core) for word in words):
            return False
    return True
