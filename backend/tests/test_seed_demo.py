"""Demo data of ``scripts.seed_demo`` / ``scripts.faq_data`` stays valid for the admin API and idempotent."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.text_normalize import normalize_fold
from app.models.faq import FaqItem
from app.models.product import Product
from app.schemas.faq import FaqCreate
from scripts.faq_data import FAQ
from scripts.seed_demo import PRODUCTS, seed_faq, seed_products


def test_faq_entries_are_valid_and_bilingual() -> None:
    questions = [entry["question"] for entry in FAQ]
    assert len(questions) == len(set(questions))
    for entry in FAQ:
        FaqCreate.model_validate(entry)  # what the admin panel would accept
        assert entry["question_tg"] and entry["answer_tg"]
        assert entry["keywords"] and all(normalize_fold(keyword) for keyword in entry["keywords"])


def test_seed_is_idempotent_and_refreshes_untouched_faq(db: Session) -> None:
    assert seed_products(db) == len(PRODUCTS)
    assert seed_faq(db) == (len(FAQ), 0)
    assert seed_products(db) == 0
    assert seed_faq(db) == (0, 0)
    assert db.scalar(select(func.count()).select_from(Product)) == len(PRODUCTS)

    seeded = db.scalars(select(FaqItem).where(FaqItem.question == FAQ[0]["question"])).one()
    seeded.keywords = ["old"]
    edited = db.scalars(select(FaqItem).where(FaqItem.question == FAQ[1]["question"])).one()
    edited.answer, edited.keywords = "Ответ владельца", ["owner"]
    db.commit()

    assert seed_faq(db) == (0, 1)
    assert seeded.keywords == FAQ[0]["keywords"]
    assert edited.keywords == ["owner"]
