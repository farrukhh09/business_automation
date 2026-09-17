# CLAUDE.md — инструкции для Claude Code в этом проекте

## Что это за проект
Веб-система автоматизации продаж домашней выпечки (Душанбе): Instagram-бот с AI (русский и таджикский), заказы, клиенты, оплата, производство, доставка с оптимизацией маршрута, отчёты, админ-панель. Ручной режим передачи доставок в Maxim.

## С чего начать новую сессию (обязательно)
1. Прочитай **`docs/PROGRESS.md`**. Там текущий этап, принятые решения, что сделано и что делать дальше.
2. Требования заказчика — `docs/SPEC.md`. Не меняй их.
3. Архитектурный контракт — `docs/architecture/01-overview.md` … `07-frontend.md`. Он обязателен: имена таблиц, колонок, enum и эндпоинтов зафиксированы. Если меняешь решение, меняй контракт и код вместе.
4. Исследования внешних API (проверены 2026-09-15) лежат в `docs/research/`: `instagram.md`, `maxim.md`, `voice.md`, `geo.md`.
5. Проверь фактическое состояние файлов и прогони тесты и сборку (команды ниже). Только после этого продолжай работу со следующего незавершённого шага в `docs/PROGRESS.md`.

## Окружение (Windows)
- Shell: Windows PowerShell 5.1. Docker, системного Node и Git нет.
- Python-venv: `backend\.venv\Scripts\python.exe` (Python 3.14; код должен работать и на 3.12). Если venv нет, создай его и выполни `pip install -r backend/requirements-dev.txt`.
- Portable Node.js 24: `.tools\node\`. Перед npm-командами в PowerShell выполни `$env:PATH = "$PWD\.tools\node;" + $env:PATH`. Если папки нет, скачай zip с nodejs.org и распакуй в `.tools\node` (короткий путь, иначе упрёшься в MAX_PATH).
- Git: портативный MinGit с GitHub-релиза `git-for-windows` (MinGit-*-64-bit.zip). Распакуй в `.tools\mingit`, `git.exe` лежит в `.tools\mingit\cmd\`.
- PostgreSQL для локальной проверки. Бинарники берутся из колеса PyPI `pgserver` (`pgserver-0.1.4-cp312-cp312-win_amd64.whl`, 12,8 МБ, PostgreSQL 16.2). Колесо — это zip: распакуй его в **ASCII-путь**, потому что с кириллицей в пути PostgreSQL на Windows ненадёжен. Бинарники — в `pgserver\pginstall\bin`. Запуск:
  1. `initdb -D <data> -U postgres -A trust -E UTF8 --no-locale`
  2. `pg_ctl -D <data> -o "-p 55432 -h 127.0.0.1" -l <log> -w start`
  3. миграции: `alembic -x db_url=postgresql+psycopg://postgres@127.0.0.1:55432/<db> upgrade head`
  4. тесты на PG: переменная `TEST_DATABASE_URL`

  Большой zip EDB не используй: он весит 330 МБ, а пути pgAdmin превышают MAX_PATH.
- `.tools/` не коммитится.

## Команды проверки
```powershell
# backend-тесты (из папки backend)
cd backend; .\.venv\Scripts\python.exe -m pytest -q
# миграции на временной SQLite
.\.venv\Scripts\python.exe -m alembic upgrade head
# frontend (из папки frontend)
$env:PATH = "$PWD\..\.tools\node;" + $env:PATH; npx tsc --noEmit; npm run lint; npm run build
```

## Правила разработки (кратко, подробно — в SPEC §50 и docs/architecture/01)
- Слои: `api/routes` (тонкие) → `services` (бизнес-правила, commit) → `repositories` (запросы) → `models`. Интеграции — адаптеры за протоколами.
- Бизнес-правила пишутся в Python, а не в промпте. LLM не имеет доступа к БД и не подтверждает заказ: подтверждение делает детерминированный `classify_confirmation`.
- В production-коде нельзя имитировать внешние API. Моки допустимы только в `tests/`.
- Никаких секретов в коде: все настройки берутся из `.env` (шаблон — `.env.example`).
- После каждого крупного этапа запускай тесты и исправляй ошибки. Затем обнови `docs/PROGRESS.md` (статус этапа, решения, следующие шаги).
- Тексты для клиентов и UI пиши на русском (клиентам — и на таджикском), код и идентификаторы — на английском.
