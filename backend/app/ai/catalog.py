"""Flavours and sizes of the catalog (03-business-rules.md §1.4).

Every flavour is sold in two sizes, and each size is a product of its own because it has a price
of its own: «Классический синнамон» — 10 сомони, «Классический большой» — 15.  Nothing in the
database ties the two rows together, so the pairing is read off the names: products whose name
begins with the same word are one flavour, and the member whose remaining words are only a size
word (:data:`SIZE_WORDS`) is that flavour's larger size.  The first member that names no size is
the standard one — what a bare flavour word means everywhere else in the bot.

The grouping is used in two places:

* the catalog is listed by flavour, not by product, so a price question is answered in six lines
  with both prices instead of twelve rows (the owner, 23.09.2026: «чтоб не было большого текста»);
* a mix («микс со всеми вкусами») is assembled from the standard sizes, one flavour each.

A name that fits nothing of the kind simply stays a group of its own, so a catalog without sizes
is listed exactly as before.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.ai.text_normalize import normalize_fold

__all__ = [
    "SIZE_WORDS",
    "FlavourGroup",
    "flavour_bases",
    "flavour_groups",
    "flavour_names",
    "product_name",
    "sized_products",
]

#: How the price list of 23.09.2026 names the larger size («БОЛЬШОЙ РАЗМЕР»), RU and TG, folded.
SIZE_WORDS: tuple[str, ...] = ("большой", "большая", "большие", "калон")
#: How customers ask for it: "большие", "крупные", "калонаш", "калонтар". Whole forms of "большой",
#: not a stem: "больше крема" / "побольше" ask for more of something, not for the large size, and
#: "большое" is left out for "большое спасибо".
_SIZE_FORMS: frozenset[str] = frozenset(
    {"большой", "большая", "большие", "большого", "больших", "большим", "большую", "большими", "большом"}
)
_SIZE_STEMS: tuple[str, ...] = ("крупн", "калон")


def _is_size_word(token: str) -> bool:
    return token in _SIZE_FORMS or token.startswith(_SIZE_STEMS)


@dataclass(frozen=True)
class FlavourGroup:
    """One flavour: the standard product and its sized variants as ``(size words, product)``."""

    base: Any
    variants: tuple[tuple[str, Any], ...] = ()


def product_name(product: Any) -> str:
    """The name of a product given as an ORM object or as a facts dict."""
    value = product.get("name") if isinstance(product, Mapping) else getattr(product, "name", None)
    return str(value or "")


def _split(product: Any) -> tuple[str, str]:
    """``("классический", "большой")`` — the flavour key and the size words ("" when none)."""
    words = product_name(product).split()
    if not words:
        return "", ""
    rest = words[1:]
    size = " ".join(rest) if rest and all(normalize_fold(word) in SIZE_WORDS for word in rest) else ""
    return normalize_fold(words[0]), size


def flavour_groups(products: Iterable[Any]) -> list[FlavourGroup]:
    """Group the catalog by flavour, keeping the order the products came in."""
    by_flavour: dict[str, list[tuple[str, Any]]] = {}
    for product in products:
        key, size = _split(product)
        by_flavour.setdefault(key, []).append((size, product))
    groups: list[FlavourGroup] = []
    for members in by_flavour.values():
        bases = [product for size, product in members if not size]
        if not bases:
            # Only sized names share this first word — each one stands on its own.
            groups.extend(FlavourGroup(product) for _, product in members)
            continue
        groups.append(FlavourGroup(bases[0], tuple((size, product) for size, product in members if size)))
        # Same first word, but not a size ("Шоколадный синнамон" and "Шоколадный торт"): own lines.
        groups.extend(FlavourGroup(product) for product in bases[1:])
    return groups


def flavour_bases(products: Iterable[Any]) -> list[Any]:
    """One product per flavour — the standard size, which is what a mix is assembled from."""
    return [group.base for group in flavour_groups(products)]


def flavour_names(names: Iterable[str]) -> list[str]:
    """The same folding for a plain list of names: «Классический большой» drops into its flavour."""
    return [product_name(group.base) for group in flavour_groups({"name": name} for name in names)]


def sized_products(products: Iterable[Any], text: str | None) -> list[Any]:
    """The larger sizes a question asks about: "а большие есть?" → every large one; "большие
    шоколадные" → the chocolate one. ``[]`` when the text names no size or the catalog has none —
    a size word is never "такого у нас нет" (audit 23.09.2026)."""
    tokens = normalize_fold(text).split()
    if not any(_is_size_word(token) for token in tokens):
        return []
    groups = flavour_groups(products)
    sized = [variant for group in groups for _, variant in group.variants]
    others = [token for token in tokens if not _is_size_word(token) and len(token) >= 4]
    named = [
        variant
        for group in groups
        for _, variant in group.variants
        if any(token[:5] == _split(group.base)[0][:5] for token in others)
    ]
    return named or sized
