"""Instagram webhook routes — docs/architecture/04-api.md §13, 06-integrations.md §1. PUBLIC, 600/min.

- ``GET  /api/webhooks/instagram``  subscription check: ``hub.mode=subscribe`` and
  ``hub.verify_token == INSTAGRAM_VERIFY_TOKEN`` → ``hub.challenge`` as text/plain, otherwise 403;
- ``POST /api/webhooks/instagram``  ``X-Hub-Signature-256`` over the raw body → one Celery task per
  customer message (and per business reply, for test mode — 06 §1a) → 200 at once. No heavy work
  here (06 §5).

Signature secrets are ``INSTAGRAM_APP_SECRET`` / ``META_APP_SECRET``. Without any secret the webhook
answers 503 — except with ``APP_ENV=development``, where the check is skipped with a warning (06 §1).
A broker failure answers 502 so that Meta delivers the batch again (re-delivered ``mid`` are ignored).
"""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    BadRequestError,
    IntegrationError,
    IntegrationNotConfiguredError,
    PermissionDeniedError,
)
from app.core.logging import get_logger, log_event
from app.core.rate_limit import WEBHOOK_LIMIT, limiter
from app.integrations.instagram.webhook import (
    SIGNATURE_HEADER,
    parse_webhook,
    signature_secrets,
    verify_signature,
    verify_subscription,
)
from app.services.task_queue import TaskQueue, get_task_queue

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

AppSettings = Annotated[Settings, Depends(get_settings)]
Queue = Annotated[TaskQueue, Depends(get_task_queue)]

#: Meta batches up to 1000 updates; a body larger than this is not a webhook.
MAX_WEBHOOK_BYTES = 5 * 1024 * 1024


@router.get("/instagram", response_class=PlainTextResponse, summary="Проверка подписки Instagram webhook")
@limiter.limit(WEBHOOK_LIMIT)
def verify_instagram_webhook(request: Request, settings: AppSettings) -> PlainTextResponse:
    params = request.query_params
    challenge = verify_subscription(
        params.get("hub.mode"), params.get("hub.verify_token"), params.get("hub.challenge"), settings
    )
    if challenge is None:
        raise PermissionDeniedError("Проверка подписки webhook не пройдена")
    return PlainTextResponse(challenge)


@router.post("/instagram", summary="События Instagram (сообщения клиентов)")
@limiter.limit(WEBHOOK_LIMIT)
async def receive_instagram_webhook(request: Request, settings: AppSettings, queue: Queue) -> dict[str, Any]:
    raw_body = await request.body()
    if len(raw_body) > MAX_WEBHOOK_BYTES:
        raise BadRequestError("Слишком большое тело webhook")

    secrets = signature_secrets(settings)
    if not secrets:
        if not settings.is_development:
            log_event(logger, "instagram.webhook_rejected", level=logging.ERROR, reason="secret_not_configured")
            raise IntegrationNotConfiguredError("Проверка подписи webhook не настроена: задайте INSTAGRAM_APP_SECRET")
        log_event(logger, "instagram.webhook_signature_skipped", level=logging.WARNING, app_env=settings.APP_ENV)
    elif not verify_signature(raw_body, request.headers.get(SIGNATURE_HEADER), secrets):
        log_event(logger, "instagram.webhook_rejected", level=logging.WARNING, reason="invalid_signature")
        raise PermissionDeniedError("Неверная подпись webhook")

    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise BadRequestError("Тело webhook не является JSON") from exc

    # A business reply (the manager answering in the Instagram app) goes on too: in test mode it is
    # recorded next to the bot's answer (06 §1a); otherwise the task drops it at once.
    events = [
        event
        for event in parse_webhook(payload)
        if (event.is_customer_message or event.is_business_reply) and not event.is_deleted
    ]
    queued = await run_in_threadpool(_enqueue, queue, [event.model_dump(mode="json") for event in events])
    log_event(logger, "instagram.webhook_received", events=len(events), queued=queued)
    if queued < len(events):
        raise IntegrationError("Очередь задач недоступна — повторите доставку webhook")
    return {"status": "ok", "events": len(events)}


def _enqueue(queue: TaskQueue, events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if queue.process_instagram_event(event))
