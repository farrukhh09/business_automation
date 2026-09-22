# 06. Внешние интеграции, фоновые задачи, окружение

Детали API — в `docs/research/*.md` (проверено 2026-09-15). Все адаптеры — за протоколами; моки только в `tests/`.

## 1. Instagram (`app/integrations/instagram/`)

Вариант: **Instagram API with Instagram Login** (`graph.instagram.com`, Instagram User access token, без Facebook Page). См. `docs/research/instagram.md`.

- `webhook.py`
  - `verify_subscription(mode, token, challenge, settings) -> str | None`.
  - `verify_signature(raw_body: bytes, header: str | None, secrets: list[str]) -> bool` — `X-Hub-Signature-256: sha256=<hex>` = HMAC-SHA256(raw body), `hmac.compare_digest`. Секреты: `INSTAGRAM_APP_SECRET` (+ опционально `META_APP_SECRET` — какой секрет применяется, в документации не подтверждено, поэтому проверяем по списку). Пустой список секретов в production → 503, в `APP_ENV=development` допускается пропуск проверки с WARNING-логом.
  - `parse_webhook(payload) -> list[InstagramEvent]`: `object == "instagram"`, `entry[].messaging[]`; `InstagramEvent{account_id (entry.id), sender_id, recipient_id, timestamp, mid, text, attachments: [{type, url}], is_echo, is_deleted, is_self, reply_to_mid}`. Пропускать `is_echo`, `is_self`, `read`, `reaction`, события без `message`. Типы вложений: `audio` → VOICE, `image` → IMAGE, прочие (`video`, `file`, `share`, `ig_reel`, `story_mention`, ...) → TEXT с пометкой `[вложение: type]` (story_mention: медиа не сохранять).
- `client.py` — `InstagramClient(settings, http: httpx.Client)`:
  - `send_text(recipient_id, text) -> str (message_id)` → `POST {INSTAGRAM_GRAPH_URL}/{INSTAGRAM_API_VERSION}/{INSTAGRAM_ACCOUNT_ID}/messages`, `Authorization: Bearer {INSTAGRAM_ACCESS_TOKEN}`, `{"recipient": {"id": ...}, "message": {"text": ...}}`. Текст > 1000 байт UTF-8 → разбиение на части (по абзацам/предложениям).
  - `send_audio(recipient_id, public_url)` → `{"message": {"attachment": {"type": "audio", "payload": {"url": ...}}}}` (форматы aac/m4a/wav/mp4, ≤25MB).
  - `get_user_profile(igsid) -> {name, username} | None` → `GET /{igsid}?fields=name,username`.
  - `download_attachment(url) -> (bytes, content_type)` — сразу при обработке (CDN-ссылки истекают), лимит 25MB.
  - `refresh_long_lived_token()` → `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=...` (задача раз в сутки; новый токен логируется только как факт обновления и сохраняется в `app_settings.instagram_token` — используется вместо env, если свежее).
  - Ошибки: 4xx/5xx → `InstagramAPIError(code, subcode, message)`; 80002 (throttling)/5xx → retry в Celery с backoff.
- 24-часовое окно: исходящие сообщения бота и оператора разрешены, если `now − conversation.last_customer_message_at < 24h`; иначе оператору 409 `messaging_window_closed` (HUMAN_AGENT tag не используется в MVP — требует App Review).
- Идемпотентность: `messages.instagram_message_id` (mid) unique — повторная доставка webhook не обрабатывается дважды.

## 2. Карты (`app/integrations/maps/`)

```python
@dataclass class GeoCandidate: formatted: str; lat: float; lng: float; precision: str  # "house" | "street" | "district" | "city" | "other"
@dataclass class GeocodeResult: status: GeocodeStatus; candidates: list[GeoCandidate]; provider: str; error: str | None = None
class Geocoder(Protocol):
    name: str
    def geocode(self, query: str, *, city: str, country_code: str = "tj") -> GeocodeResult: ...
class DistanceMatrixProvider(Protocol):
    name: str
    def matrix(self, points: list[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]: ...  # (durations_s, distances_m)
```

- `NominatimGeocoder` (**по умолчанию**, `GEOCODER_PROVIDER=nominatim`): `GET {NOMINATIM_URL}/search?q=...&format=jsonv2&addressdetails=1&limit=5&countrycodes=tj&accept-language=ru&viewbox=68.65,38.65,68.90,38.48&bounded=0`; обязательный `User-Agent: {NOMINATIM_USER_AGENT}` (с контактом), не более 1 запроса/с (глобальный лок + кэш в Redis 30 дней по нормализованной строке). **Публичный сервер отвечает 403 «Access denied» на стандартные агенты и на любые с `example.com`** (проверено 16.09.2026 — так «не находился» ни один адрес): адаптер отдаёт `FAILED(error=access_denied)`, статус интеграции предупреждает о заглушке в `NOMINATIM_USER_AGENT`. Precision: `addresstype in (building, house)` или `type=house` → `house`; результат с `address.house_number` и `place_rank ≥ 30` (магазин/кафе на доме) → `house` с флагом `is_poi` (сворачивается в дом при `rank_candidates`); `road` → `street`; `suburb|borough|neighbourhood|quarter` → `district`. Данные ODbL — координаты можно хранить (атрибуция OSM в UI карты).
- Разбор адреса и лестница запросов — `app/integrations/maps/address.py` (`parse_address`, `geocode_queries`, `candidate_matches`), правила в 03 §7. Живая проверка Nominatim по Душанбе (16.09.2026, до переноса города на Худжанд): микрорайоны есть как `suburb` («82-й микрорайон», «15 микрорайон»), улицы с домами частично («10, проспект Рудаки», «12, улица Садриддина Айни» — как магазин), дома в микрорайонах — нет; на «18-й микрорайон» Nominatim нечётко возвращает 91-й/112-й/11-й — такие кандидаты отбрасываются по номеру. После переноса на Худжанд (17.09.2026) список районов заменён на реальные названия махаллей города (Себзор, Пахтакор, Разок и др., проверено через Overpass), а bbox/центр — по границе OSM-отношения "Khujand, Tajikistan". **Живая проверка по Худжанду (17.09.2026):** в bbox ~12 тыс. объектов с номером дома; в микрорайонах дома подписаны `addr:street` «31 мкр» / «34 МКР» / «20 мкр», сами микрорайоны — места «28 микрорайон», «29-й мкр», улицы «28мкр». Запрос «Худжанд, 31 мкр, 28» находит дом, «Худжанд, 31 микрорайон, 28» — только микрорайон, а «Худжанд, 28-й микрорайон» (форма Душанбе) — чужие микрорайоны. Поэтому лестница для микрорайона: «Худжанд, N мкр, дом» → «Худжанд, N микрорайон» → «Худжанд, N мкр». Дома 76 в 28 мкр в OSM нет — такой адрес получает центр микрорайона (AMBIGUOUS, «нашли микрорайон, но не дом» + ссылка на карту), улицы вида «кучаи Озоди 12» / «улица Гагарина 10» находятся до дома.
- `GoogleGeocoder` (опционально, `GEOCODER_PROVIDER=google`, `MAPS_API_KEY`): Geocoding API v3 `GET https://maps.googleapis.com/maps/api/geocode/json?address=...&components=country:TJ&bounds=38.48,68.65|38.65,68.90&language=ru&key=...`; `ZERO_RESULTS` → NOT_FOUND; `OK` + (1 результат, без `partial_match`, `location_type in (ROOFTOP, RANGE_INTERPOLATED)`, внутри bbox) → OK; иначе AMBIGUOUS; `OVER_QUERY_LIMIT|REQUEST_DENIED|INVALID_REQUEST` → FAILED. **Лицензия Google**: координаты кэшировать ≤30 дней и не показывать на не-Google карте — поэтому при этом провайдере `location_source=GEOCODER` считается подсказкой; в README предупреждение.
- Правило автопринятия (общая функция `decide_status(candidates, bbox)`): ровно 1 кандидат с `precision=="house"` внутри bbox → OK; 0 → NOT_FOUND; иначе AMBIGUOUS. В лестнице `GeocodingService` для запроса уровня «дом» единственный кандидат-дом среди улиц/районов считается найденным домом (остальные — та же улица, а не альтернативы). `rank_candidates` сворачивает повтор одного адреса (тот же номер и улица/микрорайон, метки подъездов не считаются, ≤ 300 м) в один кандидат — OSM часто хранит здание и отдельную адресную точку. Порядок и правила лестницы (дом → основной номер → ориентир → центр улицы/микрорайона → район, ориентир не принимается автоматически) — 03 §7. Клиенту варианты показываются без индекса и страны («ТехМаркет, 12, улица Садриддина Айни, Шохмансур»).
- Bbox Худжанда (настройка `CITY_BBOX=40.2623896,69.5612523,40.3316825,69.6740681`, центр `40.2842191,69.6191174`) — взят из границы OSM-отношения "Khujand, Tajikistan" (Nominatim, 17.09.2026).
- `OsrmMatrix` (`OSRM_URL`, напр. self-hosted `http://osrm:5000`): `GET {OSRM_URL}/table/v1/driving/{lng,lat;...}?annotations=duration,distance`. Контейнер OSRM — опциональный профиль compose `routing` (подготовка данных Geofabrik Tajikistan описана в README).
- `HaversineMatrix` — всегда доступен; `distance = haversine × ROAD_FACTOR(1.3)`, `duration = distance / average_speed`. Это документированная оценка, не имитация API.
- Фабрики: `get_geocoder(settings)`, `get_distance_provider(settings)` (OSRM при ошибке → fallback haversine с логом `routing.fallback`).

## 3. Речь (`app/integrations/speech/`)

```python
class SpeechToText(Protocol):
    def transcribe(self, audio: bytes, *, mime_type: str, language_hint: str | None) -> TranscriptionResult: ...  # text, language, confidence
class TextToSpeech(Protocol):
    def supports(self, language: str) -> bool: ...
    def synthesize(self, text: str, *, language: str) -> SynthesizedAudio: ...  # bytes, mime_type, extension
```
- STT по умолчанию — `ElevenLabsSTT` (`STT_PROVIDER=elevenlabs`, `STT_API_KEY`): `POST https://api.elevenlabs.io/v1/speech-to-text`, `xi-api-key`, multipart `file`, `model_id=scribe_v2`, без `language_code` на первом проходе; повтор с `language_code=tgk`, если определён язык не ru/tg или текст не кириллический, или клиент ранее писал по-таджикски. Исходные байты Instagram (mp4/m4a) отправляются как есть.
- TTS — `OpenAITTS` (`TTS_PROVIDER=openai`, `TTS_API_KEY`): `POST https://api.openai.com/v1/audio/speech`, `{"model": "gpt-4o-mini-tts", "voice": TTS_VOICE, "input": text, "response_format": "wav"}` → конвертация ffmpeg в `.m4a` (AAC) при наличии ffmpeg, иначе отправка wav. `supports("tg") == False` — **таджикского TTS нет ни у одного облачного провайдера** → таджикоязычным клиентам ответ только текстом.
- Голосовые ответы: `BusinessSettings.voice_replies_enabled` (выкл. по умолчанию). Если включено, язык поддерживается и входящее было голосовым — отправляем текст **и** аудио (файл в `MEDIA_ROOT`, URL `{PUBLIC_BASE_URL}/api/media/{random}.m4a`, удаление через 7 дней).
- Ошибка STT/не настроен → сообщение сохраняется с `text=null`, клиенту: «Не получилось разобрать голосовое, напишите, пожалуйста, текстом», `needs_attention=true`.

## 4. Maxim (`app/integrations/maxim/`)

Исследование: официального API нет (вердикт C), работа сервиса в Худжанде не подтверждена (`docs/research/maxim.md`).

```python
class MaximIntegration(ABC):
    provider: DispatchProvider
    @abstractmethod
    def create_delivery(self, request: DeliveryDispatchRequest) -> DispatchResult: ...
    @abstractmethod
    def get_delivery_status(self, delivery: Delivery) -> DispatchStatus: ...
    @abstractmethod
    def cancel_delivery(self, delivery: Delivery) -> DispatchResult: ...
```
- `ManualMaximIntegration` (единственная реализация, `MAXIM_MODE=manual`): `create_delivery` не делает сетевых вызовов, возвращает `requires_operator=True`, `instructions` и `copy_text` (адрес «откуда/куда», координаты, получатель, телефон, комментарий, сумма к оплате при получении). Статус → `AWAITING_DISPATCH`; оператор вводит номер заказа Maxim/курьера через `PATCH /deliveries/{id}` → `DISPATCHED`. `get_delivery_status` возвращает сохранённый статус; `cancel_delivery` → инструкция отменить в приложении + статус `CANCELLED`.
- `MAXIM_MODE=api` без реализации → фабрика бросает `IntegrationNotConfiguredError("Официальный API Maxim недоступен")`. Реализация `MaximApiIntegration` добавляется только после получения официальной документации/договора.
- `android-automation/README.md` — архитектура `Backend → Android Automation Service → Maxim` как **не-MVP** компонент и условия допуска (письменное согласие Maxim, политика Google Play по AccessibilityService, человек подтверждает каждый заказ). Кода нет.

## 5. Фоновые задачи (`app/tasks/`)

`celery_app.py`: broker/backend `REDIS_URL`, `timezone=BUSINESS_TIMEZONE`, `task_acks_late=True`, `task_always_eager` из настроек: тесты, а также `APP_ENV=development` без `REDIS_URL` (локально нет брокера и worker — задачи выполняются сразу в процессе API, иначе они терялись бы в memory-брокере; например, ответ оператора получает честный статус FAILED «Instagram не настроен», а не вечный PENDING). Задачи (идемпотентны, логируют `task.*`):

| задача | назначение |
|---|---|
| `process_instagram_event(event_dict)` | InboundMessageService.handle_event (retry 3, backoff; блокировка диалога) |
| `send_instagram_message(message_id)` | отправка исходящего `Message` (retry 5 на 5xx/429/80002, кроме частично доставленного текста), статус SENT/FAILED |
| `transcribe_voice_message(message_id)` | STT (вызывается из inbound при VOICE, затем диалог; retry 3 на временные ошибки провайдера) |
| `synthesize_voice_reply(message_id)` | TTS + отправка аудио |
| `continue_dialog_after_location(order_id)` | клиент отметил точку на карте (`/l/{token}`) → бот проверяет черновик и присылает сводку или следующий вопрос (03 §7) |
| `geocode_delivery(delivery_id)` | геокодирование доставок, созданных из админки |
| `optimize_routes(date)` | построение маршрута (по запросу) |
| `generate_daily_report(date?)` | построение/перестроение отчёта за дату (по запросу) |
| `maybe_generate_daily_report()` | beat: каждые 15 мин; если бизнес-время ≥ `BusinessSettings.daily_report_time` (значение из админки; env `DAILY_REPORT_TIME` — только значение по умолчанию для настройки) и отчёт за сегодня ещё не сформирован после этого времени → `generate_daily_report(today)` |
| `send_follow_ups()` | beat: каждые 15 мин; догоняющие вопросы в диалогах, где бот ждёт клиента (03 §6a). Задержка — настройка `follow_up_after_hours`, поэтому решает сама задача, а не расписание |
| `sync_delivery_statuses()` | beat: каждые 15 мин, `MaximIntegration.get_delivery_status` для DISPATCHED (manual — no-op) |
| `refresh_instagram_token()` | beat: раз в сутки |
| `cleanup_media()` | beat: раз в сутки, TTS-файлы старше 7 дней |

В обработчике webhook (HTTP) тяжёлой работы нет: разбор, проверка подписи, `process_instagram_event.delay(...)` на каждое событие, 200.

Сервисы не импортируют задачи напрямую: они получают `TaskQueue` (`app/services/task_queue.py`, в production — `CeleryTaskQueue`, в тестах — очередь, выполняющая задачи сразу). Каждая задача — тонкая обёртка над функцией `run_*(db, ...)`, которую тесты вызывают с фейками. Задачи, запускающие диалог (`process_instagram_event`, `transcribe_voice_message`, `continue_dialog_after_location`), держат Redis-блокировку по ключу треда (`app/core/locks.py`): несколько быстрых сообщений клиента обрабатываются строго по очереди; блокировка занята → повтор задачи; Redis недоступен → обработка без блокировки с предупреждением в логе. Медиафайлы пишет воркер, отдаёт backend — у контейнеров общий том `media` (`docker-compose.yml`).

## 6. Переменные окружения (`backend/app/core/config.py` ↔ `.env.example`)

```env
# --- App ---
APP_ENV=development            # development | production | test
APP_SECRET_KEY=                # не используется для JWT; для подписи прочих токенов
PUBLIC_BASE_URL=http://localhost:8000     # публичный URL backend (webhook, media)
FRONTEND_PUBLIC_URL=http://localhost:3000 # для ссылок клиенту /l/{token}
CORS_ORIGINS=http://localhost:3000
BUSINESS_TIMEZONE=Asia/Dushanbe
LOG_LEVEL=INFO
MEDIA_ROOT=/app/media
# --- DB / Redis ---
DATABASE_URL=postgresql+psycopg://bakery:bakery@postgres:5432/bakery
REDIS_URL=redis://redis:6379/0
POSTGRES_USER=bakery
POSTGRES_PASSWORD=
POSTGRES_DB=bakery
# --- Auth ---
JWT_SECRET=
JWT_ACCESS_TTL_MINUTES=30
JWT_REFRESH_TTL_DAYS=14
FIRST_ADMIN_USERNAME=admin
FIRST_ADMIN_PASSWORD=
# --- Instagram ---
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_VERIFY_TOKEN=
INSTAGRAM_APP_SECRET=
META_APP_SECRET=
INSTAGRAM_ACCOUNT_ID=
INSTAGRAM_GRAPH_URL=https://graph.instagram.com
INSTAGRAM_API_VERSION=v25.0
# --- LLM ---
LLM_PROVIDER=anthropic        # anthropic | gemini (05 §2)
LLM_API_KEY=
LLM_MODEL=claude-opus-5
LLM_EFFORT=medium
LLM_TIMEOUT_SECONDS=60
LLM_FALLBACKS_ENABLED=true
# --- Speech ---
STT_PROVIDER=elevenlabs
STT_API_KEY=
TTS_PROVIDER=openai
TTS_API_KEY=
TTS_VOICE=alloy
# --- Maps / routing ---
GEOCODER_PROVIDER=nominatim     # nominatim | google
MAPS_API_KEY=
NOMINATIM_URL=https://nominatim.openstreetmap.org
NOMINATIM_USER_AGENT=bakery-bot/1.0 (contact@example.com)
OSRM_URL=
CITY_NAME=Худжанд
CITY_BBOX=40.2623896,69.5612523,40.3316825,69.6740681
CITY_CENTER=40.2842191,69.6191174
DAILY_REPORT_TIME=21:00         # значение по умолчанию для BusinessSettings.daily_report_time
# --- Maxim ---
MAXIM_MODE=manual               # manual | api (api не реализован — нет официального API)
MAXIM_API_KEY=
MAXIM_API_URL=
# --- Frontend ---
NEXT_PUBLIC_APP_NAME=Домашняя выпечка
BACKEND_INTERNAL_URL=http://backend:8000
```
