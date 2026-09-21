"""Catalog matching without the LLM (05-ai.md §1 and §5, step 7.2).

The LLM may return a ``product_id``, but it may also return only ``product_text`` ("медовек",
"красный бархот", "торти асал").  ``DialogService`` then asks :class:`ProductMatcher`:

* ``EXACT`` / ``SINGLE`` → the item is accepted;
* ``MULTIPLE``          → the bot asks which one ("какие именно?", SPEC §11);
* ``NONE``              → "такого товара нет в каталоге" + the list of active products.

Matching stages (first hit wins):

1. **Exact** — the normalized + Tajik-folded text equals a product name or alias.
2. **Generic category word** — "торт", "торты", "пирожное", "десерт", "торти", "синнамоны",
   "синабоны": all products whose name or aliases contain that stem or its synonym (``NONE`` when the
   catalog has none); "2 коробки" — all products sold by the box (unit "кор.").
3. **Fuzzy** — containment ("медовик" inside "хочу медовик на завтра", "бархат" inside
   "Красный бархат"), token coverage, and ``difflib`` ratio ≥ :data:`FUZZY_THRESHOLD` for
   typos.  Candidates within :data:`CANDIDATE_MARGIN` of the best score are all reported, so a
   genuinely ambiguous text becomes ``MULTIPLE`` instead of a silent wrong pick.

Inactive (or soft-deleted) products are never matched — 05 §5.7.2 requires the item's product
to be active.  Prices and ids always come from the DB; this module only maps text → ids.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any

from app.ai.text_normalize import normalize_fold

__all__ = [
    "CANDIDATE_MARGIN",
    "CATEGORY_STEMS",
    "FUZZY_THRESHOLD",
    "MatchResult",
    "MatchStatus",
    "ProductMatcher",
    "is_generic_mention",
    "split_item_mentions",
]


class MatchStatus(StrEnum):
    EXACT = "EXACT"
    SINGLE = "SINGLE"
    MULTIPLE = "MULTIPLE"
    NONE = "NONE"


@dataclass(frozen=True)
class MatchResult:
    """``candidates`` are product ids, best first; empty for ``NONE``."""

    status: MatchStatus
    candidates: list[Any] = field(default_factory=list)

    @property
    def product_id(self) -> Any | None:
        """The single accepted product (``EXACT``/``SINGLE``), otherwise ``None``."""
        if self.status in (MatchStatus.EXACT, MatchStatus.SINGLE) and self.candidates:
            return self.candidates[0]
        return None

    def __bool__(self) -> bool:
        return self.status is not MatchStatus.NONE


#: difflib ratio that counts as a typo of the same word ("медовек" → "Медовик" = 0.86).
FUZZY_THRESHOLD = 0.82
#: Candidates scoring within this distance of the best one are reported as ambiguous.
CANDIDATE_MARGIN = 0.05
#: Fuzzy matching is not attempted for very short texts ("ок" must not become "сок").
MIN_FUZZY_LENGTH = 3

#: Category words that name a whole group rather than a product (RU + TG "торти", "ширинӣ"; the
#: cinnamon rolls with their usual spellings; "коробка" — see :data:`PACKAGING_UNITS`).
CATEGORY_STEMS = (
    "торт",
    "пирожн",
    "десерт",
    "выпечк",
    "сладост",
    "кекс",
    "капкейк",
    "чизкейк",
    "ширин",
    "синнамон",
    "синамон",
    "синнабон",
    "синабон",
    "булочк",
    "кориц",
    "короб",
    # Packaging as the customers spell it: "1 каропка", "як куттӣ", "свободных боксов".
    "каропк",
    "коропк",
    "каробк",
    "кутти",
    "бокс",
)
#: A category word also finds the products named with its synonym: "синабоны" and "булочки с корицей"
#: are the "синнамоны" of the catalog.
CATEGORY_SYNONYMS = {
    "синамон": "синнамон",
    "синнабон": "синнамон",
    "синабон": "синнамон",
    "булочк": "синнамон",
    "кориц": "синнамон",
}
#: A packaging word asks for the products sold in it: "2 коробки" → every product with the unit "кор.".
#: When nothing is sold by that unit — a catalog priced per piece — the word still names no flavour,
#: so :meth:`ProductMatcher._match_category` offers the whole catalog instead of answering "нет такого".
PACKAGING_UNITS = dict.fromkeys(("короб", "каропк", "коропк", "каробк", "кутти", "бокс"), "кор")

# Words around a category word that carry no product information ("хочу 2 торта на завтра").
_MENTION_STOPWORDS = frozenset(
    {
        "хочу",
        "хотим",
        "хотел",
        "хотела",
        "хотелось",
        "нужен",
        "нужна",
        "нужно",
        "нужны",
        "надо",
        "можно",
        "дайте",
        "закажу",
        "заказать",
        "заказ",
        "мне",
        "нам",
        "пожалуйста",
        "есть",
        "какие",
        "какой",
        "штук",
        "штуки",
        "шт",
        "пару",
        "пары",
        "несколько",
        "один",
        "одну",
        "одна",
        "два",
        "две",
        "три",
        "четыре",
        "пять",
        # when / how many — context, not a product
        "завтра",
        "сегодня",
        "послезавтра",
        "утром",
        "вечером",
        "днем",
        "ночью",
        "на",
        "для",
        "в",
        "к",
        "с",
        "со",
        "по",
        "до",
        "за",
        "бы",
        "еще",
        "и",
        # tg
        "ва",
        "як",
        "ду",
        "се",
        "лозим",
        "мехохам",
        "бирта",
        "то",
    }
)

# "Красный бархат и медовик", "медовик, наполеон + эклер", "Медовик ва Наполеон".
_MENTION_SPLIT_RE = re.compile(r"\s*(?:,|;|\+|/|\bи\b|\bва\b)\s*", re.IGNORECASE)
_MENTION_TRIM = " \t.!?…-–—:;\"'«»()"


def split_item_mentions(text: str | None) -> list[str]:
    """Split a multi-item answer into separate mentions (SPEC §11: "Красный бархат и медовик")."""
    if not text:
        return []
    parts = _MENTION_SPLIT_RE.split(str(text))
    return [trimmed for part in parts if (trimmed := part.strip(_MENTION_TRIM))]


def _content_tokens(text: str | None) -> list[str]:
    return [
        token
        for token in normalize_fold(text).split()
        if token not in _MENTION_STOPWORDS and not any(char.isdigit() for char in token)
    ]


def is_generic_mention(text: str | None) -> bool:
    """True for "торт", "2 торта", "торты", "десерт", "торти" — a category, not a product.

    ``DialogService`` uses this to tell "какие именно торты?" (SPEC §11, 05 §5.7.2
    ``pending_items``) from "такого товара нет в каталоге".
    """
    tokens = _content_tokens(text)
    return bool(tokens) and all(token.startswith(CATEGORY_STEMS) for token in tokens)


@dataclass(frozen=True)
class _Entry:
    product_id: Any
    order: int
    keys: tuple[str, ...]
    key_tokens: tuple[tuple[str, ...], ...]
    haystack: str
    unit: str


def _attribute(product: Any, name: str, default: Any = None) -> Any:
    if isinstance(product, Mapping):
        return product.get(name, default)
    return getattr(product, name, default)


def _is_active(product: Any) -> bool:
    """05 §5.7.2: only active, not soft-deleted products may be matched."""
    if not bool(_attribute(product, "is_active", True)):
        return False
    return _attribute(product, "deleted_at", None) is None and not bool(_attribute(product, "is_deleted", False))


def _contains_run(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """``needle`` appears as a contiguous run of whole tokens inside ``haystack``."""
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[start : start + len(needle)] == needle for start in range(len(haystack) - len(needle) + 1))


def _token_coverage(query_tokens: Sequence[str], key_tokens: Sequence[str]) -> float:
    """Share of product-name tokens that have a close-enough token in the text (typo tolerant)."""
    if not key_tokens:
        return 0.0
    matched = 0
    for key_token in key_tokens:
        for query_token in query_tokens:
            if key_token == query_token or (
                len(key_token) >= MIN_FUZZY_LENGTH
                and len(query_token) >= MIN_FUZZY_LENGTH
                and SequenceMatcher(None, query_token, key_token).ratio() >= FUZZY_THRESHOLD
            ):
                matched += 1
                break
    return matched / len(key_tokens)


class ProductMatcher:
    """Maps free text to catalog product ids.  Built per request from the active catalog.

    ``products`` may be ORM objects or dicts with ``id``, ``name``, ``aliases``, ``is_active``.
    """

    def __init__(self, products: Iterable[Any]) -> None:
        self._entries: list[_Entry] = []
        for order, product in enumerate(products):
            if not _is_active(product):
                continue
            names = [_attribute(product, "name", "") or ""]
            names.extend(str(alias) for alias in (_attribute(product, "aliases", None) or []))
            keys = tuple(dict.fromkeys(key for key in (normalize_fold(name) for name in names) if key))
            if not keys:
                continue
            self._entries.append(
                _Entry(
                    product_id=_attribute(product, "id"),
                    order=order,
                    keys=keys,
                    key_tokens=tuple(tuple(key.split()) for key in keys),
                    haystack=" ".join(keys),
                    unit=normalize_fold(_attribute(product, "unit", None)),
                )
            )

    def __len__(self) -> int:
        return len(self._entries)

    def match(self, text: str | None) -> MatchResult:
        """Match one product mention.  Never raises; unknown text → ``NONE``."""
        query = normalize_fold(text)
        if not query:
            return MatchResult(MatchStatus.NONE)
        query_tokens = query.split()

        # 1. Exact name/alias.
        exact = [entry.product_id for entry in self._entries if query in entry.keys]
        if exact:
            return MatchResult(MatchStatus.EXACT if len(exact) == 1 else MatchStatus.MULTIPLE, exact)

        # 2. Category word ("торт", "торты", "десерт", "торти") → everything in that group.
        if is_generic_mention(query):
            return self._match_category(query_tokens)

        # 3. Containment / token coverage / typos.
        return self._match_fuzzy(query, query_tokens)

    def _match_category(self, query_tokens: list[str]) -> MatchResult:
        stems = {stem for stem in CATEGORY_STEMS for token in query_tokens if token.startswith(stem)}
        names = stems | {CATEGORY_SYNONYMS[stem] for stem in stems if stem in CATEGORY_SYNONYMS}
        units = {PACKAGING_UNITS[stem] for stem in stems if stem in PACKAGING_UNITS}
        candidates = [
            entry.product_id
            for entry in self._entries
            if any(stem in entry.haystack for stem in names) or any(entry.unit.startswith(unit) for unit in units)
        ]
        if not candidates and stems and stems <= set(PACKAGING_UNITS):
            # Only packaging was named ("сколько стоит коробка?", "1 каропка") and nothing is priced by
            # the box: every flavour is sold in one, so ask which — never "такого товара нет".
            candidates = [entry.product_id for entry in self._entries]
        if not candidates:
            return MatchResult(MatchStatus.NONE)
        # One cake in the catalog is not a question worth asking.
        status = MatchStatus.SINGLE if len(candidates) == 1 else MatchStatus.MULTIPLE
        return MatchResult(status, candidates)

    def _match_fuzzy(self, query: str, query_tokens: list[str]) -> MatchResult:
        scored: list[tuple[float, int, Any]] = []
        for entry in self._entries:
            score = max(
                (
                    self._score(query, query_tokens, key, key_tokens)
                    for key, key_tokens in zip(entry.keys, entry.key_tokens, strict=True)
                ),
                default=0.0,
            )
            if score >= FUZZY_THRESHOLD:
                scored.append((score, entry.order, entry.product_id))
        if not scored:
            return MatchResult(MatchStatus.NONE)

        scored.sort(key=lambda item: (-item[0], item[1]))
        best = scored[0][0]
        candidates = [product_id for score, _, product_id in scored if best - score <= CANDIDATE_MARGIN]
        status = MatchStatus.SINGLE if len(candidates) == 1 else MatchStatus.MULTIPLE
        return MatchResult(status, candidates)

    @staticmethod
    def _score(query: str, query_tokens: list[str], key: str, key_tokens: tuple[str, ...]) -> float:
        if not key:
            return 0.0
        if query == key:
            return 1.0
        if len(query) < MIN_FUZZY_LENGTH or len(key) < MIN_FUZZY_LENGTH:
            return 0.0  # short texts only ever match exactly
        if _contains_run(tuple(query_tokens), key_tokens):
            return 0.97  # the whole product name is inside the message
        if _contains_run(key_tokens, tuple(query_tokens)):
            return 0.92  # the message is part of the product name ("бархат" → "Красный бархат")
        ratio = SequenceMatcher(None, query, key).ratio()
        return max(ratio, 0.95 * _token_coverage(query_tokens, key_tokens))
