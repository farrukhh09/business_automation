"""Talk to the bot from the console, without Instagram (local development only).

Usage (from ``backend/``, with ``LLM_API_KEY`` set in ``.env``)::

    python -m scripts.chat_console            # continue as the same test customer
    python -m scripts.chat_console --new      # start as a brand-new customer

Every line you type goes through the same path as a real Instagram message
(``InboundMessageService.handle_event``): customer and conversation lookup, message storage,
``DialogService`` with the real LLM and geocoder, draft orders. Orders and the conversation are
visible in the admin panel (section «Диалоги»).

Nothing is sent to Instagram. Every outgoing message of the conversation is printed here as soon as
it appears in the database — the bot's replies and **the manager's replies typed in the admin
panel** (checked every ``POLL_SECONDS``). The bot's replies are stored as FAILED with a note, so the
admin panel shows honestly that they never left the system; the manager's replies get the regular
"Instagram не настроен" status from the local API server. Voice messages are not supported.

Commands: ``/метка`` — continue the dialog after placing the pin on the map page (normally the local
API server does it by itself); ``/новый`` — a new test customer; ``/выход`` — quit.
"""

import argparse
import logging
import sys
import threading
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.time import now_utc
from app.models.conversation import Conversation, Message
from app.models.enums import ConversationMode, MessageDirection, MessageSender
from app.services.customer_service import CustomerService
from app.services.dialog_state import DialogState
from app.services.inbound_service import InboundMessageService
from app.services.messaging_service import MessagingService

ACCOUNT_ID = "console"
DEFAULT_CUSTOMER_ID = "console-customer"
CUSTOMER_NAME = "Тестовый клиент (консоль)"
NOT_SENT_NOTE = "Локальная проверка: ответ показан в консоли, в Instagram не отправлялся"
PROMPT = "Вы: "
POLL_SECONDS = 2.0
SENDER_LABELS = {MessageSender.AI: "Бот", MessageSender.OPERATOR: "Менеджер"}


class ConsoleQueue:
    """``TaskQueue`` that keeps the bot's replies for marking instead of sending them."""

    def __init__(self) -> None:
        self.outbox: list[int] = []

    def process_instagram_event(self, event: dict[str, Any]) -> bool:
        return False

    def send_message(self, message_id: int) -> bool:
        self.outbox.append(message_id)
        return True

    def transcribe_voice(self, message_id: int) -> bool:
        return False

    def synthesize_voice_reply(self, message_id: int) -> bool:
        return False

    def continue_after_location(self, order_id: int) -> bool:
        return False


class OutgoingPrinter:
    """Prints new outgoing messages of every console conversation; shared by the input loop and the poller.

    The manager may answer an *earlier* test customer from the admin panel (after ``/новый``), so all
    ``console:*`` conversations are watched; a reply to another customer is labelled with that
    customer's id.
    """

    def __init__(self, conversation_key: str) -> None:
        self.conversation_key = conversation_key
        self.last_id = 0
        self.lock = threading.Lock()
        self.waiting_for_input = False

    def skip_existing(self) -> None:
        self.last_id = self._max_id()

    def flush(self) -> int:
        with self.lock:
            printed = 0
            with session_scope() as db:
                rows = db.execute(
                    select(Message, Conversation.instagram_conversation_id)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(
                        Conversation.instagram_conversation_id.like(f"{ACCOUNT_ID}:%"),
                        Message.direction == MessageDirection.OUTGOING,
                        Message.id > self.last_id,
                    )
                    .order_by(Message.id)
                ).all()
                for message, key in rows:
                    self.last_id = message.id
                    if not message.text:
                        continue
                    label = SENDER_LABELS.get(message.sender, "Бот")
                    if key != self.conversation_key:
                        label = f"{label} → клиенту {key.split(':', 1)[1]}"
                    prefix = "\n" if self.waiting_for_input else ""
                    print(f"{prefix}\n{label}:", message.text.replace("\n", "\n     "), "\n")
                    printed += 1
                if printed and self.waiting_for_input:
                    print(PROMPT, end="", flush=True)
            return printed

    def _max_id(self) -> int:
        with session_scope() as db:
            rows = db.scalars(
                select(Message.id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(Conversation.instagram_conversation_id.like(f"{ACCOUNT_ID}:%"))
                .order_by(Message.id.desc())
                .limit(1)
            ).all()
            return rows[0] if rows else 0


class ConsoleChat:
    def __init__(self, db: Session, customer_id: str) -> None:
        self.db = db
        self.queue = ConsoleQueue()
        self.inbound = InboundMessageService(db, queue=self.queue)
        self.customer_id = customer_id
        CustomerService(db).get_or_create_by_instagram_id(customer_id, name=CUSTOMER_NAME)
        db.commit()
        self.printer = OutgoingPrinter(self.conversation_key)
        self.printer.skip_existing()

    @property
    def conversation_key(self) -> str:
        return f"{ACCOUNT_ID}:{self.customer_id}"

    def conversation(self) -> Conversation | None:
        self.db.expire_all()
        return self.db.scalars(
            select(Conversation).where(Conversation.instagram_conversation_id == self.conversation_key)
        ).one_or_none()

    def send(self, text: str) -> None:
        event = {
            "account_id": ACCOUNT_ID,
            "sender_id": self.customer_id,
            "recipient_id": ACCOUNT_ID,
            "timestamp": now_utc().isoformat(),
            "mid": f"console-{uuid.uuid4().hex}",
            "text": text,
        }
        before = self._mode()
        self.inbound.handle_event(event)
        self._mark_not_sent()
        if not self.printer.flush() and self._mode() == ConversationMode.HUMAN_HANDOFF:
            print("  (бот не отвечает: диалог у менеджера — ответьте в админке или нажмите «Вернуть боту»)")
        self._print_mode_change(before)

    def location_placed(self) -> None:
        conversation = self.conversation()
        state = DialogState.from_json(conversation.state) if conversation is not None else DialogState()
        if state.draft_order_id is None:
            print("  (нет черновика заказа, ждущего метку на карте)")
            return
        before = self._mode()
        self.inbound.continue_after_location(state.draft_order_id)
        self._mark_not_sent()
        if not self.printer.flush():
            print("  (бот ничего не ответил: метка ещё не поставлена или адрес уже подтверждён)")
        self._print_mode_change(before)

    def _mark_not_sent(self) -> None:
        messaging = MessagingService(self.db, settings=self.inbound.settings, media=self.inbound.media)
        for message_id in self.queue.outbox:
            messaging.mark_failed(message_id, NOT_SENT_NOTE)
        self.queue.outbox.clear()

    def _mode(self) -> ConversationMode | None:
        conversation = self.conversation()
        return conversation.mode if conversation is not None else None

    def _print_mode_change(self, before: ConversationMode | None) -> None:
        conversation = self.conversation()
        if conversation is None or before is None or conversation.mode == before:
            return
        if conversation.mode == ConversationMode.HUMAN_HANDOFF:
            reason = conversation.handoff_reason or "без причины"
            print(f"  [диалог передан менеджеру: {reason}. Бот молчит, пока в админке не нажмут «Вернуть боту»]")
        elif conversation.mode == ConversationMode.AI:
            print("  [диалог снова ведёт бот]")


def _poll(holder: dict[str, ConsoleChat], stop: threading.Event) -> None:
    """Shows the manager's replies (and replies produced by the API server) while you type."""
    while not stop.wait(POLL_SECONDS):
        try:
            holder["chat"].printer.flush()
        except Exception as exc:  # a locked SQLite file must not kill the chat
            logging.getLogger(__name__).debug("poll failed: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Консольный чат с ботом (без Instagram)")
    parser.add_argument("--new", action="store_true", help="начать как новый клиент")
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()
    if not settings.is_development:
        print("Скрипт предназначен только для локальной разработки (APP_ENV=development).", file=sys.stderr)
        return 1
    if not settings.LLM_API_KEY.strip():
        print("Не задан LLM_API_KEY в backend/.env — без него бот сразу передаёт диалог менеджеру.", file=sys.stderr)
        return 1

    customer_id = f"console-{uuid.uuid4().hex[:8]}" if args.new else DEFAULT_CUSTOMER_ID
    print("Чат с ботом. Ответы менеджера из админки тоже появятся здесь.")
    print("Команды: /метка — после отметки на карте, /новый — новый клиент, /выход.\n")
    stop = threading.Event()
    with session_scope() as db:
        holder = {"chat": ConsoleChat(db, customer_id)}
        poller = threading.Thread(target=_poll, args=(holder, stop), daemon=True)
        poller.start()
        try:
            while True:
                chat = holder["chat"]
                chat.printer.waiting_for_input = True
                try:
                    text = input(PROMPT).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                finally:
                    chat.printer.waiting_for_input = False
                if not text:
                    continue
                command = text.lower()
                if command in ("/выход", "/exit", "/quit"):
                    break
                if command in ("/новый", "/new"):
                    chat.inbound.close()
                    last_id = chat.printer.last_id
                    holder["chat"] = ConsoleChat(db, f"console-{uuid.uuid4().hex[:8]}")
                    holder["chat"].printer.last_id = last_id  # keep watching the earlier customers too
                    print("  (новый клиент)\n")
                    continue
                if command in ("/метка", "/pin"):
                    chat.location_placed()
                    continue
                chat.send(text)
        finally:
            stop.set()
            holder["chat"].inbound.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
