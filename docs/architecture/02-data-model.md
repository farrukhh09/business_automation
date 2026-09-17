# 02. Модель данных

Все модели — в `backend/app/models/`, импортируются в `app/models/__init__.py` (для Alembic). Enums — `app/models/enums.py`. Колонки ниже — обязательный минимум; имена колонок и таблиц фиксированы (на них опираются API-схемы и фронтенд).

Обозначения: `PK` — первичный ключ, `FK→t` — внешний ключ, `?` — nullable, `U` — unique, `IX` — индекс. Все таблицы, кроме отмеченных, имеют `created_at`, `updated_at` (TimestampMixin, UTC).

## Enums (`app/models/enums.py`)

```python
class UserRole(StrEnum): ADMIN, OPERATOR
class Language(StrEnum): RU = "ru"; TG = "tg"
class OrderStatus(StrEnum): NEW, WAITING_CONFIRMATION, CONFIRMED, PREPARING, READY, HANDED_TO_COURIER, COMPLETED, CANCELLED
class PaymentStatus(StrEnum): UNPAID, PARTIALLY_PAID, PAID, REFUNDED
class DeliveryType(StrEnum): DELIVERY, PICKUP
class PaymentMethod(StrEnum): CASH, CARD, TRANSFER, OTHER
class PaymentKind(StrEnum): PAYMENT, REFUND
class OrderSource(StrEnum): INSTAGRAM, ADMIN
class ActorType(StrEnum): USER, CUSTOMER, AI, SYSTEM
class ConversationMode(StrEnum): AI, HUMAN_HANDOFF
class MessageDirection(StrEnum): INCOMING, OUTGOING
class MessageType(StrEnum): TEXT, VOICE, IMAGE, SYSTEM
class MessageSender(StrEnum): CUSTOMER, AI, OPERATOR, SYSTEM
class MessageDeliveryStatus(StrEnum): PENDING, SENT, FAILED, NOT_APPLICABLE
class Intent(StrEnum): FAQ, PRODUCT_QUERY, CREATE_ORDER, CHANGE_ORDER, CANCEL_ORDER, DELIVERY_QUERY, PAYMENT_QUERY, ORDER_STATUS, GREETING, COMPLAINT, OPERATOR_REQUEST, OTHER
class GeocodeStatus(StrEnum): PENDING, OK, NOT_FOUND, AMBIGUOUS, FAILED, MANUAL
class LocationSource(StrEnum): CUSTOMER_PIN, GEOCODER, OPERATOR, COURIER
class DeliveryStatus(StrEnum): PENDING, AWAITING_DISPATCH, DISPATCHED, DELIVERED, FAILED, CANCELLED
class DispatchProvider(StrEnum): MAXIM_MANUAL, MAXIM_API
```

Все StrEnum — значение равно имени (кроме `Language`: `"ru"`, `"tg"`).

## users — `User`
| колонка | тип | примечание |
|---|---|---|
| id | int PK | |
| username | str(64) U | логин |
| full_name | str(128)? | |
| password_hash | str(255) | Argon2, никогда не отдаётся в API |
| role | UserRole | |
| is_active | bool, default true | |
| last_login_at | datetime? | |

## refresh_tokens — `RefreshToken`
id PK; user_id FK→users (cascade); jti str(64) U; expires_at datetime; revoked_at datetime?; created_at. (без updated_at)

## customers — `Customer`
| колонка | тип | примечание |
|---|---|---|
| id | int PK | |
| instagram_user_id | str(64)? U | IGSID отправителя; null для клиентов, созданных вручную |
| name | str(128)? | |
| phone | str(32)? IX | нормализованный `+992XXXXXXXXX`, если распознан |
| username | str(64)? | Instagram username |
| language | Language, default ru | последний определённый язык общения |
| is_new | bool, default true | см. правило в `03-business-rules.md` §2 |
| last_order_at | datetime? | время последнего не отменённого подтверждённого заказа |
| notes | text? | заметки оператора |

Производные (не хранятся, считаются запросом): `orders_count`, `total_spent`, `customer_type` (`NEW`/`REGULAR` из `is_new`).

## products — `Product`
| колонка | тип | примечание |
|---|---|---|
| id | int PK | |
| name | str(128) | |
| description | text? | |
| price | Numeric(12,2) | > 0 |
| currency | str(3), default "TJS" | |
| unit | str(16), default "шт." | |
| aliases | JSON list[str], default [] | синонимы/названия на таджикском для сопоставления |
| is_active | bool, default true | «включён/выключен» |
| deleted_at | datetime? | мягкое удаление (DELETE API); удалённые не показываются нигде, кроме истории заказов |
| sort_order | int, default 0 | |

## orders — `Order`
| колонка | тип | примечание |
|---|---|---|
| id | int PK | номер заказа |
| customer_id | FK→customers IX | |
| conversation_id | FK→conversations? | диалог, из которого создан |
| source | OrderSource | |
| status | OrderStatus IX, default NEW | |
| payment_status | PaymentStatus, default UNPAID | |
| payment_method | PaymentMethod? | |
| delivery_type | DeliveryType? | null пока не выяснено |
| delivery_address | text? | копия отформатированного адреса из `deliveries` |
| delivery_latitude | Numeric(9,6)? | копия |
| delivery_longitude | Numeric(9,6)? | копия |
| delivery_date | date? IX | бизнес-дата выдачи/доставки |
| delivery_time | time? | локальное время |
| total_amount | Numeric(12,2), default 0 | сумма позиций, считается только backend |
| paid_amount | Numeric(12,2), default 0 | считается из payments |
| comment | text? | комментарий к заказу (надпись, дизайн и т.п.) |
| is_repeat_customer | bool, default false | снимок: был ли клиент постоянным в момент подтверждения |
| confirmed_at | datetime? | |
| completed_at | datetime? | |
| cancelled_at | datetime? | |
| cancel_reason | text? | |

## order_items — `OrderItem`
id PK; order_id FK→orders (cascade delete) IX; product_id FK→products? (ondelete SET NULL); product_name str(128) (снимок); quantity int (>0); unit_price Numeric(12,2) (снимок цены на момент добавления); total_price Numeric(12,2) (= quantity × unit_price); comment text?.

## order_events — `OrderEvent` (журнал изменений)
id PK; order_id FK→orders (cascade) IX; actor_type ActorType; actor_user_id FK→users?; event_type str(48) (`CREATED`, `UPDATED`, `ITEMS_CHANGED`, `STATUS_CHANGED`, `PAYMENT_CHANGED`, `DELIVERY_CHANGED`, `CONFIRMED`, `CANCELLED`); changes JSON (`{"field": [old, new]}`, значения сериализуемые); comment text?; created_at. (без updated_at)

## payments — `Payment`
id PK; order_id FK→orders (cascade) IX; kind PaymentKind; amount Numeric(12,2) (>0); method PaymentMethod?; note text?; created_by_user_id FK→users?; paid_at datetime; created_at. (без updated_at)

## deliveries — `Delivery` (структурированный адрес и доставка, 1:1 с заказом типа DELIVERY)
| колонка | тип | примечание |
|---|---|---|
| id | int PK | |
| order_id | FK→orders U (cascade) | |
| address_raw | text | как написал клиент |
| address_formatted | text? | нормализованный/от геокодера |
| city | str(64), default "Душанбе" | |
| district | str(64)? | район (Сино, Шохмансур, ...) |
| microdistrict | str(32)? | «82 мкр» |
| street | str(128)? | |
| house | str(32)? | |
| apartment | str(16)? | |
| entrance | str(16)? | |
| floor | str(8)? | |
| landmark | text? | ориентир |
| latitude | Numeric(9,6)? | |
| longitude | Numeric(9,6)? | |
| location_source | LocationSource? | откуда координаты |
| geocode_status | GeocodeStatus, default PENDING | |
| geocode_provider | str(32)? | |
| geocode_candidates | JSON list, default [] | `[{"formatted": str, "lat": float, "lng": float, "precision": str}]` |
| recipient_name | str(128)? | |
| recipient_phone | str(32)? | |
| courier_comment | text? | |
| status | DeliveryStatus, default PENDING | |
| dispatch_provider | DispatchProvider? | |
| external_id | str(64)? | номер заказа в Maxim (вводит оператор) |
| external_status | str(64)? | |
| courier_name | str(128)? | |
| courier_phone | str(32)? | |
| dispatched_at | datetime? | |
| delivered_at | datetime? | |

## location_requests — `LocationRequest` (ссылка клиенту «отметьте точку на карте»)
id PK; token str(64) U (secrets.token_urlsafe); delivery_id FK→deliveries (cascade); expires_at datetime; used_at datetime?; latitude Numeric(9,6)?; longitude Numeric(9,6)?; created_at. (без updated_at)

## route_plans — `RoutePlan` и route_stops — `RouteStop`
`RoutePlan`: id PK; delivery_date date IX; start_name str(128); start_latitude/longitude Numeric(9,6); start_time time; algorithm str(32); distance_source str(32) (`osrm`/`haversine`); total_distance_m int; total_duration_s int; created_by_user_id FK→users?; created_at/updated_at.

`RouteStop`: id PK; route_plan_id FK→route_plans (cascade) IX; delivery_id FK→deliveries (cascade); sequence int (1..n); eta time?; distance_from_prev_m int; duration_from_prev_s int; lateness_min int, default 0.

## conversations — `Conversation`
| колонка | тип | примечание |
|---|---|---|
| id | int PK | |
| customer_id | FK→customers IX | |
| instagram_conversation_id | str(128)? U | ключ треда; для Instagram Login — `"{ig_account_id}:{igsid}"` |
| mode | ConversationMode, default AI | |
| handoff_reason | text? | |
| handoff_at | datetime? | |
| needs_attention | bool, default false | подсветка в «Диалогах» |
| assigned_user_id | FK→users? | |
| last_message_at | datetime? IX | |
| last_customer_message_at | datetime? | для контроля 24-часового окна Instagram |
| state | JSON, default {} | состояние диалога (см. `05-ai.md` §4), пишет только backend |
| failed_ai_attempts | int, default 0 | подряд неудачных попыток понять клиента |

## messages — `Message`
| колонка | тип | примечание |
|---|---|---|
| id | int PK | |
| conversation_id | FK→conversations (cascade) IX | |
| direction | MessageDirection | |
| message_type | MessageType | |
| sender | MessageSender | |
| text | text? | для VOICE — результат STT |
| audio_url | text? | путь к сохранённому файлу / URL |
| media_url | text? | изображение |
| instagram_message_id | str(255)? U | `mid`, идемпотентность webhook |
| ai_processed | bool, default false | |
| intent | str(32)? | |
| ai_payload | JSON? | структурированный результат понимания (без секретов) |
| delivery_status | MessageDeliveryStatus, default NOT_APPLICABLE | для исходящих |
| error | text? | |
| sent_by_user_id | FK→users? | исходящее от оператора |
| created_at | datetime | (без updated_at) |

## faq_items — `FaqItem`
id PK; question text; answer text; question_tg text?; answer_tg text?; keywords JSON list[str], default []; is_active bool default true; sort_order int default 0.

## app_settings — `AppSetting` (ключ-значение для настроек из админки)
key str(64) PK; value JSON; updated_by_user_id FK→users?; updated_at. Типизированный доступ — `SettingsService` + схема `BusinessSettings` (`04-api.md`).

## daily_reports — `DailyReport`
id PK; report_date date U; data JSON; text text; generated_at datetime.

## Индексы и ограничения (минимум)
- `orders (delivery_date, status)`, `orders (customer_id, status)`, `messages (conversation_id, created_at)`.
- CHECK: `order_items.quantity > 0`, `products.price > 0`, `payments.amount > 0`.
- Alembic-миграция `0001_initial` создаёт всё перечисленное и должна применяться на PostgreSQL (`alembic upgrade head`).
