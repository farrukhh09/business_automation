"""Catalog, FAQ and business settings of the bakery for a local run (idempotent).

Usage (from ``backend/``, after ``alembic upgrade head``)::

    python -m scripts.seed_demo              # add what is missing
    python -m scripts.seed_demo --refresh    # also rewrite prices, FAQ answers and settings from this set

Products and FAQ items are matched by name / question: existing rows are left as they are, so the
script never overwrites what was edited in the admin panel (``--refresh`` is the exception, and it
says so). Business settings are filled only where the value is still empty or at its default.

Everything here comes from the bakery's own Instagram Direct archive (224 chats, 31.08–18.09.2026,
read on 21.09.2026), so it is what the owner really tells customers — prices, the packing rule, the
pickup point, the days off and the prepayment policy. Two things are still the owner's to enter in
the admin panel, because they are personal data this repository must not carry:

* the payment number and the «Предоплата» switch («Настройки → Предоплата») — the FAQ answers about
  prepayment and the payment text follow that switch, so turning it on brings them back;
* the exact pin of the kitchen on the map («Настройки → Склад»), used for route optimization.

The catalog is priced PER ROLL, the way the bakery quotes it: a box is packaging, not a product, and
its price is the sum of what is inside it ("40+72+14 = 126"). Since the owner's price list of
23.09.2026 every flavour comes in two sizes, each with its own price — standard (10 / 18 / 23) and
large (15 / 26 / 34) — so the large one is a product of its own, matched only when the customer says
the size; a bare "классический" still means the standard roll.

The packing rule lives in the settings: boxes of 4 and of 6 add up to every even total from 4 up,
which is ``min_order_quantity = 4`` with ``order_quantity_step = 2``. The box sizes as such are named
in the FAQ, where the owner can reword them.
"""

import sys
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.catalog import SIZE_WORDS
from app.core.database import session_scope
from app.models.faq import FaqItem
from app.models.product import Product
from app.schemas.settings import BusinessSettings
from app.services.product_service import ProductService
from app.services.settings_service import SettingsService
from scripts.faq_data import FAQ, PREPAYMENT_QUESTIONS, RETIRED_QUESTIONS

PIECE_UNIT = "шт."
#: Boxes of 4 seeded before 21.09.2026, replaced by the per-roll catalog below. They are switched
#: off (never deleted) when they still carry the old test price, i.e. nobody edited them by hand.
RETIRED_BOX_PRICE = Decimal("100")
RETIRED_PRODUCT_NAMES: tuple[str, ...] = (
    "Классические синнамоны",
    "Шоколадные синнамоны",
    "Ягодные синнамоны",
    "Фисташковые синнамоны",
    "Яблочные синнамоны",
    "Ассорти «Палитра вкуса»",
)


def _roll(name: str, price: str, description: str, aliases: list[str]) -> dict[str, Any]:
    """One cinnamon roll of that flavour, priced as the bakery quotes it."""
    return {
        "name": name,
        "description": description,
        "price": Decimal(price),
        "unit": PIECE_UNIT,
        "aliases": aliases,
    }


#: How the customer names the large size (price list 23.09.2026: «стандартный» / «большой размер»).
#: The same words group the catalog by flavour in the bot's texts, so there is one list of them.
LARGE_WORDS: tuple[str, ...] = SIZE_WORDS


def _large(name: str, price: str, description: str, flavours: list[str]) -> dict[str, Any]:
    """The same flavour in the large size: a product of its own, because it has a price of its own.

    A bare flavour word keeps meaning the standard roll — that is what every customer in the archive
    means — so this one is matched only when the size is said: «классический большой», «калон».
    ``flavours`` are the few core words of the flavour; both word orders are generated.

    The name deliberately carries no word «синнамон»: a category word ("хочу 5 синнамонов") then
    offers the six flavours to pick from instead of twelve rows where each flavour appears twice.
    The full assortment with both sizes is still what a price question answers.
    """
    aliases = [f"{flavour} {size}" for flavour in flavours for size in LARGE_WORDS]
    aliases += [f"{size} {flavour}" for flavour in flavours for size in LARGE_WORDS]
    return _roll(name, price, description, aliases)


PRODUCTS: list[dict[str, Any]] = [
    _roll(
        "Классический синнамон",
        "10",
        "Синнамон с корицей и нежным сливочным кремом — тот самый, с которого всё началось.",
        [
            "классика",
            "классический",
            "классические",
            "классических",
            "классическую",
            "классику",
            "обычный",
            "обычные",
            "белый",
            "с корицей",
            "классикӣ",
        ],
    ),
    _roll(
        "Шоколадный синнамон",
        "18",
        "Синнамон с шоколадным соусом и глазурью.",
        [
            "шоколад",
            "шоколадный",
            "шоколадные",
            "шоколадных",
            "шоколадную",
            "с шоколадом",
            "шак",
            "шоколадӣ",
            "бо шоколад",
        ],
    ),
    _roll(
        "Ягодный синнамон",
        "18",
        "Синнамон с ягодным соусом и глазурью.",
        [
            "ягода",
            "ягоды",
            "ягодный",
            "ягодные",
            "ягодных",
            "ягодную",
            "с ягодами",
            "малиновый",
            "ягад",
            "буттамева",
            "бо буттамева",
        ],
    ),
    _roll(
        "Яблочный синнамон",
        "18",
        "Синнамон с сочными яблоками и корицей.",
        [
            "яблоко",
            "яблоки",
            "яблочный",
            "яблочные",
            "яблочных",
            "яблочную",
            "с яблоками",
            "яблок",
            "себ",
            "себӣ",
            "бо себ",
        ],
    ),
    _roll(
        "Банановый синнамон",
        "18",
        "Синнамон с бананом и карамельной ноткой.",
        [
            "банан",
            "банановый",
            "банановые",
            "банановых",
            "банановую",
            "с бананом",
            "бан",
            "бананӣ",
        ],
    ),
    _roll(
        "Фисташковый синнамон",
        "23",
        "Синнамон с фисташковым кремом и дроблёными фисташками.",
        [
            "фисташка",
            "фисташки",
            "фисташку",
            "фисташковый",
            "фисташковые",
            "фисташковых",
            "фисташковую",
            "с фисташкой",
            "писта",
            "пистагӣ",
        ],
    ),
    # The large size, added 23.09.2026 from the owner's own price list («БОЛЬШОЙ РАЗМЕР»). Prices
    # are per roll, as everywhere here: 15 / 26 / 26 / 26 / 26 / 34 сомони.
    _large(
        "Классический большой",
        "15",
        "Тот же классический синнамон, только большого размера.",
        ["классический", "классика", "классикӣ"],
    ),
    _large(
        "Шоколадный большой",
        "26",
        "Шоколадный синнамон большого размера.",
        ["шоколадный", "шоколад", "шоколадӣ"],
    ),
    _large(
        "Ягодный большой",
        "26",
        "Ягодный синнамон большого размера.",
        ["ягодный", "ягода", "буттамева"],
    ),
    _large(
        "Яблочный большой",
        "26",
        "Яблочный синнамон большого размера.",
        ["яблочный", "яблоко", "себ"],
    ),
    _large(
        "Банановый большой",
        "26",
        "Банановый синнамон большого размера.",
        ["банановый", "банан", "бананӣ"],
    ),
    _large(
        "Фисташковый большой",
        "34",
        "Фисташковый синнамон большого размера.",
        ["фисташковый", "фисташка", "писта"],
    ),
]

BUSINESS_SETTINGS: dict[str, Any] = {
    "business_name": "Синнамоны",
    "pickup_address": "Худжанд, Гульбахор — выносим к «Додо пицце» у остановки Гульбахор",
    "working_hours": "Понедельник–пятница, выдача с 9:00 до 20:00. Суббота и воскресенье — выходные",
    # 03 §1.3: the bot refuses a slot outside these hours, on a day off, or an order total that is
    # not a whole number of boxes — exactly what the owner answers in Direct.
    "order_hours_start": "09:00",
    "order_hours_end": "20:00",
    "closed_weekdays": [5, 6],
    # Boxes of 4 and of 6 (23.09.2026): every total that adds up from them is an even number from 4
    # up — which is exactly "minimum 4, step 2". The box sizes themselves are named in the FAQ.
    "min_order_quantity": 4,
    "order_quantity_step": 2,
    "min_lead_time_hours": 24,
    "delivery_info_text": (
        "Доставка по Худжанду — 14 сомони, привозит таксист. За город — по договорённости, "
        "стоимость уточнит менеджер. Самовывоз бесплатный: Гульбахор, выносим к «Додо пицце»."
    ),
    "warehouse": {
        "name": "Кухня",
        # The street is not public; the pin below is the area, not the door — the owner moves it in
        # «Настройки → Склад», and only then are the route distances right.
        "address": "Худжанд, Гульбахор (ориентир — остановка Гульбахор)",
        "latitude": 40.2842191,
        "longitude": 69.6191174,
    },
}

#: The payment policy text follows the «Предоплата» switch («Настройки»), so the bot never states a
#: rule it does not apply (22.09.2026: payment is switched off until the bot is ready for real
#: customers). The account number stays out of this file — the bot sends it from the settings.
PAYMENT_TEXT_PREPAID = (
    "Оплата только предоплатой: переводом на наш номер — его бот пришлёт после подтверждения заказа. "
    "После перевода отправьте, пожалуйста, чек (скриншот) — по нему оформляем заказ. "
    "Наличными при получении не принимаем."
)
#: Payment switched off: the bot has no policy to quote, so a payment question goes to the manager.
PAYMENT_TEXT_OFF = ""


def payment_methods_text(prepayment_enabled: bool) -> str:
    return PAYMENT_TEXT_PREPAID if prepayment_enabled else PAYMENT_TEXT_OFF


def seed_products(db: Session, refresh: bool = False) -> tuple[int, int, int]:
    """Adds the per-roll catalog and switches off the boxes of 4 seeded earlier (if untouched).

    ``refresh`` also brings an existing product back to this file — price, description, aliases —
    which is how a new price list reaches the catalog (23.09.2026). Without it the prices a staff
    member typed in the admin panel are never overwritten.
    """
    existing = {product.name: product for product in db.scalars(select(Product))}
    service = ProductService(db)
    created = updated = 0
    for sort_order, data in enumerate(PRODUCTS, start=1):
        product = existing.get(data["name"])
        if product is None:
            service.create({**data, "sort_order": sort_order * 10})
            created += 1
            continue
        changes = {
            key: value
            for key, value in (("price", data["price"]), ("description", data["description"]))
            if getattr(product, key) != value
        }
        if list(product.aliases or []) != data["aliases"]:
            changes["aliases"] = data["aliases"]
        if refresh and changes:
            service.update(product.id, changes)
            updated += 1
    retired = 0
    for name in RETIRED_PRODUCT_NAMES:
        product = existing.get(name)
        if product is not None and product.is_active and product.price == RETIRED_BOX_PRICE:
            service.update(product.id, {"is_active": False})
            retired += 1
    return created, updated, retired


def seed_faq(db: Session, refresh: bool = False) -> tuple[int, int, int]:
    """Adds missing entries and switches off the ones the archive replaced.

    An entry still holding the seeded answer gets the current keywords and Tajik texts; an answer
    edited in the admin panel marks the entry as the owner's and is left alone — unless ``refresh``
    is given, which rewrites every seeded question from this file (the owner's edits are lost).

    The prepayment answers follow the «Предоплата» switch: switched off with it, switched back on
    with it, so the bot never asks for a transfer the owner has turned off (22.09.2026).
    """
    prepayment_enabled = SettingsService(db).get().prepayment_enabled
    existing = {item.question: item for item in db.scalars(select(FaqItem))}
    created = updated = 0
    for sort_order, data in enumerate(FAQ, start=1):
        item = existing.get(data["question"])
        if item is None:
            active = prepayment_enabled or data["question"] not in PREPAYMENT_QUESTIONS
            db.add(FaqItem(**data, is_active=active, sort_order=sort_order * 10))
            created += 1
            continue
        stale = (
            item.keywords != data["keywords"]
            or item.question_tg != data["question_tg"]
            or item.answer_tg != data["answer_tg"]
            or (refresh and item.answer != data["answer"])
        )
        if stale and (refresh or item.answer == data["answer"]):
            item.answer = data["answer"]
            item.keywords = list(data["keywords"])
            item.question_tg, item.answer_tg = data["question_tg"], data["answer_tg"]
            updated += 1
    retired = 0
    for question in RETIRED_QUESTIONS:
        item = existing.get(question)
        if item is not None and item.is_active:
            item.is_active = False
            retired += 1
    for question in PREPAYMENT_QUESTIONS:
        item = existing.get(question)
        if item is not None and item.is_active != prepayment_enabled:
            item.is_active = prepayment_enabled
            retired += not prepayment_enabled
    db.commit()
    return created, updated, retired


def seed_settings(db: Session, refresh: bool = False) -> list[str]:
    """Fills a setting only while it is empty or still at its contract default — a value the owner
    has already chosen in the admin panel is never overwritten.

    ``refresh`` writes every setting of this file anyway (the texts seeded before the archive was
    read are replaced), but never moves the warehouse pin once it has coordinates.

    The payment text is the one exception to "never overwrite": while it still holds one of the two
    texts of this file, it follows the «Предоплата» switch instead of staying as it was.
    """
    service = SettingsService(db)
    current = service.get()
    defaults = BusinessSettings()
    patch: dict[str, Any] = {}
    seeded = {**BUSINESS_SETTINGS, "payment_methods_text": payment_methods_text(current.prepayment_enabled)}
    for key, value in seeded.items():
        if key == "warehouse":
            if current.warehouse.latitude is None or current.warehouse.longitude is None:
                patch[key] = value
            elif refresh:
                patch[key] = {key_: value_ for key_, value_ in value.items() if key_ in ("name", "address")}
            continue
        stored = getattr(current, key)
        ours = key == "payment_methods_text" and stored.strip() in (PAYMENT_TEXT_PREPAID, PAYMENT_TEXT_OFF)
        if refresh or ours or not stored or stored == getattr(defaults, key):
            patch[key] = value
    if patch:
        service.update(patch)
    return sorted(patch)


def main(argv: list[str] | None = None) -> int:
    refresh = "--refresh" in (argv if argv is not None else sys.argv[1:])
    with session_scope() as db:
        products, products_updated, retired_products = seed_products(db, refresh=refresh)
        faq, faq_updated, faq_retired = seed_faq(db, refresh=refresh)
        settings = seed_settings(db, refresh=refresh)
        prepayment_enabled = SettingsService(db).get().prepayment_enabled
    print(
        f"Товаров добавлено: {products}, обновлено: {products_updated} "
        f"(всего в наборе {len(PRODUCTS)}), отключено старых: {retired_products}"
    )
    print(f"Предоплата: {'включена' if prepayment_enabled else 'выключена'} — ответы про оплату следуют за ней")
    print(
        f"Вопросов FAQ добавлено: {faq}, обновлено: {faq_updated}, отключено: {faq_retired} "
        f"(всего в наборе {len(FAQ)})"
    )
    print(f"Настройки заполнены: {', '.join(settings) if settings else 'ничего не менялось'}")
    if not refresh:
        print("Подсказка: --refresh перезаписывает FAQ и настройки из этого набора (правки в панели потеряются).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
