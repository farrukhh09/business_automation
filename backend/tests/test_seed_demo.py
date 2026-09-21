"""Demo data of ``scripts.seed_demo`` / ``scripts.faq_data`` stays valid for the admin API and idempotent."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.text_normalize import normalize_fold
from app.models.faq import FaqItem
from app.models.product import Product
from app.schemas.faq import FaqCreate
from scripts.faq_data import FAQ, RETIRED_QUESTIONS
from scripts.seed_demo import PRODUCTS, RETIRED_PRODUCT_NAMES, seed_faq, seed_products


def test_faq_entries_are_valid_and_bilingual() -> None:
    questions = [entry["question"] for entry in FAQ]
    assert len(questions) == len(set(questions))
    for entry in FAQ:
        FaqCreate.model_validate(entry)  # what the admin panel would accept
        assert entry["question_tg"] and entry["answer_tg"]
        assert entry["keywords"] and all(normalize_fold(keyword) for keyword in entry["keywords"])


def test_seed_is_idempotent_and_refreshes_untouched_faq(db: Session) -> None:
    assert seed_products(db) == (len(PRODUCTS), 0)  # (created, retired)
    assert seed_faq(db) == (len(FAQ), 0, 0)  # (created, updated, retired)
    assert seed_products(db) == (0, 0)
    assert seed_faq(db) == (0, 0, 0)
    assert db.scalar(select(func.count()).select_from(Product)) == len(PRODUCTS)

    seeded = db.scalars(select(FaqItem).where(FaqItem.question == FAQ[0]["question"])).one()
    seeded.keywords = ["old"]
    edited = db.scalars(select(FaqItem).where(FaqItem.question == FAQ[1]["question"])).one()
    edited.answer, edited.keywords = "Ответ владельца", ["owner"]
    db.commit()

    assert seed_faq(db) == (0, 1, 0)
    assert seeded.keywords == FAQ[0]["keywords"]
    assert edited.keywords == ["owner"]  # an answer edited in the panel is the owner's


def test_refresh_rewrites_an_edited_answer(db: Session) -> None:
    """``--refresh`` is the documented way back to this file's texts (the owner's edits are lost)."""
    seed_faq(db)
    edited = db.scalars(select(FaqItem).where(FaqItem.question == FAQ[1]["question"])).one()
    edited.answer = "Ответ владельца"
    db.commit()

    assert seed_faq(db, refresh=True) == (0, 1, 0)
    assert edited.answer == FAQ[1]["answer"]


def test_products_are_priced_per_piece(db: Session) -> None:
    """The bakery quotes per roll and sums a box from what is inside it (seed_demo docstring)."""
    assert {product["unit"] for product in PRODUCTS} == {"шт."}
    assert all(product["price"] > 0 for product in PRODUCTS)


def test_no_answer_offers_a_retired_product() -> None:
    """A retired item must leave the FAQ too.

    The entry «Можно ли собрать разные вкусы в одной коробочке?» went on selling the assorted box
    «Палитра вкуса» after the catalog moved to per-roll prices, because only the question it
    replaced was retired. The trade name in guillemets is what identifies such an item.
    """
    assert not {entry["question"] for entry in FAQ} & set(RETIRED_QUESTIONS)
    trade_names = [
        normalize_fold(name.split("«")[1].rstrip("»")) for name in RETIRED_PRODUCT_NAMES if "«" in name
    ]
    assert trade_names, "у снятых товаров пропали фирменные названия — тест перестал что-либо проверять"
    for entry in FAQ:
        answers = normalize_fold(f"{entry['answer']} {entry['answer_tg']}")
        for trade_name in trade_names:
            assert trade_name not in answers, f"{entry['question']}: снятый товар «{trade_name}»"
