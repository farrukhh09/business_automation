"""Payment receipts read by the bot (02-data-model.md: payment_receipts; 03-business-rules.md §3)."""

from sqlalchemy import or_, select

from app.models.receipt import PaymentReceipt
from app.repositories.base import BaseRepository


class ReceiptRepository(BaseRepository[PaymentReceipt]):
    model = PaymentReceipt
    not_found_detail = "Чек не найден"

    def find_earlier(
        self, *, file_sha256: str, reference: str | None, fingerprint: str | None
    ) -> PaymentReceipt | None:
        """The first stored receipt with the same file, the same transaction number or the same transfer."""
        keys = [PaymentReceipt.file_sha256 == file_sha256]
        if reference:
            keys.append(PaymentReceipt.reference == reference)
        if fingerprint:
            keys.append(PaymentReceipt.fingerprint == fingerprint)
        return self.db.scalar(select(PaymentReceipt).where(or_(*keys)).order_by(PaymentReceipt.id).limit(1))

    def for_order(self, order_id: int) -> list[PaymentReceipt]:
        return list(
            self.db.scalars(
                select(PaymentReceipt).where(PaymentReceipt.order_id == order_id).order_by(PaymentReceipt.id)
            )
        )
