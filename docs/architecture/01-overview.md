# 01. Обзор архитектуры и соглашения

Этот набор документов (`docs/architecture/*.md`) — **обязательный контракт** для всех, кто пишет код проекта. Требования заказчика — `docs/SPEC.md`. Результаты исследования внешних API — `docs/research/*.md`.

Если код расходится с контрактом — исправляется код, либо (осознанно) контракт + код одновременно.

## 1. Стек (зафиксировано)

| Слой | Решение |
|---|---|
| Backend | Python ≥3.12 (код совместим с 3.12–3.14), FastAPI, Pydantic v2, pydantic-settings |
| ORM | SQLAlchemy 2.x, **синхронный** ORM (`Session`), стиль `Mapped[]`/`mapped_column` |
| БД | PostgreSQL 16+ (драйвер `psycopg` v3, URL `postgresql+psycopg://...`); тесты — SQLite in-memory |
| Миграции | Alembic (`backend/alembic/`) |
| Фоновые задачи | Celery 5 + Redis (broker + result backend), Celery beat для расписания |
| Auth | JWT (PyJWT, HS256): access (30 мин) + refresh (14 дней, ротация, хранение jti в БД); пароли — `pwdlib` Argon2 |
| Rate limiting | `slowapi` (storage: Redis в prod, memory в тестах) |
| HTTP-клиент интеграций | `httpx` (sync) |
| LLM | Anthropic Python SDK (`anthropic` 1.x), модель по умолчанию `claude-opus-5` (env `LLM_MODEL`); альтернатива — Google Gemini через REST (`LLM_PROVIDER=gemini`, 05 §2) |
| Frontend | Next.js (App Router) + React + TypeScript strict + Tailwind CSS, `@tanstack/react-query`, `recharts`, `react-leaflet` |
| Контейнеры | Docker Compose: `backend`, `worker`, `beat`, `frontend`, `postgres`, `redis` |

Почему синхронный SQLAlchemy: нагрузка малого бизнеса невелика, Celery-задачи синхронные, а sync-сессии исключают целый класс ошибок (lazy load в async). FastAPI выполняет `def`-эндпоинты в threadpool.

## 2. Структура репозитория

```text
.
├── backend/
│   ├── app/
│   │   ├── main.py                 # create_app(): middleware, routers, exception handlers
│   │   ├── core/
│   │   │   ├── config.py           # Settings (pydantic-settings), get_settings()
│   │   │   ├── database.py         # engine, SessionLocal, get_db(), session_scope()
│   │   │   ├── logging.py          # JSON-логгер, фильтр секретов, get_logger()
│   │   │   ├── security.py         # hash/verify password, JWT create/decode
│   │   │   ├── rate_limit.py       # limiter (slowapi)
│   │   │   ├── time.py             # now_utc(), business_today(), BUSINESS_TZ helpers
│   │   │   └── exceptions.py       # доменные исключения → HTTP
│   │   ├── models/                 # ORM-модели (по модулю на агрегат) + enums.py
│   │   ├── schemas/                # Pydantic-схемы API (по модулю на домен) + common.py
│   │   ├── repositories/           # доступ к данным: запросы, без commit
│   │   ├── services/               # бизнес-логика, транзакции (commit)
│   │   ├── api/
│   │   │   ├── deps.py             # get_db, get_current_user, require_roles
│   │   │   ├── router.py           # api_router: подключает все роутеры под /api
│   │   │   └── routes/             # auth.py, users.py, customers.py, products.py, orders.py,
│   │   │                           # production.py, statistics.py, reports.py, deliveries.py,
│   │   │                           # faq.py, conversations.py, settings.py, webhooks.py, media.py, health.py
│   │   ├── ai/                     # LLM-клиент, промпты, схемы понимания, tools, confirmation, guard
│   │   ├── integrations/
│   │   │   ├── instagram/          # webhook parsing/signature, Graph API client
│   │   │   ├── maps/               # geocoders, distance matrix providers
│   │   │   ├── speech/             # STT / TTS adapters
│   │   │   └── maxim/              # MaximIntegration interface + implementations
│   │   └── tasks/                  # celery_app.py + задачи
│   ├── alembic/ + alembic.ini
│   ├── scripts/                    # create_admin.py, seed_demo.py
│   ├── tests/                      # pytest
│   ├── Dockerfile
│   ├── requirements.txt            # prod, закреплённые версии
│   ├── requirements-dev.txt        # -r requirements.txt + pytest, respx, ...
│   └── pyproject.toml              # настройки pytest/ruff
├── frontend/                       # Next.js (app/, components/, services/, types/, lib/)
├── android-automation/             # (не MVP) только README с архитектурой и условиями
├── docs/
├── docker-compose.yml
├── .env.example
└── README.md
```

## 3. Слои и правила зависимостей

```text
api/routes  →  services  →  repositories  →  models
     │             │
     │             ├──→ integrations (через интерфейсы/протоколы, получаемые фабриками)
     │             └──→ ai (только dialog/inbound сервисы)
     └──→ schemas
tasks  →  services
ai     →  (read-only) services через ToolRegistry; никогда не импортирует repositories/models для записи
```

1. **Роутеры тонкие**: разбор запроса, проверка роли, вызов сервиса, возврат схемы. Никакой бизнес-логики и прямых запросов к БД.
2. **Сервисы** содержат бизнес-правила, делают `session.commit()` (одна транзакция на операцию). Конструктор: `Service(db: Session, ...зависимости)`.
3. **Репозитории** — только запросы (`select`, `add`, `flush`). Не делают commit. Класс на агрегат: `OrderRepository(db)`.
4. **Интеграции** — адаптеры за протоколами (`typing.Protocol` / `abc.ABC`). Бизнес-логика не знает конкретного провайдера. Выбор реализации — фабрика по настройкам (`get_geocoder(settings)` и т.п.).
5. **AI не имеет доступа к БД**: получает данные через `ToolRegistry` (read-only функции поверх сервисов) и возвращает структурированный результат; все записи выполняет backend после проверок (см. `05-ai.md`).
6. **Запрещено** имитировать внешние API в production-коде. Если провайдер не настроен — адаптер бросает `IntegrationNotConfiguredError`, система деградирует штатно (например, передаёт диалог оператору, помечает адрес как требующий ручной проверки). Моки внешних API — только в `tests/` (respx / фейковые реализации протоколов в тестах).

## 4. Кодовые соглашения (backend)

- Идентификаторы и код — на английском; тексты для клиентов и UI — на русском (и таджикском для клиентских ответов).
- Типизация везде; `from __future__ import annotations` не требуется.
- Enums: `class X(StrEnum)` в `app/models/enums.py`; в БД — `sqlalchemy.Enum(X, native_enum=False, length=32, validate_strings=True)` (VARCHAR + CHECK → переносимо на SQLite).
- Деньги: `Numeric(12, 2)` ↔ `Decimal`. Округление `ROUND_HALF_UP` до 2 знаков. В JSON API деньги отдаются **числом** (см. `04-api.md`, тип `Money`).
- Валюта: код `TJS`, подпись в текстах — «сомони».
- Время: все `DateTime(timezone=True)` хранятся в UTC (`now_utc()`). Бизнес-дата/время (дата и время доставки) — `Date` и `Time` в локальном времени бизнеса `BUSINESS_TIMEZONE=Asia/Dushanbe`. «Сегодня», «вчера», периоды статистики, ежедневный отчёт — в бизнес-таймзоне (`app/core/time.py`). В Windows нужен пакет `tzdata`.
- Первичные ключи — `Integer` autoincrement.
- `created_at`/`updated_at` — через `TimestampMixin` (`server_default=func.now()`, `onupdate=func.now()`), значения UTC.
- Доменные ошибки — исключения из `app/core/exceptions.py` (`NotFoundError`, `ValidationError`/`BusinessRuleError`, `ConflictError`, `PermissionDeniedError`, `IntegrationError`, `IntegrationNotConfiguredError`), глобальный handler переводит их в HTTP (404/422/409/403/502/503) с телом `{"detail": str, "code": str}`.
- Логи: `logger = get_logger(__name__)`; события пишутся с полями `event=...` (например `order.status_changed`), без секретов и паролей (фильтр маскирует ключи `password`, `token`, `secret`, `api_key`, `authorization`, `access_token`, `refresh_token`).
- Никаких секретов в коде. Все настройки — `Settings` из переменных окружения (`.env`).

## 5. Тестирование (соглашения)

- `pytest` из `backend/`: `backend/.venv/Scripts/python -m pytest` (Windows) / `pytest` в контейнере.
- `tests/conftest.py`: SQLite in-memory (`StaticPool`), `Base.metadata.create_all`, фикстуры `db`, `client` (TestClient с override `get_db`), `admin_user`, `operator_user`, `admin_headers`, `operator_headers`, фабрики `make_product`, `make_customer`, `make_order`.
- Celery в тестах: `task_always_eager=True`.
- Внешние HTTP — только через `respx` или фейковые реализации протоколов **внутри tests/**.
- Опционально `TEST_DATABASE_URL=postgresql+psycopg://...` — те же тесты на PostgreSQL.
- Тестовые файлы именуются по домену: `test_orders.py`, `test_customers.py`, `test_confirmation.py`, `test_route_optimizer.py`, ... Разные домены — разные файлы (параллельная разработка без конфликтов).
