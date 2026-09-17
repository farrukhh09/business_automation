"""Product catalog (04-api.md §4, SPEC §15).

The admin creates/edits/(de)activates products and changes prices; the bot matches the *current*
catalog (``app/ai/product_matcher.py``). Rules implemented here:

- ``name`` is required and trimmed, ``price`` must be > 0 with ``Money`` precision (03 §1.4),
  ``currency`` defaults to ``TJS`` and ``unit`` to ``шт.``;
- ``aliases`` are normalized (trimmed, lowercased, de-duplicated, blanks removed) so the matcher
  compares folded text against folded aliases;
- ``DELETE`` is a **soft delete**: ``deleted_at`` is stamped and ``is_active`` set to ``false``;
  a deleted product is invisible to the whole application except order history (02-data-model.md).

Prices of existing orders are snapshots (``order_items.unit_price``, 03 §1.4): changing
``Product.price`` here never touches an order — this service only writes the ``products`` row.
"""

from collections.abc import Iterable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.core.logging import get_logger, log_event
from app.core.time import now_utc
from app.models.product import Product
from app.models.user import User
from app.repositories.products import ProductRepository
from app.schemas.product import ProductCreate, ProductUpdate

logger = get_logger(__name__)

S = TypeVar("S", bound=BaseModel)

# ``null`` in a PATCH body means "leave unchanged" for every field except these, which are cleared.
CLEARABLE_PRODUCT_FIELDS: frozenset[str] = frozenset({"description"})

PRODUCT_INVALID_DETAIL = "Некорректные данные товара"


def normalize_tags(values: Iterable[str] | None) -> list[str]:
    """Aliases / FAQ keywords: trimmed (inner whitespace collapsed), lowercased, blanks and
    duplicates removed; the original order is kept."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        tag = " ".join(str(value).split()).lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def error_fields(exc: PydanticValidationError) -> list[str]:
    return sorted({".".join(str(part) for part in error["loc"]) or "__root__" for error in exc.errors()})


def validate_payload(schema: type[S], data: S | Mapping[str, Any], detail: str) -> S:
    """Accept an already validated schema object or a plain mapping (service called directly)."""
    if isinstance(data, schema):
        return data
    try:
        return schema.model_validate(dict(data))
    except PydanticValidationError as exc:
        raise ValidationError(detail=detail, fields=error_fields(exc)) from exc


def apply_patch(
    entity: Any,
    changes: Mapping[str, Any],
    *,
    clearable: frozenset[str],
    normalized_lists: frozenset[str] = frozenset(),
) -> list[str]:
    """Apply the set fields of a PATCH body; returns the names of the fields that really changed."""
    changed: list[str] = []
    for field, raw in changes.items():
        if raw is None and field not in clearable:
            continue
        value = normalize_tags(raw) if field in normalized_lists and raw is not None else raw
        old = getattr(entity, field)
        if old != value:
            setattr(entity, field, value)
            changed.append(field)
    return changed


class ProductService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = ProductRepository(db)

    # ------------------------------------------------------------------ reads

    def list(self, include_inactive: bool = False, search: str | None = None) -> list[Product]:
        """``GET /products``: never deleted; inactive only with ``include_inactive``."""
        return self.repository.list(include_inactive=include_inactive, search=search)

    def get(self, product_id: int) -> Product:
        """``GET /products/{id}``: deleted products are gone → 404."""
        return self.repository.get_visible_or_raise(product_id)

    # ------------------------------------------------------------------ writes

    def create(self, data: ProductCreate | Mapping[str, Any], user: User | None = None) -> Product:
        payload = validate_payload(ProductCreate, data, PRODUCT_INVALID_DETAIL)
        product = Product(
            name=payload.name,
            description=payload.description,
            price=payload.price,
            currency=payload.currency,
            unit=payload.unit,
            aliases=normalize_tags(payload.aliases),
            is_active=payload.is_active,
            sort_order=payload.sort_order,
        )
        self.repository.add(product)
        self.db.commit()
        log_event(
            logger,
            "product.created",
            product_id=product.id,
            is_active=product.is_active,
            user_id=user.id if user else None,
        )
        return product

    def update(self, product_id: int, data: ProductUpdate | Mapping[str, Any], user: User | None = None) -> Product:
        payload = validate_payload(ProductUpdate, data, PRODUCT_INVALID_DETAIL)
        product = self.repository.get_visible_or_raise(product_id)
        changed = apply_patch(
            product,
            payload.model_dump(exclude_unset=True),
            clearable=CLEARABLE_PRODUCT_FIELDS,
            normalized_lists=frozenset({"aliases"}),
        )
        self.repository.flush()
        self.db.commit()
        log_event(
            logger,
            "product.updated",
            product_id=product_id,
            fields=sorted(changed),
            user_id=user.id if user else None,
        )
        return product

    def delete(self, product_id: int, user: User | None = None) -> None:
        """Soft delete (04 §4): ``deleted_at`` + ``is_active=false``; order history keeps the name."""
        product = self.repository.get_visible_or_raise(product_id)
        product.deleted_at = now_utc()
        product.is_active = False
        self.repository.flush()
        self.db.commit()
        log_event(logger, "product.deleted", product_id=product_id, user_id=user.id if user else None)
