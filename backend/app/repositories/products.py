"""Products (02-data-model.md: products; 04-api.md §4).

Soft-deleted products (``deleted_at`` set) are hidden everywhere except order history.
Text search runs in Python over the (small) catalog: it matches ``name`` and ``aliases``
case-insensitively for Cyrillic on every database (SQLite's ``lower()`` is ASCII-only and
JSON aliases are stored with ``\\u`` escapes).
"""

import builtins
from collections.abc import Iterable

from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.models.product import Product
from app.repositories.base import BaseRepository


def _fold(text: str | None) -> str:
    return (text or "").casefold().replace("ё", "е").strip()


def product_matches(product: Product, search: str) -> bool:
    needle = _fold(search)
    if not needle:
        return True
    haystacks = [product.name, *(product.aliases or [])]
    return any(needle in _fold(value) for value in haystacks)


class ProductRepository(BaseRepository[Product]):
    model = Product
    not_found_detail = "Товар не найден"

    def list(self, include_inactive: bool = False, search: str | None = None) -> builtins.list[Product]:
        """Not deleted; active only unless ``include_inactive``; ordered by ``sort_order``, name, id."""
        stmt = select(Product).where(Product.deleted_at.is_(None))
        if not include_inactive:
            stmt = stmt.where(Product.is_active.is_(True))
        stmt = stmt.order_by(Product.sort_order, Product.name, Product.id)
        products = builtins.list(self.db.scalars(stmt).all())
        if search and search.strip():
            products = [product for product in products if product_matches(product, search)]
        return products

    def get_visible(self, product_id: int) -> Product | None:
        """Not deleted (active or inactive) — ``GET/PATCH/DELETE /products/{id}``."""
        product = self.get(product_id)
        if product is None or product.deleted_at is not None:
            return None
        return product

    def get_visible_or_raise(self, product_id: int) -> Product:
        product = self.get_visible(product_id)
        if product is None:
            raise NotFoundError(self.not_found_detail)
        return product

    def get_active(self, product_id: int) -> Product | None:
        """Active and not deleted — the only products that may be added to an order (03 §1.3)."""
        product = self.get(product_id)
        if product is None or product.deleted_at is not None or not product.is_active:
            return None
        return product

    def get_many(self, ids: Iterable[int]) -> dict[int, Product]:
        """``{id: Product}`` for the given ids, including inactive/deleted ones (callers validate)."""
        unique_ids = sorted({int(product_id) for product_id in ids})
        if not unique_ids:
            return {}
        products = self.db.scalars(select(Product).where(Product.id.in_(unique_ids))).all()
        return {product.id: product for product in products}
