# Заметки по развёртыванию в production

Документ дополняет README: как запустить систему на сервере безопасно и поддерживать её.
Команды даны для Linux-сервера (bash). Контейнеры описаны в `docker-compose.yml`, переменные — в `.env.example`.

## 1. Состав и требования

| Сервис | Назначение | Порт |
|---|---|---|
| `postgres` | PostgreSQL 16, том `bakery_postgres_data` | только внутренняя сеть |
| `redis` | брокер Celery, кэш, rate limiting (AOF), том `bakery_redis_data` | только внутренняя сеть |
| `backend` | FastAPI; при старте применяет миграции и создаёт первого администратора | `8000` |
| `worker` | Celery worker (сообщения Instagram, STT/TTS, геокодирование, маршруты) | — |
| `beat` | Celery beat (ежедневный отчёт, продление токена Instagram, синхронизация доставок, очистка медиа) | — |
| `frontend` | админ-панель Next.js; проксирует `/api/*` в backend | `3000` |
| `osrm` | опционально, профиль `routing`: матрица времени и расстояний по дорогам | только внутренняя сеть (`5000`) |

- Docker Engine с Docker Compose v2. Для файла `docker-compose.prod.yml` из раздела 3.3 нужен Compose **не ниже 2.24.4** (тег `!override`).
- Ориентир для сервера (оценка, не замер): 2 vCPU, 4 ГБ RAM, 20 ГБ диска; с OSRM добавьте ещё ~1–2 ГБ RAM.
- Время сервера синхронизируется по NTP: от него зависят срок жизни JWT и проверка webhook. Контейнеры работают в UTC, бизнес-время задаёт `BUSINESS_TIMEZONE`.
- Имя проекта Compose зафиксировано (`name: bakery`), поэтому тома называются `bakery_*` независимо от имени каталога.

## 2. Первый запуск

```bash
cp .env.example .env
chmod 600 .env
# заполните .env: обязательно POSTGRES_PASSWORD, JWT_SECRET, APP_SECRET_KEY,
# APP_ENV=production, PUBLIC_BASE_URL, FRONTEND_PUBLIC_URL, CORS_ORIGINS,
# FIRST_ADMIN_PASSWORD (на первый запуск), ключи интеграций
docker compose up -d --build
docker compose ps                     # backend должен стать healthy
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/health/ready   # проверка БД и Redis
```

- На сервере сразу запускайте с файлом `docker-compose.prod.yml` (раздел 3.3): `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` — иначе порты 8000 и 3000 будут открыты на всех интерфейсах.
- Секреты генерируйте так: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
- Первый администратор создаётся при старте backend, если задан `FIRST_ADMIN_PASSWORD`. После входа смените пароль в разделе «Пользователи», очистите `FIRST_ADMIN_PASSWORD` в `.env` и выполните `docker compose up -d`.
- Создать администратора вручную: `docker compose exec backend python -m scripts.create_admin`.
- Изменили `.env` — выполните `docker compose up -d` (контейнеры пересоздаются). `docker compose restart` новые значения **не** подхватывает.
- **Никогда** не запускайте `docker compose down -v` на сервере: флаг `-v` удаляет тома с базой данных.

## 3. HTTPS и reverse proxy

### 3.1 Зачем

- Instagram отправляет webhook только на **публичный HTTPS** с валидным сертификатом (самоподписанные не принимаются), а приложение Meta должно быть в режиме Live.
- Голосовые ответы Instagram скачивает по публичной HTTPS-ссылке `{PUBLIC_BASE_URL}/api/media/...`.
- Клиенты открывают ссылку «Отметьте точку доставки» `{FRONTEND_PUBLIC_URL}/l/{token}` с телефона.
- Токены и пароли администраторов нельзя передавать по HTTP.

### 3.2 Один домен: `/api` → backend, остальное → frontend

Рекомендуемая схема — один домен, например `https://bakery.example.com`:

- `/api/*` проксируется **напрямую в backend** (`127.0.0.1:8000`), минуя Next.js: webhook доходит без лишнего звена, подпись HMAC проверяется по сырому телу запроса;
- всё остальное — во frontend (`127.0.0.1:3000`).

Тогда в `.env`:

```env
APP_ENV=production
PUBLIC_BASE_URL=https://bakery.example.com
FRONTEND_PUBLIC_URL=https://bakery.example.com
CORS_ORIGINS=https://bakery.example.com
```

Если админ-панель и backend на разных доменах (`admin.example.com` и `api.example.com`), то `PUBLIC_BASE_URL` — домен backend, `FRONTEND_PUBLIC_URL` и `CORS_ORIGINS` — домен админ-панели.

Callback URL webhook в Meta App Dashboard: `https://bakery.example.com/api/webhooks/instagram`, Verify Token — значение `INSTAGRAM_VERIFY_TOKEN`. Проверка подписки вручную:

```bash
curl -fsS "https://bakery.example.com/api/webhooks/instagram?hub.mode=subscribe&hub.verify_token=<VERIFY_TOKEN>&hub.challenge=12345"
# ожидается ответ: 12345
```

**Caddy** (сертификат Let's Encrypt выпускается автоматически):

```caddyfile
bakery.example.com {
    encode gzip
    request_body {
        max_size 30MB
    }
    handle /api/* {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        reverse_proxy 127.0.0.1:3000
    }
}
```

**nginx** (сертификат, например, через certbot):

```nginx
server {
    listen 80;
    server_name bakery.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;    # nginx < 1.25.1: уберите эту строку и напишите «listen 443 ssl http2;»
    server_name bakery.example.com;

    ssl_certificate     /etc/letsencrypt/live/bakery.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bakery.example.com/privkey.pem;

    client_max_body_size 30m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        # Перезаписываем, а не дописываем: клиент не должен подделать свой IP (rate limiting).
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Не закрывайте `/api/webhooks/instagram`, `/api/public/*` и `/api/media/*` basic-auth или IP-фильтром: к ним обращаются Meta и клиенты.

### 3.3 Не публиковать порты 8000/3000 в интернет

Uvicorn запущен с `--proxy-headers` и доверяет заголовкам `X-Forwarded-*` только от loopback и частных сетей (`127.0.0.1, ::1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7`): так видны reverse proxy на хосте (через docker-proxy), контейнер frontend и сеть compose. Клиент с публичного IP, подключившийся к порту 8000 напрямую, свой IP подделать не может. Но остаются лазейки: клиент из той же частной сети (LAN, VPC), а также запросы через порт 3000: rewrite-прокси Next.js сам `X-Forwarded-For` не добавляет, а заголовок клиента передаёт в backend как есть. Без reverse proxy перед портом 3000 backend видит IP контейнера frontend — все пользователи админ-панели делят один лимит запросов (вход — 5 в минуту на всех), а клиент может подставить свой заголовок. Поэтому к портам 8000 и 3000 должен обращаться только reverse proxy, который **перезаписывает** `X-Forwarded-For` (Caddy делает это по умолчанию, для nginx — пример выше).

Список доверенных адресов можно заменить переменной `FORWARDED_ALLOW_IPS` в `.env` (через запятую, IP или сети), например если reverse proxy работает на другом сервере с публичным IP.

Docker публикует порты в обход `ufw`/`firewalld`, поэтому одного firewall недостаточно. Привяжите порты к localhost файлом `docker-compose.prod.yml` рядом с `docker-compose.yml`:

```yaml
services:
  backend:
    ports: !override
      - "127.0.0.1:8000:8000"
  frontend:
    ports: !override
      - "127.0.0.1:3000:3000"
```

Запуск на сервере:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Если reverse proxy сам работает в контейнере, подключите его к сети `bakery_default` и проксируйте на `backend:8000` и `frontend:3000`, а `ports` у backend и frontend уберите (`ports: !reset []`).

## 4. Резервное копирование PostgreSQL

В базе персональные данные клиентов (имена, телефоны, адреса): копии шифруйте, храните вне сервера, доступ ограничьте.

**Логический бэкап (основной способ, без остановки):**

```bash
mkdir -p backups
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "backups/bakery-$(date +%F_%H%M).dump"
```

**Ежедневно через cron** (в crontab символ `%` экранируется как `\%`):

```cron
30 3 * * * cd /opt/bakery && docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/bakery-$(date +\%F).dump && find backups -name 'bakery-*.dump' -mtime +14 -delete
```

Затем копируйте `backups/` во внешнее хранилище (S3-совместимое, другой сервер) — например, `rclone` или `restic` с шифрованием.

**Восстановление:**

```bash
docker compose stop backend worker beat
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < backups/bakery-2026-09-15.dump
docker compose start backend worker beat
```

**Копия тома целиком** (только при остановленном postgres, та же мажорная версия PostgreSQL):

```bash
docker compose stop postgres
docker run --rm -v bakery_postgres_data:/data:ro -v "$PWD/backups:/backup" alpine \
  tar czf "/backup/pgdata-$(date +%F).tar.gz" -C /data .
docker compose start postgres
```

- Раз в месяц проверяйте восстановление на отдельной машине: непроверенный бэкап — не бэкап.
- Делайте бэкап перед каждым обновлением (миграции применяются автоматически при старте backend).
- Том `bakery_media` содержит только аудио голосовых ответов, их удаляют через 7 дней — бэкап не обязателен.
- Том `bakery_redis_data` — очередь задач и кэш; бэкап не нужен, но при его потере пропадут задачи, которые ещё не выполнены.

## 5. OSRM: подготовка данных (профиль `routing`)

Без OSRM маршрут строится по оценке «по прямой × 1.3» (haversine). OSRM даёт реальные время и расстояния по дорогам OpenStreetMap (данные ODbL, атрибуция OSM обязательна).

### 5.1 Подготовка

Шаги подготовки выполняются **тем же образом**, что и сервис `osrm` (через `docker compose run`): формат файлов `.osrm*` зависит от версии OSRM.

```bash
mkdir -p data/osrm
curl -fL -o data/osrm/tajikistan-latest.osm.pbf \
  https://download.geofabrik.de/asia/tajikistan-latest.osm.pbf

docker compose --profile routing run --rm --no-deps osrm \
  osrm-extract -p /opt/car.lua /data/tajikistan-latest.osm.pbf
docker compose --profile routing run --rm --no-deps osrm \
  osrm-partition /data/tajikistan-latest.osrm
docker compose --profile routing run --rm --no-deps osrm \
  osrm-customize /data/tajikistan-latest.osrm

docker compose --profile routing up -d osrm
```

То же без Compose (из каталога `data/osrm`), как в `docs/research/geo.md`:

```bash
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend osrm-extract -p /opt/car.lua /data/tajikistan-latest.osm.pbf
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend osrm-partition /data/tajikistan-latest.osrm
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend osrm-customize /data/tajikistan-latest.osrm
```

На Windows (PowerShell) файл можно скачать так: `Invoke-WebRequest -Uri https://download.geofabrik.de/asia/tajikistan-latest.osm.pbf -OutFile data\osrm\tajikistan-latest.osm.pbf`.

Выгрузка Таджикистана — около 46 МБ (Geofabrik, 2026-09-14).

### 5.2 Подключение

```env
OSRM_URL=http://osrm:5000
```

```bash
docker compose --profile routing up -d      # пересоздать backend/worker с новым .env
docker compose exec backend python -c "import httpx; r = httpx.get('http://osrm:5000/table/v1/driving/68.7739,38.5600;68.7390,38.5615?annotations=duration,distance', timeout=10); print(r.status_code, r.json()['code'])"
# ожидается: 200 Ok
```

- Чтобы не писать `--profile routing` в каждой команде, добавьте в `.env` строку `COMPOSE_PROFILES=routing`.
- Статус видно в админ-панели: «Настройки» → статус интеграций (`routing`).
- Если OSRM недоступен, backend переключается на haversine и пишет в лог событие `routing.fallback`.
- `osrm-routed` по умолчанию принимает в матрицу до 100 точек (по issue OSRM и сторонним источникам). Если доставок в день больше 99, добавьте `--max-table-size` в `command` сервиса `osrm`.
- Публичный демо-сервер OSRM для коммерческого использования не предназначен (не более 1 запроса/с, без гарантий) — используйте только self-hosted.

### 5.3 Обновление карты (раз в неделю)

```bash
cd /opt/bakery
curl -fL -o data/osrm/tajikistan-latest.osm.pbf https://download.geofabrik.de/asia/tajikistan-latest.osm.pbf
docker compose --profile routing stop osrm        # на время пересборки backend использует haversine
docker compose --profile routing run --rm --no-deps osrm osrm-extract -p /opt/car.lua /data/tajikistan-latest.osm.pbf
docker compose --profile routing run --rm --no-deps osrm osrm-partition /data/tajikistan-latest.osrm
docker compose --profile routing run --rm --no-deps osrm osrm-customize /data/tajikistan-latest.osrm
docker compose --profile routing up -d osrm
```

- После обновления образа OSRM (`docker compose pull osrm`) обязательно заново подготовьте данные — иначе `osrm-routed` не запустится на старых файлах.
- Для предсказуемости закрепите версию образа в `docker-compose.yml` (например, `ghcr.io/project-osrm/osrm-backend:<тег>`; тег проверьте на странице пакета ghcr.io).
- Сверяйте маршруты с опытом курьеров: в OSM Душанбе могут отсутствовать одностороннее движение и закрытые дворы.

## 6. Логи

- Все сервисы пишут в stdout/stderr, backend — JSON-строки с полем `event` (`order.status_changed`, `ai.request`, `task.*`, `routing.fallback` и т.д.). Фильтр в backend маскирует пароли, токены и ключи.
- Ротация настроена в `docker-compose.yml`: драйвер `json-file`, до 5 файлов по 10 МБ на контейнер.

```bash
docker compose logs -f --tail=200 backend worker beat
docker compose logs --since 1h worker | grep 'task.'
docker compose logs backend | grep 'routing.fallback'
```

**Централизованный сбор** (рекомендуется для production): Vector, Fluent Bit или Promtail читают `/var/lib/docker/containers/*/*-json.log`, разбирают JSON и отправляют в Loki / Elasticsearch / облачный сервис. Рекомендации:

- храните 30–90 дней, доступ к логам только у администраторов: в них есть id клиентов и заказов;
- `LOG_LEVEL=INFO`; `DEBUG` — только временно для диагностики;
- настройте оповещения:
  - `/api/health/ready` отвечает 503 (внешний uptime-мониторинг);
  - ошибки задач `task.*`, особенно `send_instagram_message` и `refresh_instagram_token`;
  - всплеск ответов 502 `integration_error` и 503 `integration_not_configured`;
  - частые `routing.fallback`;
  - контейнер в состоянии `unhealthy` или `restarting` (`docker compose ps`).

## 7. Токен Instagram

- Токен из Meta App Dashboard долгоживущий — **60 дней**. Продлить можно только токен, которому **больше 24 часов** и который **ещё не истёк**; продлённый действует 60 дней с момента продления.
- Задача beat `refresh_instagram_token` раз в сутки продлевает токен и сохраняет новый в `app_settings.instagram_token`; backend использует его вместо `INSTAGRAM_ACCESS_TOKEN` из `.env`, если он свежее. В лог пишется только факт обновления, не значение.
- Поэтому `worker` и `beat` должны работать постоянно. Если система была остановлена дольше срока жизни токена, он истечёт — нужен ручной выпуск.
- Проверяйте статус: «Настройки» → статус интеграций (`instagram`) и логи worker по задаче `refresh_instagram_token`.

**Выпуск нового токена вручную** (истёк, отозван или сменили аккаунт):

1. Meta App Dashboard → приложение → Instagram → «API setup with Instagram business login» → «Generate token» напротив аккаунта → войти в Instagram → скопировать токен.
2. Вставить значение в `.env` в `INSTAGRAM_ACCESS_TOKEN=` (токен не пересылать в мессенджерах и не сохранять в заметках).
3. Пересоздать контейнеры: `docker compose up -d backend worker beat`.
4. Проверить токен, не оставляя его в истории shell:

```bash
read -rs IG_TOKEN
curl -fsS -H "Authorization: Bearer $IG_TOKEN" "https://graph.instagram.com/v25.0/me?fields=user_id,username"
unset IG_TOKEN
```

   В ответе `user_id` должен совпадать с `INSTAGRAM_ACCOUNT_ID`.
5. Отправить тестовое сообщение в Direct и убедиться, что в разделе «Диалоги» оно появилось и бот ответил.

Если утёк App Secret — сбросьте его в Dashboard и обновите `INSTAGRAM_APP_SECRET` (и `META_APP_SECRET`, если используется). Сменили `INSTAGRAM_VERIFY_TOKEN` — обновите его и в настройках webhook в Dashboard.

## 8. Обновление приложения

```bash
cd /opt/bakery
# 1) бэкап БД (раздел 4)
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps
curl -fsS https://bakery.example.com/api/health/ready
```

- Миграции применяются автоматически при старте backend (до 30 попыток с ожиданием БД). Вручную: `docker compose run --rm backend migrate`.
- `worker` и `beat` стартуют только после того, как backend стал healthy, то есть после миграций.
- Изменения `NEXT_PUBLIC_APP_NAME` и адреса backend для rewrites встраиваются при сборке frontend — нужен `--build`.

## 9. Чек-лист безопасности

- [ ] `APP_ENV=production` (отключает `/api/docs`, делает подпись webhook обязательной).
- [ ] `POSTGRES_PASSWORD`, `JWT_SECRET`, `APP_SECRET_KEY`, `INSTAGRAM_VERIFY_TOKEN` — случайные и длинные, нигде не повторяются.
- [ ] `.env` с правами `600`, не в Git, не в бэкапах в открытом виде.
- [ ] Порты 8000 и 3000 привязаны к `127.0.0.1` (раздел 3.3); PostgreSQL, Redis и OSRM не опубликованы.
- [ ] Только HTTPS; HTTP перенаправляется на HTTPS.
- [ ] `FIRST_ADMIN_PASSWORD` очищен после первого входа, пароль администратора сменён.
- [ ] Ключ `MAPS_API_KEY` (если используется) ограничен IP сервера и нужными API.
- [ ] `NOMINATIM_USER_AGENT` содержит реальный контакт.
- [ ] Бэкапы БД ежедневные, шифрованные, хранятся вне сервера, восстановление проверено.
- [ ] Логи собираются, оповещения настроены.
- [ ] Регулярно обновляются ОС сервера и базовые образы: `docker compose pull`, затем `up -d --build`.
