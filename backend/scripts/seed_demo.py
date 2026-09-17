"""Demo catalog, FAQ and business settings for a local run (idempotent).

Usage (from ``backend/``, after ``alembic upgrade head``)::

    python -m scripts.seed_demo

Products and FAQ items are matched by name / question: existing rows are left as they are, so the
script never overwrites what was edited in the admin panel. Business settings are filled only where
the stored value is still empty.

The catalog is the bakery's own, from its Instagram (17.09.2026): cinnamon rolls sold in boxes of 4 —
five flavours and the assorted box «Палитра вкуса». The prices are NOT: the posts show none, so every
box costs a temporary 100 сомони for testing (said in the description) until the owner sets the real
prices in the admin panel («Товары»). Settings and the pickup address are still invented test data.
"""

import sys
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import session_scope
from app.models.faq import FaqItem
from app.models.product import Product
from app.services.product_service import ProductService
from app.services.settings_service import SettingsService
from scripts.faq_data import FAQ

#: Temporary test price of a box — the Instagram posts name no prices (see the module docstring).
TEST_PRICE = Decimal("100")
TEST_PRICE_NOTE = "Цена временная — для теста."
BOX_UNIT = "кор."


def _box(name: str, description: str, aliases: list[str]) -> dict[str, Any]:
    """A box of 4 cinnamon rolls at the temporary test price."""
    return {
        "name": name,
        "description": f"{description} {TEST_PRICE_NOTE}",
        "price": TEST_PRICE,
        "unit": BOX_UNIT,
        "aliases": aliases,
    }


PRODUCTS: list[dict[str, Any]] = [
    _box(
        "Классические синнамоны",
        "Коробочка из 4 синнамонов с корицей и нежной глазурью.",
        [
            "классика",
            "классические",
            "классический",
            "классических",
            "классическую",
            "классику",
            "синнамоны классика",
            "коробочка классика",
            "классикӣ",
        ],
    ),
    _box(
        "Шоколадные синнамоны",
        "Коробочка из 4 синнамонов с шоколадным соусом и глазурью.",
        [
            "шоколад",
            "шоколадные",
            "шоколадный",
            "шоколадных",
            "шоколадную",
            "с шоколадом",
            "синнамоны шоколад",
            "коробочка шоколад",
            "шоколадӣ",
            "бо шоколад",
        ],
    ),
    _box(
        "Ягодные синнамоны",
        "Коробочка из 4 синнамонов с ягодным соусом и глазурью.",
        [
            "ягоды",
            "ягодные",
            "ягодный",
            "ягодных",
            "ягодную",
            "с ягодами",
            "синнамоны ягоды",
            "коробочка ягоды",
            "буттамева",
            "бо буттамева",
        ],
    ),
    _box(
        "Фисташковые синнамоны",
        "Коробочка из 4 синнамонов с фисташковым кремом и дроблёными фисташками.",
        [
            "фисташка",
            "фисташки",
            "фисташку",
            "фисташковые",
            "фисташковый",
            "фисташковых",
            "фисташковую",
            "с фисташкой",
            "синнамоны фисташка",
            "коробочка фисташка",
            "писта",
            "пистагӣ",
        ],
    ),
    _box(
        "Яблочные синнамоны",
        "Коробочка из 4 синнамонов с сочными яблоками и корицей.",
        [
            "яблоко",
            "яблоки",
            "яблочные",
            "яблочный",
            "яблочных",
            "яблочную",
            "с яблоками",
            "синнамоны яблоко",
            "коробочка яблоко",
            "себӣ",
            "бо себ",
        ],
    ),
    _box(
        "Ассорти «Палитра вкуса»",
        "4 вкуса в одной коробочке: классика, шоколад, ягоды и фисташка.",
        [
            "ассорти",
            "палитра",
            "палитра вкуса",
            "микс",
            "разные вкусы",
            "4 вкуса",
            "ассорти синнамонов",
            "коробочка ассорти",
            "омехта",
        ],
    ),
]

BUSINESS_SETTINGS: dict[str, Any] = {
    "business_name": "Синнамоны",
    "pickup_address": "Худжанд, улица Ленина, 45 (тестовый адрес)",
    "working_hours": "Ежедневно с 9:00 до 20:00",
    "payment_methods_text": "Наличными при получении или переводом на карту (реквизиты пришлёт менеджер).",
    "delivery_info_text": "Доставка по Худжанду — от 20 до 40 сомони в зависимости от района. Самовывоз бесплатный.",
    "warehouse": {
        "name": "Кухня",
        "address": "Худжанд, улица Ленина, 45 (тестовый адрес)",
        "latitude": 40.2842191,
        "longitude": 69.6191174,
    },
}


def seed_products(db: Session) -> int:
    existing = set(db.scalars(select(Product.name)))
    service = ProductService(db)
    created = 0
    for sort_order, data in enumerate(PRODUCTS, start=1):
        if data["name"] in existing:
            continue
        service.create({**data, "sort_order": sort_order * 10})
        created += 1
    return created


def seed_faq(db: Session) -> tuple[int, int]:
    """Adds missing entries; an entry still holding the seeded answer gets the current keywords and
    Tajik texts (an answer edited in the admin panel marks the entry as the owner's — left alone)."""
    existing = {item.question: item for item in db.scalars(select(FaqItem))}
    created = updated = 0
    for sort_order, data in enumerate(FAQ, start=1):
        item = existing.get(data["question"])
        if item is None:
            db.add(FaqItem(**data, is_active=True, sort_order=sort_order * 10))
            created += 1
        elif item.answer == data["answer"] and (
            item.keywords != data["keywords"]
            or item.question_tg != data["question_tg"]
            or item.answer_tg != data["answer_tg"]
        ):
            item.keywords = list(data["keywords"])
            item.question_tg, item.answer_tg = data["question_tg"], data["answer_tg"]
            updated += 1
    db.commit()
    return created, updated


def seed_settings(db: Session) -> list[str]:
    service = SettingsService(db)
    current = service.get()
    patch: dict[str, Any] = {}
    for key, value in BUSINESS_SETTINGS.items():
        if key == "warehouse":
            if current.warehouse.latitude is None or current.warehouse.longitude is None:
                patch[key] = value
        elif not getattr(current, key):
            patch[key] = value
    if patch:
        service.update(patch)
    return sorted(patch)


def main() -> int:
    with session_scope() as db:
        products = seed_products(db)
        faq, faq_updated = seed_faq(db)
        settings = seed_settings(db)
    print(f"Товаров добавлено: {products} (всего в наборе {len(PRODUCTS)})")
    print(f"Вопросов FAQ добавлено: {faq}, обновлено: {faq_updated} (всего в наборе {len(FAQ)})")
    print(f"Настройки заполнены: {', '.join(settings) if settings else 'ничего не менялось'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
