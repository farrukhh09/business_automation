# Frontend — админ-панель (Next.js)

Контракт: `docs/architecture/07-frontend.md`, API — `docs/architecture/04-api.md`.

## Команды

```bash
npm install          # зависимости
npm run dev          # http://localhost:3000, /api/* проксируется на BACKEND_INTERNAL_URL
npm run lint         # ESLint
npm run typecheck    # next typegen && tsc --noEmit
npm run build        # production-сборка (output: standalone)
```

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `BACKEND_INTERNAL_URL` | куда проксировать `/api/*` (читается при сборке) | `http://localhost:8000` |
| `NEXT_PUBLIC_APP_NAME` | название в интерфейсе | `Домашняя выпечка` |

## Docker

```bash
docker build --build-arg BACKEND_INTERNAL_URL=http://backend:8000 -t bakery-frontend .
docker run -p 3000:3000 bakery-frontend
```

## Структура

- `app/` — маршруты (App Router): `login`, `l/[token]` (публичная карта), `(panel)/*` (защищённые разделы).
- `components/ui` — базовые компоненты; `components/layout` — Sidebar, Topbar, RoleGate.
- `services/http.ts` — fetch-обёртка (Bearer, авто-refresh, `ApiError`); `services/api.ts` — функции всех эндпоинтов.
- `types/api.ts` — типы 1:1 со схемами API; `lib/` — форматирование, подписи enum, авторизация, React Query.
