"""Demo catalog, FAQ and business settings for a local run (idempotent).

Usage (from ``backend/``, after ``alembic upgrade head``)::

    python -m scripts.seed_demo

Products and FAQ items are matched by name / question: existing rows are left as they are, so the
script never overwrites what was edited in the admin panel. Business settings are filled only where
the stored value is still empty. The data is invented for testing — replace it with the real
catalog before going live.
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

PRODUCTS: list[dict[str, Any]] = [
    {
        "name": "Торт «Медовик»",
        "description": "Медовые коржи со сметанным кремом, 1,5 кг",
        "price": Decimal("220"),
        "aliases": ["медовик", "медовый торт", "торти асалӣ"],
    },
    {
        "name": "Торт «Наполеон»",
        "description": "Слоёные коржи с заварным кремом, 1,5 кг",
        "price": Decimal("200"),
        "aliases": ["наполеон", "торти наполеон"],
    },
    {
        "name": "Торт «Красный бархат»",
        "description": "Бисквит red velvet с крем-чизом, 1,5 кг",
        "price": Decimal("280"),
        "aliases": ["красный бархат", "red velvet", "ред вельвет"],
    },
    {
        "name": "Торт «Молочная девочка»",
        "description": "Тонкие сгущёночные коржи со сливочным кремом, 1,5 кг",
        "price": Decimal("250"),
        "aliases": ["молочная девочка"],
    },
    {
        "name": "Чизкейк «Нью-Йорк»",
        "description": "Классический запечённый чизкейк, 1,2 кг",
        "price": Decimal("260"),
        "aliases": ["чизкейк", "нью-йорк", "cheesecake"],
    },
    {
        "name": "Эклер",
        "description": "С заварным кремом",
        "price": Decimal("10"),
        "aliases": ["эклеры", "эклер с кремом"],
    },
    {
        "name": "Капкейк",
        "description": "Ванильный или шоколадный, со сливочной шапкой",
        "price": Decimal("15"),
        "aliases": ["капкейки", "кекс с кремом"],
    },
    {
        "name": "Круассан с шоколадом",
        "description": "Слоёное масляное тесто",
        "price": Decimal("14"),
        "aliases": ["круассан", "круассаны"],
    },
    {
        "name": "Самбуса с мясом",
        "description": "Слоёная, из тандыра",
        "price": Decimal("8"),
        "aliases": ["самбуса", "самбӯса"],
    },
    {
        "name": "Пахлава",
        "description": "С грецким орехом и мёдом",
        "price": Decimal("90"),
        "unit": "кг",
        "aliases": ["пахлава", "баклава"],
    },
    {
        "name": "Овсяное печенье",
        "description": "Упаковка 500 г",
        "price": Decimal("35"),
        "unit": "уп.",
        "aliases": ["печенье", "кулчақанд"],
    },
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
