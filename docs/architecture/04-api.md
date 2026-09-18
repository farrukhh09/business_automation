# 04. REST API (контракт backend ↔ frontend)

Префикс всех маршрутов — `/api`. JSON. OpenAPI доступен на `/api/docs` (только при `APP_ENV != production`).

## 0. Общие соглашения

- **Авторизация**: `Authorization: Bearer <access_token>`. Роли: `ADMIN` (всё), `OPERATOR` (см. колонку «Роль»). `STAFF` = ADMIN или OPERATOR. `PUBLIC` = без токена.
- **Ошибки**: `{"detail": "текст на русском", "code": "machine_code"}`; валидация — стандартный 422 FastAPI. Коды: 400 `bad_request`, 401 `not_authenticated`/`invalid_token`, 403 `forbidden`, 404 `not_found`, 409 `conflict`, 422 `validation_error`/`order_incomplete`/`invalid_status_transition`/..., 429 `rate_limited`, 502 `integration_error`, 503 `integration_not_configured`.
- **Пагинация**: query `page` (≥1, default 1), `page_size` (1..100, default 20) → `Page[T] = {"items": T[], "total": int, "page": int, "page_size": int}`.
- **Типы**: `Money` — JSON number с 2 знаками (`1500.0`); `date` — `"YYYY-MM-DD"`; `time` — `"HH:MM"` (вход принимает и `"HH:MM:SS"`); `datetime` — ISO 8601 с таймзоной (UTC). Enum — строки из `02-data-model.md`.
- **Rate limits** (slowapi): по умолчанию 120/мин на IP; `POST /auth/login` 5/мин; `POST /auth/refresh` 30/мин; webhook 600/мин; публичная карта 30/мин.

## 1. Auth

| Метод | Путь | Роль | Тело → Ответ |
|---|---|---|---|
| POST | /auth/login | PUBLIC | `{username, password}` → `TokenPair` |
| POST | /auth/refresh | PUBLIC | `{refresh_token}` → `TokenPair` (старый refresh отзывается — ротация) |
| POST | /auth/logout | STAFF | `{refresh_token}` → 204 |
| GET | /auth/me | STAFF | → `UserOut` |

`TokenPair = {access_token, refresh_token, token_type: "bearer", expires_in: int (сек), user: UserOut}`
`UserOut = {id, username, full_name, role, is_active, last_login_at, created_at}`

## 2. Users (ADMIN)

| GET /users | → `UserOut[]` |
|---|---|
| POST /users | `{username, full_name?, password (≥8), role}` → `UserOut` 201 |
| PATCH /users/{id} | `{full_name?, password?, role?, is_active?}` → `UserOut` (нельзя деактивировать/понизить самого себя → 409) |
| DELETE /users/{id} | деактивация → 204 |

## 3. Customers

| Метод | Путь | Роль | |
|---|---|---|---|
| GET | /customers | STAFF | query: `search` (имя/username/телефон), `customer_type` (`new`/`regular`), `page`, `page_size`, `sort` (`last_order_at`/`created_at`/`total_spent`, префикс `-` = desc; default `-last_order_at`) → `Page[CustomerListItem]` |
| GET | /customers/{id} | STAFF | → `CustomerDetail` |
| POST | /customers | STAFF | `{name, phone?, username?, language?, notes?}` → `CustomerDetail` 201 |
| PATCH | /customers/{id} | STAFF | `{name?, phone?, language?, notes?, is_blocked?}` → `CustomerDetail` |

`CustomerListItem = {id, name, username, phone, instagram_user_id, language, is_new, is_blocked, customer_type: "NEW"|"REGULAR", orders_count, total_spent: Money, last_order_at, created_at}`
`CustomerDetail = CustomerListItem + {notes, conversation_id: int|null, orders: OrderListItem[]}` (заказы — все, новые сверху). `orders_count`/`total_spent` — только valid-заказы.

## 4. Products

| GET | /products | STAFF | query `include_inactive` (bool, default false), `search` → `ProductOut[]` (без удалённых) |
|---|---|---|---|
| GET | /products/{id} | STAFF | → `ProductOut` |
| POST | /products | ADMIN | `ProductCreate` → `ProductOut` 201 |
| PATCH | /products/{id} | ADMIN | `ProductUpdate` (все поля опциональны) → `ProductOut` |
| DELETE | /products/{id} | ADMIN | мягкое удаление (`deleted_at`, `is_active=false`) → 204 |

`ProductCreate = {name, description?, price: Money (>0), currency? ="TJS", unit? ="шт.", aliases?: string[], is_active? =true, sort_order? =0}`
`ProductOut = ProductCreate + {id, created_at, updated_at}`

## 5. Orders

| Метод | Путь | Роль | |
|---|---|---|---|
| GET | /orders | STAFF | query: `status` (повторяемый), `payment_status`, `delivery_type`, `customer_id`, `delivery_date`, `date_from`, `date_to` (по `delivery_date`), `search` (номер/имя/телефон), `page`, `page_size`, `sort` (`-created_at` default, `delivery_date`, `-delivery_date`, `total_amount`) → `Page[OrderListItem]` |
| POST | /orders | ADMIN | `OrderCreate` → `OrderDetail` 201 |
| GET | /orders/{id} | STAFF | → `OrderDetail` |
| PATCH | /orders/{id} | STAFF* | `OrderUpdate` → `OrderDetail` |
| POST | /orders/{id}/status | STAFF | `{status, comment?}` → `OrderDetail` |
| POST | /orders/{id}/payments | STAFF | `{kind: PAYMENT|REFUND, amount: Money, method?, note?}` → `OrderDetail` |
| POST | /orders/{id}/cancel | STAFF | `{reason?}` → `OrderDetail` |
| GET | /orders/{id}/events | STAFF | → `OrderEventOut[]` |

`*` PATCH: OPERATOR может менять только `status`, `payment_status`, `paid_amount`, `payment_method`, `comment` и блок `delivery` (адрес/получатель/телефон/комментарий курьеру/координаты). Попытка изменить `items`, `delivery_date`, `delivery_time`, `delivery_type`, `customer_id` оператором → 403. ADMIN — всё.

`OrderItemIn = {product_id: int, quantity: int (≥1), comment?: string}` (цены не принимаются)
`DeliveryIn = {address_raw?, district?, microdistrict?, street?, house?, apartment?, entrance?, floor?, landmark?, recipient_name?, recipient_phone?, courier_comment?, latitude?, longitude?}` (координаты от сотрудника → `location_source=OPERATOR`, `geocode_status=MANUAL`; адрес без координат → запуск геокодирования)
`OrderCreate = {customer_id, items: OrderItemIn[] (≥1), delivery_type, delivery_date, delivery_time, comment?, payment_method?, delivery?: DeliveryIn, confirm: bool = true}` (confirm=true → сразу CONFIRMED при полноте, иначе NEW)
`OrderUpdate = {items?: OrderItemIn[] (заменяет набор), delivery_type?, delivery_date?, delivery_time?, comment?, status?, payment_status?, paid_amount?: Money, payment_method?, delivery?: DeliveryIn}`

`OrderListItem = {id, customer: {id, name, username, phone}, status, payment_status, delivery_type, delivery_date, delivery_time, delivery_address, total_amount, paid_amount, items_summary: string ("Медовик ×2, Чизкейк ×1"), items_count: int (Σ quantity), comment, created_at}`
`OrderDetail = OrderListItem + {items: OrderItemOut[], payment_method, delivery_latitude, delivery_longitude, source, conversation_id, is_repeat_customer, confirmed_at, completed_at, cancelled_at, cancel_reason, updated_at, delivery: DeliveryOut|null, payments: PaymentOut[], allowed_transitions: OrderStatus[], missing_fields: string[]}`
`OrderItemOut = {id, product_id, product_name, quantity, unit_price, total_price, comment}`
`PaymentOut = {id, kind, amount, method, note, paid_at, created_by_user_id}`
`OrderEventOut = {id, actor_type, actor_user: {id, username}|null, event_type, changes, comment, created_at}`

## 6. Production

`GET /production?date=YYYY-MM-DD` (STAFF; default сегодня) → `{date, orders_count, items: [{product_id, product_name, quantity, unit}], text}`

## 7. Statistics

| GET | /statistics | STAFF | query: `period` (`today`/`yesterday`/`week`/`month`/`custom`), `date_from`, `date_to` (для custom), `date_basis` (`delivery` default / `created`) → `StatisticsOut` |
|---|---|---|---|
| GET | /statistics/dashboard | STAFF | → `DashboardOut` |
| GET | /statistics/timeseries | STAFF | query `date_from`, `date_to`, `date_basis` → `[{date, revenue, orders_count, paid_amount, expenses, profit}]` (расходы — по `expense_date` дня) |

`StatisticsOut = {period: {name, date_from, date_to, date_basis}, finance: {revenue, orders_count, paid_orders_count, partially_paid_orders_count, unpaid_orders_count, paid_amount, unpaid_amount, average_check}, customers: {new_customers, regular_customers, total_customers, new_customer_orders, regular_customer_orders, customers_registered}, delivery: {delivery_orders, pickup_orders}, profit_and_loss: {revenue, expenses, profit, margin_percent: number|null, expenses_by_category: [{category, amount}]}}` (правила — 03 §4 «Расходы и прибыль»; блок отдельно от `finance`, который сохраняется в снимке ежедневного отчёта)

## 7a. Расходы

| GET | /expenses | STAFF | query `date_from?`, `date_to?` (включительно; `date_from > date_to` → 422), `category?`, `page`, `page_size` → `Page[ExpenseOut]` (сортировка `-expense_date`, `-id`) |
|---|---|---|---|
| POST | /expenses | ADMIN | `ExpenseCreate` → 201 `ExpenseOut` (дата в будущем → 422 `expense_date_in_future`) |
| PATCH | /expenses/{id} | ADMIN | частично `ExpenseCreate` (отсутствует — без изменений, `comment: null` очищает) → `ExpenseOut` |
| DELETE | /expenses/{id} | ADMIN | → 204 (физическое удаление) |

`ExpenseCreate = {expense_date: date, category: ExpenseCategory, amount: Money (> 0), comment?: string|null (до 2000)}`; неизвестные поля → 422.
`ExpenseOut = ExpenseCreate + {id, created_by_user_id, created_at, updated_at}`
`DashboardOut = {date, orders_today, revenue_today, unpaid_orders_count (все valid-заказы с оплатой ≠ PAID и delivery_date ≥ сегодня−30д), new_customers_today, regular_customers_today, delivery_orders_today, pickup_orders_today, waiting_confirmation_count, conversations_needing_attention, recent_orders: OrderListItem[] (10)}` — «сегодня» по `delivery_date`.

## 8. Reports

| GET | /reports/daily | STAFF | query `date` (default вчера, если сейчас раньше `daily_report_time`, иначе сегодня) → `DailyReportOut` (если нет в БД — строится на лету и сохраняется; **сегодня и позже — всегда пересчитывается**, чтобы не показывать дневной снимок до вечернего задания) |
|---|---|---|---|
| POST | /reports/daily/generate | ADMIN | `{date}` → `DailyReportOut` (перестраивает) |
| GET | /reports/daily/history | STAFF | query `limit` (default 30) → `DailyReportOut[]` без `data` |

`DailyReportOut = {date, generated_at, text, data: {finance, customers, delivery, production}}`

## 9. Deliveries

| Метод | Путь | Роль | |
|---|---|---|---|
| GET | /deliveries | STAFF | query `date` (default сегодня), `status`, `geocode_status` → `DeliveryListItem[]` (только valid-заказы) |
| GET | /deliveries/{id} | STAFF | → `DeliveryOut` |
| PATCH | /deliveries/{id} | STAFF | `DeliveryIn + {status?, external_id?, external_status?, courier_name?, courier_phone?}` → `DeliveryOut` |
| POST | /deliveries/{id}/geocode | STAFF | → `DeliveryOut` (перезапуск геокодирования) |
| POST | /deliveries/{id}/select-candidate | STAFF | `{index}` → `DeliveryOut` (`location_source=OPERATOR`, `MANUAL`) |
| POST | /deliveries/{id}/location-link | STAFF | → `{url, expires_at}` (ссылка для клиента) |
| POST | /deliveries/optimize | STAFF | `{date, start_time?: "HH:MM"}` → `RoutePlanOut` |
| GET | /deliveries/routes | STAFF | query `date` → `RoutePlanOut | null` (последний план) |
| POST | /deliveries/{id}/dispatch | STAFF | → `DispatchResultOut` (Maxim manual: карточка для оператора, статус `AWAITING_DISPATCH`) |
| GET | /deliveries/dispatch-sheet | STAFF | query `date` → `{text, stops: [...]}` копируемый список в порядке маршрута |

`DeliveryOut = {id, order_id, address_raw, address_formatted, city, district, microdistrict, street, house, apartment, entrance, floor, landmark, latitude, longitude, location_source, geocode_status, geocode_provider, geocode_candidates, recipient_name, recipient_phone, courier_comment, status, dispatch_provider, external_id, external_status, courier_name, courier_phone, dispatched_at, delivered_at, created_at, updated_at}`
`DeliveryListItem = DeliveryOut + {order: {id, status, payment_status, total_amount, paid_amount, delivery_date, delivery_time, items_summary, customer: {id, name, phone}}}`
`RoutePlanOut = {id, delivery_date, start: {name, latitude, longitude}, start_time, algorithm, distance_source, total_distance_m, total_duration_s, created_at, stops: [{sequence, delivery_id, order_id, address, latitude, longitude, approximate, approximate_place, eta, desired_time, lateness_min, distance_from_prev_m, duration_from_prev_s, recipient_name, phone, courier_comment, items_summary}], unlocated: [{delivery_id, order_id, address, reason}]}` (`approximate=true` — точка не дом, а место, найденное геокодером: микрорайон, улица, ориентир; `approximate_place` — его название, 03 §7)
`DispatchResultOut = {delivery: DeliveryOut, provider, requires_operator: bool, instructions: string, copy_text: string}`

## 10. FAQ

| GET /faq | STAFF | query `include_inactive` → `FaqOut[]` |
|---|---|---|
| POST /faq | ADMIN | `{question, answer, question_tg?, answer_tg?, keywords?: string[], is_active?, sort_order?}` → `FaqOut` 201 |
| PATCH /faq/{id} | ADMIN | частичное → `FaqOut` |
| DELETE /faq/{id} | ADMIN | → 204 (физическое удаление) |

## 11. Conversations (Диалоги)

| GET | /conversations | STAFF | query `mode`, `needs_attention`, `search`, `page`, `page_size` → `Page[ConversationListItem]` (сортировка `-last_message_at`) |
|---|---|---|---|
| GET | /conversations/{id} | STAFF | query `before_id?`, `limit` (default 50) → `ConversationDetail` |
| POST | /conversations/{id}/messages | STAFF | `{text}` (1..2000) → 201 `MessageOut` (`delivery_status=PENDING`, отправка в Instagram фоновой задачей; 409 `messaging_window_closed`, если прошло >24 ч с последнего сообщения клиента). Если диалог был у бота — он переводится в `HUMAN_HANDOFF` («Оператор ответил клиенту вручную»), чтобы бот и оператор не отвечали одновременно; `needs_attention=false` |
| POST | /conversations/{id}/handoff | STAFF | `{reason?}` → `ConversationDetail` («Взять на себя»: `mode=HUMAN_HANDOFF`, `assigned_user_id` = сотрудник, `needs_attention=false`; без причины — «Оператор взял диалог на себя») |
| POST | /conversations/{id}/resume | STAFF | → `ConversationDetail` (вернуть боту: `mode=AI`, `needs_attention=false`, `failed_ai_attempts=0`, причина и назначение очищаются) |
| POST | /conversations/{id}/read | STAFF | → 204 (`needs_attention=false`) |

`ConversationListItem = {id, customer: {id, name, username, phone}, mode, needs_attention, handoff_reason, last_message_at, last_message_preview, active_order_id}`
`ConversationDetail = ConversationListItem + {messages: MessageOut[] (по возрастанию времени), state_summary: {draft_order_id, awaiting: string|null, language}}`
`active_order_id` — последний оформленный и не завершённый заказ клиента (CONFIRMED…HANDED_TO_COURIER; черновик — в `state_summary.draft_order_id`). `last_message_preview` — до 120 символов; голосовое без текста — «[голосовое сообщение]», изображение — «[изображение]». `state_summary.draft_order_id` = null, если черновик уже подтверждён/отменён из админки. `audio_url`/`media_url` — относительные ссылки `/api/media/{filename}`.
`MessageOut = {id, direction, message_type, sender, text, audio_url, media_url, intent, delivery_status, error, sent_by_user_id, created_at}`

## 11a. Тестовый чат (только `APP_ENV=development`)

Разговор с ботом прямо из админки, без Instagram — то же самое, что консольный скрипт
`scripts/chat_console.py`, но через HTTP. `customer_key` придумывает фронтенд (случайная строка,
хранится в `localStorage`; новый ключ = новый тестовый клиент/диалог, как `--new` у консольного
скрипта). Диалог — обычная запись в `conversations` (`instagram_conversation_id = "webtest:{key}"`),
поэтому виден в разделе «Диалоги» как любой другой; исходящие ответы бота помечаются `FAILED` с
пояснением «не отправлялся, показан только в интерфейсе» — никуда реально не уходят. Вне
`APP_ENV=development` оба маршрута отвечают 404 (не просто прячутся из меню).

| GET | /test-chat/{customer_key} | STAFF | → `TestChatOut` (пустой, если ещё не было сообщений) |
|---|---|---|---|
| POST | /test-chat/{customer_key}/messages | STAFF | `{text}` (1..2000) → `TestChatOut`, сообщение проходит весь путь `DialogService`, как настоящее от клиента |
| POST | /test-chat/{customer_key}/images | STAFF | `multipart/form-data`: `file` (JPEG/PNG/WebP/GIF, ≤ 10 МБ) + `text` (подпись, необязательно, ≤ 2000) → `TestChatOut` |

`TestChatOut = {conversation_id: int|null, mode, needs_attention, messages: MessageOut[]}`

Загруженный файл сохраняется в `MEDIA_ROOT` так же, как скачанное вложение Instagram, и сообщение
приходит боту как обычное IMAGE (`InboundMessageService.handle_event(..., stored_image=...)` —
скачивать нечего, CDN-ссылки нет). Так проверяется разбор чека об оплате (03 §3). Неподходящий файл
— 422 `validation_error`.

## 12. Settings

| GET | /settings | STAFF | → `BusinessSettings` |
|---|---|---|---|
| PUT | /settings | ADMIN | `BusinessSettings` (частичное обновление допустимо) → `BusinessSettings` |
| GET | /settings/integrations | ADMIN | → `{instagram: IntegrationStatus, llm, stt, tts, geocoder, routing, maxim}`; `IntegrationStatus = {configured: bool, provider: string|null, details: string}` (без секретов) |

`BusinessSettings = {business_name: "Домашняя выпечка", ai_enabled: true, voice_replies_enabled: false, warehouse: {name, address, latitude, longitude}, pickup_address: string, working_hours: string, min_lead_time_hours: 24, max_days_ahead: 60, delivery_time_window_minutes: 60, route_start_time: "09:00", service_time_minutes: 5, average_speed_kmh: 25, daily_report_time: "21:00", payment_methods_text: string, delivery_info_text: string, prepayment_enabled: false, prepayment_percent: 100, prepayment_wallet: string, prepayment_wallet_banks: string, prepayment_auto_confirm: false}`

Предоплата (03 §3, 17.09.2026): `prepayment_wallet` — номер счёта, который бот называет после «Да» и в ответе об оплате: кошелёк-телефон или номер карты (13 цифр и больше — карта, и бот говорит «на карту»); `prepayment_wallet_banks` — где он принимается («Душанбе Сити, Алиф, Эсхата» или банк карты; пусто — бот не уточняет); `prepayment_percent` — доля суммы заказа (1..100); `prepayment_auto_confirm` — отмечать оплату по совпавшему чеку без сотрудника (по умолчанию `false`).

## 13. Публичные и служебные маршруты

| Метод | Путь | Роль | |
|---|---|---|---|
| GET | /webhooks/instagram | PUBLIC | верификация `hub.mode=subscribe`, `hub.verify_token == INSTAGRAM_VERIFY_TOKEN` → `hub.challenge` (text/plain), иначе 403 |
| POST | /webhooks/instagram | PUBLIC (подпись) | проверка `X-Hub-Signature-256` (403 при неверной подписи; 503 без секрета, кроме `APP_ENV=development`) → 200 `{status: "ok", events: N}` быстро; одна задача Celery на каждое сообщение клиента (эхо, «прочитано», реакции пропускаются); очередь недоступна → 502, Meta доставит повторно |
| GET | /public/location/{token} | PUBLIC | → `{business_name, address_raw, latitude?, longitude?, city_center: {lat, lng}, suggested?: {lat, lng}, expired: bool, used: bool}` — `suggested` = лучший кандидат геокодера (центр улицы/микрорайона) для центрирования карты, пин не ставится |
| POST | /public/location/{token} | PUBLIC | `{latitude, longitude}` (внутри bbox страны) → `{ok: true}`; 410 если истёк |
| GET | /media/{filename} | PUBLIC | файлы `MEDIA_ROOT` со случайными именами: `tts-*` — голосовые ответы (Instagram скачивает их по URL), `in-*` — медиа входящих сообщений для «Диалогов»; неверное имя или путь вне каталога → 404 |
| GET | /health | PUBLIC | `{status: "ok"}` |
| GET | /health/ready | PUBLIC | проверка БД и Redis → 200/503 |
