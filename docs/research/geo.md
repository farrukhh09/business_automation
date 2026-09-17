# Геокодирование и оптимизация маршрутов курьеров в Душанбе

Дата проверки: **2026-09-15**. Все утверждения ниже сверены с официальной документацией (ссылки в разделе «Источники»). Что не удалось подтвердить, помечено **«не подтверждено»**.

---

## 1. Вывод и рекомендация

### 1.1 Главное

- **Ни один провайдер не публикует качество геокодирования Душанбе на уровне дома.** Официальные таблицы и новости подтверждают только, что покрытие *есть*.
- Живой тест публичного Nominatim 2026-09-15 это подтверждает:

| Запрос (`countrycodes=tj`) | Результат |
|---|---|
| `Душанбе, Сино` | 1 результат: `"Ноҳияи Сино, Душанбе, 734000, Тоҷикистон"`, `category: boundary`, `type: administrative`, `addresstype: borough`, `place_rank: 14`. Это центроид района, а не дом. |
| `Душанбе, 82 мкр, дом 5` | `[]` (ничего не найдено) |

- Google Geocoding v4 прямо пишет, что **не поддерживает** «Historical or unofficial place names» и названия бизнесов без адреса. Неформальные ориентиры («возле рынка Корвон») текстовым геокодером надёжно не решаются.

### 1.2 Рекомендации

| Вопрос | Рекомендация |
|---|---|
| **(1) Источник координат / геокодер** | **Основной источник — точка, подтверждённая клиентом.** Бот отправляет ссылку на свою страницу «подтвердите место»: геолокация браузера плюс перетаскиваемый пин. При вручении заказа сохраняется GPS курьера. Это собственные данные: хранить можно бессрочно, без поминутной оплаты и лицензионных ограничений.<br><br>**Текстовый геокодер по умолчанию — Google Geocoding API.**<br>• v4 GA с 2026-03-30; v3 документирован как legacy и работает.<br>• Ограничение страной: `components=country:TJ` (v3, это жёсткий фильтр). В v4 `regionCode=TJ` — только смещение.<br>• Смещение к bbox Душанбе: `bounds` (v3) или `locationBias.rectangle.*` (v4).<br>• Автопринятие только при одновременном выполнении всех условий: ровно 1 результат; нет `partial_match` (есть только в v3); `ROOFTOP`/`RANGE_INTERPOLATED`; точка внутри bbox.<br><br>**Фоллбэк — 2GIS Geocoder**, но только после пилота на демо-ключе (1 000 запросов). Нужно проверить, что данные API реально покрывают Душанбе: потребительский 2ГИС запущен там в ноябре 2025, >153 тыс. зданий. Лицензия запрещает хранить результаты, поэтому ответ 2GIS — это подсказка оператору, а не сохраняемая координата.<br><br>**Последний бесплатный вариант — Nominatim**, для центроидов районов, микрорайонов и ориентиров. Данные ODbL: хранить можно, с атрибуцией.<br><br>Всё ниже порога автопринятия уходит оператору, а бот просит у клиента пин. |
| **(2) Матрица расстояний/времени** | **Self-hosted OSRM** (сервис `table`) на выгрузке Geofabrik Tajikistan (46.1 MB, данные до 2026-09-14T20:21:51Z).<br>• Нет платы за вызов и нет квот.<br>• Лимит `--max-table-size` по умолчанию 100 точек (см. §3.3), так что 41×41 проходит без проблем.<br><br>**Альтернатива «всё в Google»:** не строить `computeRouteMatrix`, а сразу вызывать Route Optimization API.<br>• 40 заказов/день = 1 200 shipments/мес — в пределах 5 000 бесплатных, то есть **$0**.<br>• Ежедневная матрица 41×41 через Compute Route Matrix Essentials ≈ **$202/мес**. |
| **(3) TSP / VRP** | **Решать локально.** На 5–40 точек от одной кухни:<br>• чистый TSP: nearest neighbour + 2-opt/Or-opt;<br>• окна времени или 2+ курьера: OR-Tools (`ortools`) или VROOM (BSD-2-Clause, принимает матрицу OSRM или собственную).<br><br>Платный API оптимизации на таком объёме не оправдан:<br>• в Таджикистане у Google нет Traffic Layer (в таблице покрытия «—»), а это главное преимущество облака;<br>• добавляется OAuth;<br>• появляется правило «показывать только на Google Maps».<br><br>Google Route Optimization имеет смысл как бесплатный *бенчмарк*, если вы и так в экосистеме Google. |

### 1.3 Архитектуру определяет лицензирование, а не точность

- **Google:**
  - lat/lng из Geocoding / Routes / Route Optimization можно кэшировать не более 30 дней подряд; `place_id` можно хранить.
  - «Customer must not use Google Maps Content … in conjunction with a non-Google map».
  - Страницы policies: результаты, показанные на карте, — только на Google Map. Route Optimization «can be displayed on Google Maps only; using them on other maps is forbidden».
- **Яндекс (бесплатные условия, редакция 01.09.2026):**
  - Геокодер только на сайтах/в приложениях с JavaScript API Яндекс Карт.
  - Координаты — «исключительно для отображения их посредством Сервиса «JavaScript API Яндекс Карт…»».
  - Запрещено показывать результаты на картах третьих лиц.
  - Кэш — не более 30 дней.
  - Запрещены сервисы «связанные с управлением и диспетчеризацией».
  - Лимит 1 000 запросов в сутки.
- **2GIS (оферта WebAPI, редакция 27.03.2026):**
  - «Кэширование Продуктов не предусмотрено».
  - Извлекать и сохранять Продукты запрещено.

**Вывод.** Координаты, которые вы **храните** и отдаёте в OSRM или свой солвер, должны быть подтверждены клиентом или курьером, а не взяты сырыми из вендорского геокодера. Считается ли подача Google-координат в не-Google движок маршрутизации использованием «in conjunction with a non-Google map» — юридический вопрос, здесь он не решён. Консервативный вариант — либо подтверждённые пины, либо Google end-to-end.

---

## 2. Детали

### 2.1 Адреса в Душанбе и нормализация

- Клиенты смешивают русский и таджикский. Используются:
  - микрорайоны («82 мкр», «91 мкр»);
  - районы («Сино» = «ноҳияи Сино» = «район Сино»);
  - ориентиры.
- OSM/Nominatim возвращает таджикские названия (`Ноҳияи Сино`). Для русских подписей, где они есть в OSM, передавайте `accept-language=ru`.
- Нормализуйте до геокодирования (тем же LLM, что разбирает DM):
  - каноническая строка («Душанбе, 82-й микрорайон, 5»);
  - структура {district, microdistrict, house, landmark, apartment/entrance/floor}.
  - Квартиру, подъезд и этаж не отправляйте в геокодер: храните для курьера.
- Пары таджикский↔русский (общеязыковые знания, не из документации провайдеров): ноҳия ↔ район, кӯча ↔ улица, хиёбон ↔ проспект, мкр ↔ микрорайон.
- Грубый bbox Душанбе для смещения и проверки попадания: lat 38.48–38.65, lon 68.65–68.90; центр ≈ 38.560, 68.774. **Приблизительно, не подтверждено документально.** Сверьте по карте перед хардкодом.

---

### 2.2 Google Geocoding API (v3 и v4)

**Покрытие.** Таблица покрытия Google Maps Platform для Tajikistan:

| Функция | Статус |
|---|---|
| Geocoding | ⬤ |
| Driving Directions / Snap to Roads | ⬤ |
| Walking Directions | ⬤ |
| Map Tiles 2D/3D | ⬤ |
| Traffic Layer | — |
| Biking Directions | — |
| Speed Limits | — |
| Maps JavaScript 3D | — |

Качество на уровне микрорайона и дома **не документировано**: нужен бенчмарк.

#### v3 (legacy)

`GET https://maps.googleapis.com/maps/api/geocode/json?...`. HTTPS обязателен. Аутентификация: `key=`.

```http
GET https://maps.googleapis.com/maps/api/geocode/json
    ?address=Душанбе, 82 микрорайон, 5
    &components=country:TJ
    &bounds=38.48,68.65|38.65,68.90
    &language=ru
    &region=tj
    &key=API_KEY
```

**`components`:**
- `country` и `postal_code` **жёстко ограничивают** выдачу.
- `route`, `locality`, `administrative_area` «may be used to influence results, but will not be enforced».
- Повторяющиеся фильтры объединяются через AND.
- «Component filtering returns a `ZERO_RESULTS` response only if you provide filters that exclude each other».

**`bounds` («southwest|northeast») и `region` (ccTLD)** только **смещают** выдачу, но не ограничивают её.

**`extra_computations`** (`ADDRESS_DESCRIPTORS`, `BUILDING_AND_ENTRANCES`):
- Address Descriptors GA только **в Индии** (2025-03-25), в других странах экспериментально.
- Для TJ не рассчитывайте.

Форма ответа (значения иллюстративные):

```json
{
  "results": [{
    "address_components": [ ... ],
    "formatted_address": "...",
    "geometry": {
      "location": {"lat": 38.5615, "lng": 68.7391},
      "location_type": "GEOMETRIC_CENTER",
      "viewport": { ... }
    },
    "partial_match": true,
    "place_id": "...",
    "types": ["sublocality", "political"]
  }],
  "status": "OK"
}
```

**Сигналы v3:**

| Сигнал | Смысл / действие |
|---|---|
| `status: ZERO_RESULTS` | «the geocode was successful but returned no results». Не найдено: просить пин. |
| `status: OK` и `results.length > 1` | «the geocoder may return several results when address queries are ambiguous». Неоднозначно: показать кандидатов оператору. |
| `partial_match: true` | «did not return an exact match for the original request, though it was able to match part of the requested address». Не принимать автоматически. |
| `geometry.location_type` | • `ROOFTOP`: точность до адреса.<br>• `RANGE_INTERPOLATED`: интерполяция на дороге.<br>• `GEOMETRIC_CENTER`: центр полилинии или полигона, например микрорайона.<br>• `APPROXIMATE`.<br>Автопринятие только для первых двух. |
| `OVER_QUERY_LIMIT` / `UNKNOWN_ERROR` | Повтор с экспоненциальной задержкой. |
| `OVER_DAILY_LIMIT` | Ключ, биллинг или лимит расходов. Не повторять, алерт. |
| `REQUEST_DENIED` / `INVALID_REQUEST` | Ошибка конфигурации. Не повторять, алерт. |
| `types` | `street_address`/`premise` против `sublocality`/`neighborhood` — дополнительный признак гранулярности. |

#### v4 (GA 2026-03-30; Preview с 2025-04-30)

**Эндпоинты:**
- Неструктурированный адрес: `GET https://geocode.googleapis.com/v4/geocode/address/{addressQuery}`.
- Структурированный: `GET https://geocode.googleapis.com/v4/geocode/address?address.addressLines=...&address.locality=...`.
- Прочие: reverse geocoding, place geocoding, SearchDestinations (для SearchDestinations field mask **обязателен**).
- Каналы: `/v4/` (GA), `/v4beta/`, `/v4alpha/`.

**Аутентификация:**
- API-ключ: заголовок `X-Goog-Api-Key` или `key=` в примерах.
- OAuth. Scopes: `https://www.googleapis.com/auth/cloud-platform`, `.../maps-platform.geocode`, `.../maps-platform.geocode.address`.

**Параметры:**
- `languageCode`, `regionCode` (смещение);
- `locationBias.rectangle.low.latitude`, `locationBias.rectangle.low.longitude`, `locationBias.rectangle.high.latitude`, `locationBias.rectangle.high.longitude`;
- `$fields` / `X-Goog-FieldMask` (для address — опционально).

**Квота:** 25 QPS по умолчанию; на странице usage указан и лимит 3 000 QPM.

```bash
curl -H "X-Goog-Api-Key: API_KEY" \
  "https://geocode.googleapis.com/v4/geocode/address/%D0%94%D1%83%D1%88%D0%B0%D0%BD%D0%B1%D0%B5%2C%2082%20%D0%BC%D0%B8%D0%BA%D1%80%D0%BE%D1%80%D0%B0%D0%B9%D0%BE%D0%BD%2C%205?regionCode=TJ&languageCode=ru&locationBias.rectangle.low.latitude=38.48&locationBias.rectangle.low.longitude=68.65&locationBias.rectangle.high.latitude=38.65&locationBias.rectangle.high.longitude=68.90"
```

Спецсимволы в пути кодируются: `/`→`%2F`, `#`→`%23`, `+`→`%2B`, пробел→`%20`.

```json
{"results": [{
  "place": "//places.googleapis.com/places/ChIJ...",
  "placeId": "ChIJ...",
  "location": {"latitude": 38.56, "longitude": 68.77},
  "granularity": "GEOMETRIC_CENTER",
  "viewport": {"low": {}, "high": {}},
  "formattedAddress": "...",
  "postalAddress": {},
  "addressComponents": [],
  "types": ["sublocality"]
}]}
```

**Сигналы v4:**
- `granularity`: `ROOFTOP` / `RANGE_INTERPOLATED` / `GEOMETRIC_CENTER` / `APPROXIMATE` (плюс служебное `GRANULARITY_UNSPECIFIED`). В v4 это `granularity` вместо `location_type`.
- Поля `GeocodeResult` в reference: `place`, `placeId`, `location`, `granularity`, `viewport`, `bounds`, `formattedAddress`, `postalAddress`, `addressComponents`, `postalCodeLocalities`, `types`, `plusCode`. **Аналога `partial_match` в этом списке нет.** Если этот сигнал критичен, используйте v3.
- Точная форма пустого ответа v4 **не подтверждена**. Отсутствующий или пустой `results` — «не найдено»; HTTP не-2xx — ошибка.

**История:** 2025-08-13 в v3 прекращены экспериментальные Entrances, Navigation points, Building outlines, Grounds; вместо них SearchDestinations (v4).

**Лимиты и цены:**
- v3: 3 000 QPM. v4: 25 QPS по умолчанию.
- SKU **Geocoding**: 10 000 бесплатных событий в месяц. Далее за 1 000:
  - 10 001–100 000: $5.00
  - 100 001–500 000: $4.00
  - 500 001–1 000 000: $3.00
  - 1 000 001–5 000 000: $1.50
  - 5 000 000+: $0.38
- Кредит $200/мес действовал до 2025-02-28; сейчас действуют бесплатные лимиты по SKU.
- ≤40 адресов/день ≈ 1 200/мес → **$0**.
- Что v4-address тарифицируется тем же SKU «Geocoding», **не подтверждено**: SearchDestinations имеет отдельный SKU.

**Условия** (Service Specific Terms, датированная версия 2024-05-22):
- «Customer can temporarily cache latitude (lat) and longitude (lng) values from the Geocoding API for up to 30 consecutive calendar days, after which Customer must delete the cached latitude and longitude values.»
- `place_id` кэшировать можно.
- «Customer must not use Google Maps Content from the Geocoding API in conjunction with a non-Google map.»
- Страница Geocoding Policies: «Geocoding API results displayed on a map must be shown on a Google Map»; place ID «exempt from the caching restrictions».
- Актуальную версию SST (по поисковому индексу изменена 2026-06-04) прочитать целиком не удалось: страница обрезается. **Сверьте живой текст.**

---

### 2.3 Яндекс HTTP Геокодер

**Покрытие:**
- На странице продукта: «более 29 млн адресов в России и СНГ».
- Качество по Душанбе на уровне дома **не документировано**.
- Допустимые `lang`: только `ru_RU, uk_UA, be_BY, en_RU, en_US, tr_TR`. **Таджикского нет.**

**Эндпоинт:** `GET https://geocode-maps.yandex.ru/v1`.

**Обязательные параметры:**
- `apikey` («Ключ будет активирован в течение 15 минут после получения»);
- `geocode`;
- `lang`.

**Опциональные параметры:**
- `format=json`;
- `ll` + `spn` или `bbox=x1,y1~x2,y2`;
- `rspn=1` (жёстко ограничить областью);
- `results` (по умолчанию 10, максимум 50);
- `skip`;
- `kind` (house, street, metro, district, locality);
- `sco` (longlat/latlong);
- `uri`;
- `signature`.

```http
GET https://geocode-maps.yandex.ru/v1?apikey=KEY&geocode=Душанбе, 82 микрорайон, 5&lang=ru_RU&format=json&bbox=68.65,38.48~68.90,38.65&rspn=1&results=5
```

```json
{"response": {"GeoObjectCollection": {
  "metaDataProperty": {"GeocoderResponseMetaData": {"request": "...", "found": "3", "results": "5"}},
  "featureMember": [{"GeoObject": {
    "metaDataProperty": {"GeocoderMetaData": {
      "kind": "house", "precision": "exact", "text": "...",
      "Address": {"country_code": "TJ", "Components": [{"kind": "locality", "name": "Душанбе"}]}
    }},
    "Point": {"pos": "68.77 38.56"}
  }}]
}}}
```

Порядок в `Point.pos` — «долгота широта». Значения иллюстративные.

**Сигналы:**
- Не найдено: `found == "0"` или пустой `featureMember`.
- Несколько кандидатов: `found > 1`.
- `precision`:

| Значение | Описание |
|---|---|
| `exact` | «Найден дом с указанным номером дома» |
| `number` | «…с указанным номером, но с другим номером строения или корпуса» |
| `near` | «…с номером, близким к запрошенному» |
| `range` | «Найдены приблизительные координаты запрашиваемого дома» |
| `street` | «Найдена только улица» |
| `other` | «Не найдена улица, но найден, например, посёлок, район и т. п.» |

- `kind`: house, street, metro, district, locality, province, country, hydro, railway_station, airport, vegetation, other.
- Всегда проверять `country_code == "TJ"`.

**Условия** («Условия использования отдельных сервисов «Яндекс Карт»», редакция 01.09.2026):
- **5.1.3** запрещает «Создавать на основе Сервисов системы мониторинга транспортных средств, людей или иных объектов, отображающие информацию в реальном времени, и любые другие услуги, связанные с управлением и диспетчеризацией».
- **5.1.5** запрещает сохранять Данные, кроме «временного хранения (кэширования) результатов ответа на запросы исключительно для целей улучшения функциональности и работоспособности Сервисов и только для использования в рамках возможностей, предоставляемых Сервисами, на срок не более 30 дней».
- **6.6.1:**
  - «Пользователь может использовать Геокодер только на сайтах или в приложениях, использующих Сервис JavaScript API Яндекс Карт…».
  - Координаты — «исключительно для отображения их посредством Сервиса «JavaScript API Яндекс Карт включая Плеер Панорам»».
  - «Пользователю запрещается отображать результаты работы Геокодера в сервисах третьих лиц или на картографических материалах третьих лиц».
- **6.6.2:** не более 1 000 запросов в сутки; сверх — только на коммерческой основе.
- **Итог: бесплатные условия для курьерской диспетчеризации не подходят.**

**Платно** (таблица 1, оплата за год):

| Запросов/сутки | Стандартная лицензия | Расширенная («с сохранением данных») | Сверх лимита, за 1 000 |
|---|---|---|---|
| 1 000 | 195 000 ₽ | 226 200 ₽ | 390 ₽ |
| 10 000 | 585 000 ₽ | 678 600 ₽ | 195 ₽ |
| 25 000 | 1 105 000 ₽ | 1 281 800 ₽ | 163 ₽ |

Разрешает ли платная лицензия диспетчерское использование, **не подтверждено**: нужно смотреть оферту. **Вердикт: дорого и ограничительно для домашней пекарни.**

---

### 2.4 2GIS Geocoder API (и Places API для ориентиров)

**Покрывает ли 2GIS Душанбе?**
- **Потребительский продукт — да.**
  - 2gis.tj/ru/dushanbe.
  - vecherka.tj от 2025-11-11: «более 153 тыс. зданий и 23 тыс. организаций», таджикский и русский языки.
- **Данные API — явно нигде не указаны.**
  - Обзор Regions API не содержит списка стран.
  - В справочнике `region/list` у `country_code_filter` есть только пример `ru`.
  - Косвенный признак: в списке локалей API есть `tg_TJ` и `ru_TJ`.
  - Оферта WebAPI разрешает отображение Продуктов «на территории всего мира».
  - **Проверьте демо-ключом до выбора.**

**Эндпоинт:** `GET https://catalog.api.2gis.com/3.0/items/geocode`.
- Обязательные: `q` (1–500 символов), `key`.
- Опциональные:
  - `fields` (например `items.point`);
  - `locale` (например `ru_RU`);
  - `point` + `radius` (0–50 000 м) или `lon`/`lat`;
  - `type` (`building`, `street`, `adm_div`, `attraction` и др.);
  - `page` и `page_size` (1–50, по умолчанию 20).

```http
GET https://catalog.api.2gis.com/3.0/items/geocode?q=Душанбе, 82 микрорайон, 5&fields=items.point&locale=ru_RU&key=KEY
```

```json
{"meta": {"code": 200, "api_version": "...", "issue_date": "..."},
 "result": {"total": 1, "items": [{
   "id": "...", "name": "...", "full_name": "...", "type": "building",
   "purpose_name": "...", "address_name": "...",
   "point": {"lon": 68.77, "lat": 38.56}
 }]}}
```

**Сигналы:**
- Не найдено: HTTP / `meta.code` 404 («Item not found», тип `itemNotFound`). Точный JSON тела ошибки дословно **не подтверждён**.
- Неоднозначно: `result.total > 1`.
- Гранулярность: `items[].type` (building / street / adm_div).
- Прочие коды: 400 / 403 / 408 / 500.

**Цены** (Platform Manager, ₽/мес; годовая подписка −10%):

| API | Тарификация | 10 000 единиц | Другие пакеты |
|---|---|---|---|
| Geocoder | за успешный запрос (200/204) | 4 700 ₽ | 1 000 000 = 70 000 ₽ |
| Places | за успешный запрос | 6 700 ₽ | 5 000 000 = 250 000 ₽ |
| Routing, Distance Matrix | см. §3.2 | 6 700 ₽ | — |
| TSP | points × couriers | 14 000 ₽ | 1 000 000 = 200 000 ₽ |

- Лимит: 600 единиц/мин на Geocoder, Places, Routing, Distance Matrix.
- Демо-ключ: 1 000 запросов.

**Лицензия** (оферта WebAPI, редакция 27.03.2026):
- Охватывает в том числе Geocoder, Places, Routing, Distance Matrix, TSP, Regions, Suggest.
- **3.1:** «Кэширование Продуктов не предусмотрено»; «Пользователю запрещается извлекать, сохранять полученные по настоящему Договору Продукты».
- **2.10:** атрибуция «2ГИС» с гиперссылкой на http://dev.2gis.ru.
- Явного запрета диспетчеризации в этой оферте не найдено.

**Старые «Правовая информация по API 2ГИС»** (редакция 05.12.2022):
- **п. 4.1 е** запрещает использование «для системы мониторинга транспортных средств, отображающей информацию в реальном времени, и прочих услуг, связанных с управлением и диспетчеризацией транспортных средств».
- **п. 4.2** допускает лишь временное кэширование результатов геокодирования для использования в рамках Сервиса.
- **Получите письменное подтверждение от 2GIS** (api@2gis.com), что курьерская маршрутизация разрешена вашей подпиской и какой документ применим.

---

### 2.5 OpenStreetMap Nominatim

**Эндпоинт:** `GET https://nominatim.openstreetmap.org/search` (документация 5.3.2).

**Параметры:**
- Свободная форма `q=` **или** структурированные `amenity/street/city/county/state/country/postalcode`. Их нельзя комбинировать с `q`.
- `format` (по умолчанию `jsonv2`);
- `limit` (по умолчанию 10, максимум 40);
- `addressdetails`, `accept-language`, `countrycodes=tj`;
- `viewbox=x1,y1,x2,y2` + `bounded=1`;
- `layer` (address, poi, railway, natural, manmade);
- `featureType` (country, state, city, settlement);
- `dedupe`.

**Живой результат (2026-09-15):**

```http
GET https://nominatim.openstreetmap.org/search?q=Душанбе, Сино&format=jsonv2&countrycodes=tj&limit=5&addressdetails=1
```

```json
[{"lat": "38.5615057", "lon": "68.7390650",
  "display_name": "Ноҳияи Сино, Душанбе, 734000, Тоҷикистон",
  "category": "boundary", "type": "administrative",
  "addresstype": "borough", "place_rank": 14,
  "importance": 0.6144832693101554}]
```

Тот же запрос с `q=Душанбе, 82 мкр, дом 5` вернул `[]`.

**Сигналы:**
- `[]` — не найдено; более одного элемента — кандидаты.
- Гранулярность: `addresstype` / `category` / `type` / `place_rank` (`borough` с рангом 14 — район, а не здание).
- Флага частичного совпадения нет: сравнивайте части `address` с запросом.
- `place_id` нестабилен между инсталляциями: используйте `osm_type` + `osm_id`.

**Политика публичного сервера** (operations.osmfoundation.org):
- «an absolute maximum of 1 request per second».
- Валидный HTTP Referer или User-Agent, идентифицирующий приложение.
- «Results must be cached on your side. Clients sending repeatedly the same query may be classified as faulty and blocked».
- **Bulk-режим:** «Scripts running longer than a day and scripts that are run at regular intervals are restricted to 4 requests per minute»; один поток, одна машина. Ночной пакетный скрипт попадает под 4 запроса/мин.
- Запрещены автодополнение и систематические запросы (сетка reverse-запросов, полные списки).
- Атрибуция ODbL.
- Для большего объёма: коммерческие провайдеры или своя инсталляция.

**Пригодность для продакшена:** ~40 разовых запросов в день по мере поступления заказов, за кэшем и очередью ≤1 запрос/с, допустимы. SLA нет.

**Self-host** (документация 5.3.2):
- «A minimum of 2GB of RAM is required».
- PostgreSQL 12+ (13+ «strongly recommended»), PostGIS 3.0+ (3.2+ рекомендуется), osm2pgsql 1.8+, Python 3.9+.
- Полная планета: 128 GB RAM и ≥1 TB диска.
- Выгрузка Таджикистана — 46.1 MB. Хватит небольшой VM, но это **оценка, не документировано**.

**Ограничитель — точность, а не лицензия:** номера домов в микрорайонах в OSM, судя по тесту, редки.

---

### 2.6 openrouteservice geocoder (Pelias) и геокодинг GraphHopper

**ORS:**
- Эндпоинты `/geocode/search`, `/geocode/autocomplete`, `/geocode/search/structured` (beta), `/geocode/reverse` публичного API.
- «This endpoint is not part of openrouteservice, but of our public API. It is not available when running an own instance of openrouteservice.»
- Поля ответа (по документации Pelias):
  - `confidence` (0.0–1.0);
  - `match_type` (`exact` / `interpolated` / `fallback`);
  - `accuracy` (`point` / `centroid`);
  - `layer`.
- Пустой `features` — не найдено. Данные OSM: ожидайте покрытие как у Nominatim.
- Точный способ передачи ключа (заголовок или `api_key`) здесь **не подтверждён**: см. API Playground.

**GraphHopper:**
- Геокодинг: 0.3 кредита (провайдер default/gisgraphy) или 0.9 кредита (nominatim, nettoolkit, opencagedata).
- Free-план только некоммерческий.
- Своих данных по Душанбе не добавляет.

---

## 3. Источники матрицы расстояний/времени

### 3.1 Google Routes API — computeRouteMatrix

`POST https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix`

Заголовки: `Content-Type: application/json`, `X-Goog-Api-Key`, `X-Goog-FieldMask`.

```json
{
  "origins": [{"waypoint": {"location": {"latLng": {"latitude": 38.56, "longitude": 68.77}}}}],
  "destinations": [{"waypoint": {"location": {"latLng": {"latitude": 38.58, "longitude": 68.74}}}}],
  "travelMode": "DRIVE",
  "routingPreference": "TRAFFIC_UNAWARE"
}
```

Ответ — поток или массив. Элементы «not guaranteed to be returned in any order», поэтому ориентируйтесь на индексы:

```json
[{"originIndex": 0, "destinationIndex": 0, "status": {}, "distanceMeters": 822, "duration": "160s", "condition": "ROUTE_EXISTS"}]
```

**Сигналы ошибки:** `condition: "ROUTE_NOT_FOUND"` или непустой `status`.

**Лимиты:**
- ≤625 элементов на запрос;
- ≤100 при `TRAFFIC_AWARE_OPTIMAL` или `TRANSIT`;
- ≤50 origins+destinations, если они заданы адресом или place ID;
- 3 000 элементов/мин.

**Цены** (за элемент = origins × destinations):

| SKU | Когда применяется | Бесплатно в месяц | Далее |
|---|---|---|---|
| Essentials | базовый запрос | 10 000 | $5.00 / 1 000 |
| Pro | `TRAFFIC_AWARE` / `TRAFFIC_AWARE_OPTIMAL` | 5 000 | $10.00 / 1 000 |
| Enterprise | например, двухколёсный транспорт | 1 000 | $15.00 / 1 000 |

**Стоимость для проекта:**
- 40 точек + склад = 41×41 = 1 681 элемент/день ≈ 50 430/мес → (50 430 − 10 000) × $0.005 ≈ **$202/мес**, минимум 3 запроса в день из-за лимита 625.
- 20 точек: 441/день → 13 230/мес → ≈ **$16**.
- Трафик-aware режим, вероятно, мало что даст: Traffic Layer для TJ отсутствует. **Это вывод, не документировано.**

**Условия** (SST 2024-05-22, раздел Routes API):
- lat/lng кэшировать ≤30 дней;
- `place_id` можно хранить;
- «must not use Google Maps Content from the Routes API in conjunction with a non-Google map».

### 3.2 2GIS Distance Matrix API

`POST https://routing.api.2gis.com/get_dist_matrix?key=KEY&version=2.0`

```json
{"points": [{"lat": 38.56, "lon": 68.77}, {"lat": 38.58, "lon": 68.74}],
 "sources": [0], "targets": [1],
 "transport": "driving", "type": "jam"}
```

```json
{"routes": [{"status": "OK", "source_id": 0, "target_id": 1,
             "distance": 4100, "duration": 540, "reliability": 1}]}
```

(Значения иллюстративные.)

- `transport`: driving, taxi, truck, walking, bicycle, scooter, motorcycle, public_transport.
- `type`: `jam` (по умолчанию), `statistics`, `shortest`.
- `start_time` в RFC 3339.
- `status` на пару: OK, FAIL, POINT_EXCLUDED, ROUTE_NOT_FOUND, ROUTE_DOES_NOT_EXISTS, ATTRACT_FAIL, PLATFORMS_NOT_FOUND.
- HTTP-коды: 200, 204 (не найдено), 400, 408 («Execution time exceeded 5 seconds»), 422.
- Синхронный режим — до 25 точек отправления или прибытия. Больше — асинхронно: task id, затем опрос.
- Тарификация за каждую пару source–target: 10 000 единиц — 6 700 ₽/мес; 600 единиц/мин; демо-ключ 1 000.
- Матрица 41×41 ежедневно (~50 тыс. единиц/мес) требует пакета больше; цену я не извлёк (**не подтверждено**).
- Покрытие дорожного графа Душанбе в API **не подтверждено** (см. §2.4).
- Лицензия запрещает хранение Продуктов.

### 3.3 OSRM (рекомендуется, self-hosted)

**Публичные демо-серверы не для продакшена.**
- Вики OSRM «Demo server» (сейчас routing.openstreetmap.de):
  - «Do not exceed 1 request per second»;
  - «usage is restricted to reasonable, non-commercial use-cases»;
  - «We provide no guarantees wrt. uptime, latency, or data updates».
- FOSSGIS (routing.openstreetmap.de/about.html):
  - «One request per second max»;
  - «No scraping, no heavy usage»;
  - валидный user agent (и referrer, если применимо);
  - атрибуция и ссылка «fix the map».

**Self-host:**
- Данные: `https://download.geofabrik.de/asia/tajikistan-latest.osm.pbf` — 46.1 MB, «contains all OSM data up to 2026-09-14T20:21:51Z», ODbL 1.0.
- Docker-образ: `ghcr.io/project-osrm/osrm-backend` (пайплайн MLD, по README).
- Релизы:
  - Сейчас версии вида YY.M.x: v26.9.0 от 2026-09-01, v26.8.0, v26.7.x, v26.6.x.
  - Ранее был v6.0.0. Что это последний semver-релиз, **не подтверждено**.
  - v26.9.0: «feat(http): add POST request support».
  - `docs/http.md`: сервисы `route`, `table`, `match` принимают JSON POST.

```bash
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend osrm-extract -p /opt/car.lua /data/tajikistan-latest.osm.pbf
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend osrm-partition /data/tajikistan-latest.osrm
docker run -t -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend osrm-customize /data/tajikistan-latest.osrm
docker run -t -i -p 5000:5000 -v "${PWD}:/data" ghcr.io/project-osrm/osrm-backend osrm-routed --algorithm mld /data/tajikistan-latest.osrm
```

Обновление: еженедельный cron — скачать выгрузку, прогнать шаги заново, перезапустить контейнер.

**Сервис `table`** (HTTP API v1):

```http
GET http://osrm:5000/table/v1/driving/68.7739,38.5600;68.7390,38.5615;68.80,38.58?annotations=duration,distance
```

```json
{"code": "Ok",
 "durations": [[0, 412.3, 655.1], [398.7, 0, 720.4], [640.2, 701.9, 0]],
 "distances": [[0, 3510.2, 6120.8], [3490.1, 0, 7015.3], [6010.6, 6950.0, 0]],
 "sources": [{"location": [68.7739, 38.56], "name": "...", "distance": 12.4}],
 "destinations": [ ... ]}
```

(Значения иллюстративные; координаты в URL — `lon,lat`.)

- **Опции:** `sources` / `destinations` (индексы или `all`), `annotations`, `fallback_speed`, `fallback_coordinate` (`input`/`snapped`), `scale_factor`. Оценённые ячейки перечислены в `fallback_speed_cells`.
- **Коды:**
  - общие: `Ok`, `InvalidUrl`, `InvalidService`, `InvalidVersion`, `InvalidOptions`, `InvalidQuery`, `InvalidValue`, `NoSegment`, `TooBig`, `DisabledDataset`;
  - для table: `NoTable`, `NotImplemented`.
- Большая `distance` снапа точки означает проблему геокода или пина: точка далеко от дороги.
- `osrm-routed --max-table-size` по умолчанию 100 локаций. Это подтверждено issue #1761 в репозитории и сторонними источниками; в текущей справке `osrm-routed` дословно **не проверено**.
- **Сервис `trip`:**
  - «greedy heuristic (farthest-insertion algorithm) for 10 or more waypoints and uses brute force for less than 10 waypoints»;
  - опции `roundtrip`, `source` (`any`/`first`), `destination` (`any`/`last`); не все комбинации поддерживаются;
  - **окон времени нет**, но это удобный бейзлайн.
- Точность зависит от полноты дорожного графа OSM в Душанбе (односторонние улицы, закрытые дворы). Сверяйте с опытом курьеров.

### 3.4 openrouteservice matrix (hosted)

**Запрос:** `POST /v2/matrix/{profile}` с `locations` ([lon,lat]), `sources`, `destinations`, `metrics`, `units`, `resolve_locations`.

**Ответ:** `durations`, `distances`, `sources`/`destinations` со `snapped_distance`. Непроходимые пары — `null`.

**Restrictions:**
- Matrix: «3,500 (e.g. 50 x 50) per request» (сам пример противоречив: 50×50 = 2 500); «25 (e.g. 5 x 5)» при dynamic arguments.
- Directions: 50 waypoints.
- Optimization: 3 vehicles.

**Дневные квоты бесплатного плана не подтверждены.** Страница планов HeiGIT рендерится JS и не читается. Сторонние и старые источники называют Directions 2 000/день и 40/мин, Matrix 2 500/день и 60/мин. Смотрите дашборд.

Условия коммерческого использования на прочитанных страницах **не найдены**. Self-hosted ORS поддерживает directions и matrix.

### 3.5 GraphHopper Matrix API

**Эндпоинты:**
- Синхронно: `POST https://graphhopper.com/api/1/matrix?key=KEY` (есть и `GET /matrix`).
- Асинхронно: `POST .../matrix/calculate`, затем `GET .../matrix/solution/{jobId}`.

**Тело запроса:** `points` или `from_points`/`to_points`, `profile`, `out_arrays` (`weights`, `times`, `distances`), `fail_fast`.

**Ошибки:** при `fail_fast=false` в ответе `hints` с `invalid_from_points`, `invalid_to_points`, `point_pairs`. Точный текст сообщения («Cannot find from_points…») **не подтверждён**.

**Кредиты** — **не «за пару»**: «#origins * #destinations / 2 credits» или `MAX_OF(#origins, #destinations) * 10`, если это меньше. Для 41×41: min(840.5, 410) = **410 кредитов** за матрицу.

**Планы:**

| План | Цена | Кредитов в день | Условия |
|---|---|---|---|
| Free | €0 | 500 | только некоммерческий |
| Basic | €69/мес | 5 000 | — |
| Standard | €199/мес | 15 000 | — |
| Premium | €479/мес | 50 000 | — |

Граф OSM, так что для Душанбе не лучше self-hosted OSRM.

---

## 4. Оптимизация маршрута (TSP/VRP)

### 4.1 Google Route Optimization API

**Эндпоинты:**
- `POST https://routeoptimization.googleapis.com/v1/{parent=projects/*}:optimizeTours` (`parent` = `projects/{project-id}` или `projects/{project-id}/locations/{location-id}`).
- `batchOptimizeTours` — пакетный вариант (LRO).

**Аутентификация — OAuth:**
- Scope `https://www.googleapis.com/auth/cloud-platform`, IAM-разрешение `routeoptimization.locations.use`.
- Страница настройки требует «include an OAuth token with all API or SDK requests».
- API-ключи в reference не упоминаются. Используйте сервисный аккаунт или ADC.

```json
{
  "timeout": "5s",
  "model": {
    "globalStartTime": "2026-09-16T04:00:00Z",
    "globalEndTime": "2026-09-16T14:00:00Z",
    "shipments": [{
      "label": "order-1042",
      "deliveries": [{
        "arrivalWaypoint": {"location": {"latLng": {"latitude": 38.5615, "longitude": 68.7391}}},
        "duration": "300s",
        "timeWindows": [{"startTime": "2026-09-16T06:00:00Z", "endTime": "2026-09-16T08:00:00Z"}]
      }]
    }],
    "vehicles": [{
      "startWaypoint": {"location": {"latLng": {"latitude": 38.5600, "longitude": 68.7739}}},
      "endWaypoint": {"location": {"latLng": {"latitude": 38.5600, "longitude": 68.7739}}},
      "costPerHour": 27
    }]
  }
}
```

Душанбе UTC+5: время передаётся в UTC. `TimeWindow` также поддерживает `softStartTime` и `softEndTime`.

**Запрос и ответ:**
- Ответ: `routes[]` (с `visits[]`), `skippedShipments[]` (неразрешимые, например невозможное окно), `validationErrors`, `metrics`, `requestLabel`, `totalCost`.
- Прочие поля запроса: `considerRoadTraffic`, `populatePolylines`, `searchMode`, `solvingMode`, `useGeodesicDistances`, `label`.

**Квоты:**
- `optimizeTours`: 60 QPM.
- `batchOptimizeTours`: 60 QPM, ≤100 MB на запрос, ≤100 запросов в батче.

**Цены** (тарификация «based on the number of shipments in each request»):

| SKU | Когда применяется | Бесплатно в месяц | Цена за 1 000 по тирам |
|---|---|---|---|
| Single Vehicle Routing (Pro) | запрос с 1 машиной | 5 000 | $10.00 / $4.00 / $2.00 / $0.80 / $0.70 |
| Fleet Routing (Enterprise) | запрос с 2+ машинами | 1 000 | $30.00 / $14.00 / $6.00 / $2.40 / $2.10 |

Не тарифицируются запросы, не прошедшие валидацию, режим `VALIDATE_ONLY`, неразрешимые и проигнорированные shipments.

**Стоимость для проекта:**

| Сценарий | Shipments/мес | Стоимость |
|---|---|---|
| 1 машина, 1 план/день | 40 × 30 = 1 200 | **$0** |
| 1 машина, 4 перепланирования/день | 4 800 | $0, но близко к лимиту 5 000 |
| 2 курьера, 1 план/день | 1 200 | (1 200 − 1 000) × $0.03 ≈ **$6/мес** |
| 2 курьера, 4 перепланирования/день | 4 800 | ≈ **$114/мес** |

**Политики:**
- «Route Optimization API results can be displayed on Google Maps only; using them on other maps is forbidden».
- «Content pre-fetching, caching, or storage is generally prohibited, except for place IDs».
- SST 2024-05-22 допускает кэш lat/lng ≤30 дней.

### 4.2 2GIS TSP API

**Эндпоинты** (асинхронно):
- `POST https://routing.api.2gis.com/logistics/vrp/2.0/create?key=KEY`;
- затем `GET https://routing.api.2gis.com/logistics/vrp/2.0/status?task_id=...&key=KEY`.

**Тело запроса:**
- `waypoints[]`: координаты, окна времени, груз;
- `agents[]`: курьеры, вместимость, стоимость;
- `depots` (опционально);
- `options`: пробки, тип маршрутизации, дата, часовой пояс.

**Статусы:** `Run`, `Done`, `Partial` (исключены точки или агенты), `Fail`.

**Лимиты:** до **350 точек и 200 курьеров** по обзору, ru и en. Поисковый сниппет с «4000 points» противоречит документации.

**Возможности:** жёсткие и мягкие окна, вместимость, несколько курьеров, депо.

**Тарификация:** «количество точек × количество курьеров»; 10 000 единиц — 14 000 ₽/мес.

**Лицензия:** та же оферта WebAPI; вопрос диспетчеризации см. §2.4.

### 4.3 GraphHopper Route Optimization API

`POST https://graphhopper.com/api/1/vrp?key=KEY`. Синхронный эндпоинт не обработает задачи, которые «take longer than 10 seconds to solve»; для них асинхронный `/vrp/optimize`.

```json
{"vehicles": [{"vehicle_id": "courier-1",
               "start_address": {"location_id": "kitchen", "lon": 68.7739, "lat": 38.56},
               "return_to_depot": true, "earliest_start": 32400, "latest_end": 64800}],
 "services": [{"id": "order-1042",
               "address": {"location_id": "o1042", "lon": 68.7391, "lat": 38.5615},
               "duration": 300, "time_windows": [{"earliest": 36000, "latest": 43200}]}]}
```

**Ответ:**
- `status`: `waiting_in_queue` / `processing` / `finished`;
- `solution`: `costs`, `distance`, `transport_time`, `no_unassigned`, `routes[].activities[]` (`arr_time`, `end_time`);
- `unassigned.details[].code`:

| Код | Причина |
|---|---|
| 1 | skill |
| 2 | окно времени |
| 3 | вместимость |
| 21–27 | ограничения связей, машин, времени |
| 50 | точка недостижима по дороге |

**Кредиты:** «#vehicles x #locations», минимум 10.

**Лимиты локаций по планам:**

| План | Машин | Локаций | Цена |
|---|---|---|---|
| Free | 1 | — | некоммерческий |
| Basic | 2 | 30 | **мало для 40 точек + склад** |
| Standard | 10 | 80 | €199/мес |
| Premium | 20 | 200 | — |

### 4.4 openrouteservice optimization

- Публичный API — обёртка над **VROOM**.
- Недоступен в собственной инсталляции ORS (как и geocoder).
- Restrictions: 3 vehicles.
- SLA нет.

### 4.5 Локальный солвер (рекомендуется)

При 5–40 точках, одном складе и 1–2 курьерах локальные эвристики дают близкий к оптимуму результат за доли секунды. Это общее алгоритмическое знание, не мой бенчмарк; наивная реализация на Python ниже при n≈41 может работать до ~секунды.

**Варианты:**
1. **Чистый TSP без окон:** nearest neighbour → 2-opt → Or-opt. Считайте полную стоимость маршрута, чтобы корректно учесть асимметричные длительности OSRM (одностороннее движение).
2. **Окна «доставить 10:00–12:00», время обслуживания, 2+ курьера, вместимость:** OR-Tools routing (`pip install ortools`; `from ortools.constraint_solver import pywrapcp, routing_enums_pb2`).
   - Классы: `pywrapcp.RoutingIndexManager`, `pywrapcp.RoutingModel`.
   - Методы: `RegisterTransitCallback`, `SetArcCostEvaluatorOfAllVehicles`, `AddDimension` (время), `GetDimensionOrDie`, `CumulVar(index).SetRange(e, l)`, `SolveWithParameters`.
   - Поиск: `FirstSolutionStrategy.PATH_CHEAPEST_ARC` + `LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH` с `time_limit`.
3. **Или VROOM:** BSD-2-Clause; TSP, CVRP, VRPTW, MDHVRPTW, PDPTW; работает с OSRM, openrouteservice, Valhalla или собственной матрицей. Последняя стабильная v1.15.0 — по newreleases.io, на GitHub не перепроверено. Запускается рядом с OSRM в Docker.

```python
# Минимальный NN + 2-opt с проверкой окон (все времена в секундах; T = durations из OSRM)
def cost(r, T):
    return sum(T[a][b] for a, b in zip(r, r[1:]))

def feasible(r, T, tw, svc, t0):
    t = t0
    for a, b in zip(r, r[1:]):
        t += svc[a] + T[a][b]
        e, l = tw[b]
        if t > l:
            return False
        t = max(t, e)  # ждать, если приехали раньше
    return True

def nearest_neighbour(T, depot=0):
    left, r = set(range(len(T))) - {depot}, [depot]
    while left:
        nxt = min(left, key=lambda j: T[r[-1]][j])
        r.append(nxt)
        left.remove(nxt)
    return r + [depot]

def two_opt(r, T, ok):
    best, improved = r, True
    while improved:
        improved = False
        for i in range(1, len(best) - 2):
            for k in range(i + 1, len(best) - 1):
                cand = best[:i] + best[i:k + 1][::-1] + best[k + 1:]
                if cost(cand, T) < cost(best, T) - 1e-6 and ok(cand):
                    best, improved = cand, True
    return best
# ok = lambda r: feasible(r, T, tw, svc, t0)
# Если стартовый NN-маршрут уже нарушает окна, 2-opt его не исправит -> OR-Tools/VROOM.
```

**Почему локально, а не платный API:**
- нет платы за вызов и квот;
- нет OAuth и сервисного аккаунта;
- нет лицензионной привязки «показывать только на карте Google/Яндекса»;
- работает офлайн рядом с OSRM;
- легко добавить правила пекарни (горячее первым, хрупкие торты, флаг «позвонить заранее»);
- дёшево перепланировать при новом заказе.

Google Route Optimization можно держать как опциональный A/B-бенчмарк: при этом объёме он бесплатен при одном курьере.

---

## 5. Предлагаемый пайплайн (FastAPI)

1. LLM извлекает структурированный адрес из DM на русском или таджикском.
2. Если клиент прислал пин или подтвердил место по ссылке, сохраните координаты (собственные данные) и **пропустите геокодирование**.
3. Иначе вызовите основной геокодер с ограничением страной и смещением к Душанбе.
   - **AUTO_ACCEPT**, если **обязательно** точка внутри bbox Душанбе и выполнено одно из:
     - Google: один результат, нет `partial_match` (v3), `ROOFTOP`/`RANGE_INTERPOLATED` (`location_type` или `granularity`);
     - Яндекс: `precision=exact`;
     - 2GIS: `type=building` и `total==1`.
   - **NEEDS_REVIEW**, если любое из:
     - несколько результатов;
     - `partial_match`;
     - `GEOMETRIC_CENTER` / `APPROXIMATE`;
     - `precision` in {`street`, `other`, `range`, `near`};
     - Nominatim `addresstype` = borough/suburb/neighbourhood.
   - **NOT_FOUND**: `ZERO_RESULTS` / пустой `results` (v4) / `found=0` / 404 `itemNotFound` / `[]`.
4. Для NEEDS_REVIEW и NOT_FOUND:
   - попробуйте фоллбэк (2GIS, затем Nominatim для центроида района);
   - оператор подтверждает в админке;
   - бот отправляет «Пожалуйста, отправьте геолокацию / Лутфан, ҷойгиршавиро фиристед».
5. Вендорские геокоды считайте временными: Google lat/lng ≤30 дней, 2GIS хранить нельзя. Долгосрочно храните только подтверждённые пины и уточняйте их GPS курьера при вручении.
6. Ночью или по запросу:
   - постройте OSRM `table` по подтверждённым точкам дня;
   - запустите локальный солвер (NN+2-opt или OR-Tools с окнами);
   - отправьте курьеру упорядоченный список с deep link в навигатор.
7. Повторы:
   - backoff на `OVER_QUERY_LIMIT` / `UNKNOWN_ERROR` / HTTP 408/429/5xx;
   - никогда не повторять `REQUEST_DENIED` / `INVALID_REQUEST` / `OVER_DAILY_LIMIT` / HTTP 400/403;
   - публичный Nominatim: ≤1 запрос/с, реальный User-Agent, кэш; периодические скрипты ≤4 запроса/мин.

**Бенчмарк до выбора:**
- Возьмите ~100 реальных адресов из прошлых DM.
- Прогоните через Google (v3 и v4), 2GIS (демо-ключ), Nominatim и Яндекс, если когда-нибудь будете его лицензировать.
- Измерьте долю автопринятия и ошибку в метрах относительно точек, подтверждённых курьером.
- Основной геокодер выбирайте по данным. Ранжирование выше — оценка документированных рисков, а не измерение.

---

## 6. Ограничения и риски

### 6.1 Неподтверждённое и оговорки

- **Качество.** Ни один провайдер не публикует качество геокодирования Душанбе на уровне дома.
- **2GIS API:**
  - покрытие Душанбе в данных API явно не документировано (есть только косвенные локали `tg_TJ`/`ru_TJ`);
  - допустима ли курьерская диспетчеризация по платной подписке, нужно подтвердить письменно: правила 2022 года её запрещают, оферта 2026 года о ней молчит.
- **Google Service Specific Terms.** Текст про кэш и non-Google map проверен в версии 2024-05-22 и на страницах policies. Актуальная версия (по индексу изменена 2026-06-04) не прочитана.
- **Google Geocoding v4:**
  - точная форма пустого ответа — **не подтверждено**;
  - маппинг SKU для v4-address — **не подтверждено**;
  - `partial_match` в полях `GeocodeResult` отсутствует;
  - неофициальные названия мест v4 официально не поддерживает.
- **Google Route Optimization:** что API-ключи не принимаются, прямо не сказано; документация требует OAuth-токен.
- **openrouteservice:** дневные квоты — **не подтверждено**; пример «3,500 (e.g. 50 x 50)» на странице restrictions противоречив.
- **2GIS:** цена пакета Distance Matrix ~50 тыс. единиц и точный JSON ошибки 404 геокодера — **не подтверждено**.
- **Яндекс:** допускает ли платная лицензия диспетчерские сценарии — **не подтверждено**.
- **OSRM:**
  - дефолт `--max-table-size`=100 подтверждён issue и сторонними источниками, не справкой;
  - что v6.0.0 — последний semver-релиз, **не подтверждено**.
- **VROOM:** v1.15.0 как последний релиз — по newreleases.io.
- **Ресурсы self-host** для выгрузки 46 MB — оценка. Документированы только цифры для всей планеты (Nominatim: 128 GB RAM / 1 TB).
- **OR-Tools** не бенчмаркался; скорость «доли секунды» — общее знание.
- **Не исследовано:**
  - политика тайлов для своей страницы «подтвердите место» (OSM tile policy, провайдеры MapLibre);
  - цена Google Maps JS для админки;
  - доступность геолокаций из Instagram DM через Messaging API.

### 6.2 Бизнес-риски

- **Юридическая неопределённость «Google-координаты в OSRM».** Минимизируйте её подтверждёнными пинами.
- **Устаревание OSM-графа.** Нужна еженедельная пересборка и обратная связь от курьеров.
- **Публичный Nominatim** без SLA и с риском блокировки при нарушении политики: кэш обязателен, при росте — self-host.
- **Санкционные и платёжные риски** оплаты российских сервисов (2GIS, Яндекс) из Таджикистана и Google Cloud биллинга: **не исследовано**.

### 6.3 Исправления относительно черновика

1. **Nominatim:** добавлено правило «scripts running longer than a day and scripts that are run at regular intervals are restricted to 4 requests per minute» и риск блокировки за повторяющиеся одинаковые запросы.
2. **OSRM demo:** текущая вики говорит «Do not exceed 1 request per second» и «reasonable, non-commercial use-cases». Цитаты черновика «Excessive use is not allowed» / «Access … shall be withdrawn» на текущей странице не найдены.
3. **Яндекс:**
   - уточнены дословные формулировки 5.1.3, 5.1.5 (кэш только для улучшения работы Сервисов, ≤30 дней) и 6.6.1 (Геокодер только с JS API, запрет показывать на картах третьих лиц);
   - перерасход: 390 ₽ за тир 1 000/сутки, 195 ₽ за 10 000, 163 ₽ за 25–100 тыс.
4. **Google SST:**
   - дата актуальной версии по индексу 2026-06-04, а не 2026-06-10 (это дата EEA SST);
   - ограничения 30 дней и non-Google map прописаны также для Routes API и Route Optimization API.
5. **Google v4:**
   - в полях `GeocodeResult` нет `partial_match`;
   - есть `GRANULARITY_UNSPECIFIED`, `bounds`, `plusCode`, `postalCodeLocalities`;
   - v4 не поддерживает «Historical or unofficial place names»;
   - уточнены OAuth scopes и `key=` в примерах;
   - поддерживаемый путь — `/v4/geocode/address/{addressQuery}`.
6. **Route Optimization:**
   - при 2 курьерах и 4 перепланированиях в день ≈ $114/мес, а не $6 ($6 — только без перепланирования);
   - добавлены политика «caching … generally prohibited, except for place IDs» и статус `requestLabel`.
7. **GraphHopper:**
   - Matrix тарифицируется не «за пару», а по формуле `origins*destinations/2` или `max*10` (41×41 = 410 кредитов);
   - VRP — `vehicles × locations`;
   - добавлены дневные кредиты и лимиты машин по планам;
   - текст ошибки «Cannot find from_points» не подтверждён, документирован `hints`.
8. **2GIS:**
   - в TSP добавлен статус `Run` и подтверждён хост `routing.api.2gis.com`;
   - цена TSP 14 000 ₽ за 10 тыс. единиц;
   - «Geocoder 5M = 175 000 ₽» не подтверждено (подтверждено 1M = 70 000 ₽);
   - добавлены атрибуция 2.10 (ссылка http://dev.2gis.ru) и п. 4.2 старых правил (временное кэширование геокодинга);
   - утверждение про «Route Planner API» (задача китайского почтальона) не подтверждено и удалено.
9. **openrouteservice:**
   - квоты черновика (Directions 10 000/день, Geocoding 15 000/день) не подтверждены: другие источники дают Directions 2 000/день;
   - добавлено ограничение «25 (e.g. 5 x 5)» при dynamic arguments.
10. **OSRM:**
    - список кодов расширен (`TooBig`, `DisabledDataset` и др.);
    - POST поддерживают `route`/`table`/`match`;
    - `fallback_coordinate` добавлен.
11. **Прочее:**
    - бесплатный лимит Route Optimization при 4 перепланированиях — 4 800 из 5 000, запас минимальный;
    - наивный 2-opt на Python при n=41 работает не «миллисекунды», а до ~1 с.

---

## 7. Источники

**Google Maps Platform**
- https://developers.google.com/maps/documentation/geocoding/requests-geocoding
- https://developers.google.com/maps/documentation/geocoding/release-notes
- https://developers.google.com/maps/documentation/geocoding/geocoding-v4-overview
- https://developers.google.com/maps/documentation/geocoding/geocoding
- https://developers.google.com/maps/documentation/geocoding/reference/rest/v4/geocode.address/geocodeAddress
- https://developers.google.com/maps/documentation/geocoding/reference/rest/v4/geocode.address/geocodeAddressQuery
- https://developers.google.com/maps/documentation/geocoding/reference/rest/v4/GeocodeResult.Granularity
- https://developers.google.com/maps/documentation/geocoding/usage-and-billing
- https://developers.google.com/maps/documentation/geocoding/policies
- https://developers.google.com/maps/coverage
- https://developers.google.com/maps/billing-and-pricing/pricing
- https://developers.google.com/maps/documentation/routes/compute_route_matrix
- https://developers.google.com/maps/documentation/routes/usage-and-billing
- https://developers.google.com/maps/documentation/routes/policies
- https://developers.google.com/maps/documentation/route-optimization/reference/rest/v1/projects/optimizeTours
- https://developers.google.com/maps/documentation/route-optimization/reference/rest/v1/ShipmentModel
- https://developers.google.com/maps/documentation/route-optimization/usage-and-billing
- https://developers.google.com/maps/documentation/route-optimization/policies
- https://developers.google.com/maps/documentation/route-optimization/oauth-token
- https://cloud.google.com/maps-platform/terms/maps-service-terms (актуальная версия, прочитана не полностью)
- https://cloud.google.com/maps-platform/terms/maps-service-terms/index-20240522

**Яндекс**
- https://yandex.ru/maps-api/docs/geocoder-api/request.html
- https://yandex.ru/maps-api/docs/geocoder-api/response.html
- https://yandex.ru/maps-api/products/geocoder-api
- https://yandex.ru/legal/maps_api/
- https://yandex.ru/dev/tariffs/doc/ru/geocoder/prices/
- https://yandex.ru/dev/maps/tariffs/doc/geosearch/prices/index.html

**2GIS**
- https://vecherka.tj/archives/71308
- https://2gis.tj/ru/dushanbe
- https://docs.2gis.com/ru/api/search/geocoder/overview
- https://docs.2gis.com/ru/api/search/geocoder/reference/3.0/items/geocode
- https://docs.2gis.com/en/api/search/regions/overview
- https://docs.2gis.com/ru/api/search/regions/reference/2.0/region/list
- https://docs.2gis.com/ru/api/navigation/distance-matrix/overview
- https://docs.2gis.com/ru/api/navigation/distance-matrix/reference/get_dist_matrix
- https://docs.2gis.com/ru/api/navigation/tsp/overview
- https://docs.2gis.com/en/api/navigation/tsp/overview
- https://docs.2gis.com/platform-manager/subscription/pricing
- https://law.2gis.ru/offer-license-agreement-webapi
- https://law.2gis.ru/api-rules

**OpenStreetMap / Nominatim / Geofabrik**
- https://operations.osmfoundation.org/policies/nominatim/
- https://nominatim.org/release-docs/latest/api/Search/
- https://nominatim.org/release-docs/latest/admin/Installation/
- https://nominatim.openstreetmap.org/search?q=Душанбе, Сино&format=jsonv2&countrycodes=tj&limit=5&addressdetails=1 (живой тест 2026-09-15)
- https://download.geofabrik.de/asia/tajikistan.html

**OSRM**
- https://github.com/Project-OSRM/osrm-backend/blob/master/README.md
- https://github.com/Project-OSRM/osrm-backend/blob/master/docs/http.md
- https://github.com/Project-OSRM/osrm-backend/releases
- https://github.com/Project-OSRM/osrm-backend/tags
- https://github.com/Project-OSRM/osrm-backend/releases/tag/v6.0.0
- https://github.com/Project-OSRM/osrm-backend/issues/1761
- https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server
- https://routing.openstreetmap.de/about.html

**openrouteservice / Pelias**
- https://openrouteservice.org/restrictions/
- https://giscience.github.io/openrouteservice/api-reference/endpoints/geocoder/
- https://giscience.github.io/openrouteservice/api-reference/endpoints/matrix/
- https://github.com/pelias/documentation/blob/master/result_quality.md
- https://account.heigit.org/info/plans (не читается без JS)

**GraphHopper**
- https://www.graphhopper.com/pricing/
- https://docs.graphhopper.com/openapi/route-optimization/solvevrp.md
- https://docs.graphhopper.com/openapi/matrices
- https://docs.graphhopper.com/openapi/matrices/getmatrix.md
- https://support.graphhopper.com/support/solutions/articles/44000718211-what-is-one-credit-

**Солверы**
- https://github.com/VROOM-Project/vroom
- https://newreleases.io/project/github/VROOM-Project/vroom/release/v1.15.0
- https://developers.google.com/optimization/routing/vrptw
