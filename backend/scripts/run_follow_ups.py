"""One pass of the follow-up questions (03-business-rules.md §6a), without Celery.

In production the pass is started by Celery beat every 15 minutes (``app.tasks.follow_up``). While
testing locally there is usually no broker and no beat, so this script does the same thing once::

    python -m scripts.run_follow_ups          # send what is due
    python -m scripts.run_follow_ups --dry    # only show what would be sent, writing nothing

Without ``--dry`` it writes real messages into the dialogs and hands them to the usual queue; with no
broker running the message stays ``PENDING`` and the admin panel shows it as not sent.
"""

import sys

from app.core.database import session_scope
from app.services.follow_up_service import run_follow_ups

STAGE_LABELS = {
    "draft": "заказ не дособран",
    "confirmation": "ждём подтверждения",
    "receipt": "ждём чек",
}


def main(argv: list[str] | None = None) -> int:
    dry = "--dry" in (argv if argv is not None else sys.argv[1:])
    with session_scope() as db:
        result = run_follow_ups(db, dry_run=dry)
    title = "Отправили бы" if dry else "Отправлено"
    print(f"{title} догоняющих вопросов: {len(result.planned)} (рассмотрено диалогов: {result.considered})")
    for conversation_id, stage in result.planned:
        print(f"  диалог #{conversation_id}: {STAGE_LABELS.get(stage, stage)}")
    if result.skipped:
        print("Пропущено: " + ", ".join(f"{reason} — {count}" for reason, count in sorted(result.skipped.items())))
    if dry:
        print("Это был пробный запуск (--dry): ничего не сохранено и не отправлено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
