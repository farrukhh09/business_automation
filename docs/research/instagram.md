# Instagram DM: интеграция для домашней пекарни (Душанбе)

Дата проверки: 2026-09-15. Каждое ключевое утверждение перепроверено по официальной документации Meta for Developers. Страницы читались через fetch-инструмент, который пересказывает содержимое. Поэтому перед написанием кода сверяйте фрагменты с живой страницей.

Meta переносит документацию с `/docs/...` на `/documentation/...`, и пока доступны обе копии. Местами они расходятся: например, в одной из копий страницы sender actions пример `mark_seen` содержит `typing_on`.

Метка **«не подтверждено»** означает, что в официальных документах этого найти не удалось.

---

## 1. Вывод и рекомендация

**Используйте Instagram API with Instagram Login (Business Login for Instagram), хост `graph.instagram.com`.**

- **Facebook Page не нужна.** В обзоре Instagram Platform для этой конфигурации указано: хост `graph.instagram.com`, токен Instagram User, «Facebook Page: Not required». Это удобно для пекарни, у которой есть только профессиональный аккаунт Instagram.
- **Переписка поддерживается напрямую.** Там же: у Instagram Login messaging поддерживается нативно, у Facebook Login — «via Messenger Platform».
- **Для DM доступно всё нужное:**
  - приём сообщений через webhooks и отправка через Send API (текст, изображения, audio/video/PDF);
  - профиль отправителя (User Profile API);
  - sender actions `typing_on`, `typing_off`, `mark_seen` (changelog 2025-09-23);
  - webhook `message_edit` (2025-09-10);
  - PDF-вложения (2025-12-19);
  - несколько изображений в одном сообщении (бета с 2025-11-03, для всех аккаунтов с 2026-05-06);
  - отправка изображений по `attachment_id` (2026-03-13).
- **Чего нет в Instagram Login:** hashtag search, product tagging, partnership ads. Для DM это не важно.
  - *Исправление черновика:* там было написано «cannot access ads or tagging». Сейчас обзор перечисляет именно эти три функции.
- **App Review не нужен для одного своего бизнеса.** Страница App Review: *«My app is only for a business I own or manage»* → Standard Access, ревью не требуется. Advanced Access (App Review и Business Verification) нужен Tech Provider, чьё приложение обслуживает несколько бизнесов.
- **Главный риск.** В документации Messenger-варианта сказано: «Apps with Standard Access can only send messages to people that have a role on the app». Для Instagram Login такой фразы нет, но и обратного явно не сказано (**не подтверждено**). Обычные клиенты пекарни ролей в приложении не имеют. **Первым делом проверьте на реальном стороннем аккаунте**, что webhook приходит и ответ доставляется. Если нет, подавайте на Advanced Access.
- **Альтернатива: Instagram API with Facebook Login for Business / Messenger Platform** (`graph.facebook.com`, Page access token, `instagram_basic` + `instagram_manage_messages` + `pages_show_list` + `pages_read_engagement`, для webhooks ещё `pages_manage_metadata`). Выбирайте её, только если аккаунт уже привязан к Page и нужен единый inbox Messenger+Instagram или handover protocol.
  - Одно приложение может использовать **либо** Facebook Login, **либо** Instagram Login, но не оба сразу (страница App Review).

**Версия Graph API**

| Версия | Выпуск | Доступна до |
|---|---|---|
| v26.0 (последняя) | 2026-07-29 | «уточняется» |
| v25.0 | 2026-02-18 | 2028-07-29 |

- Все примеры в документации Instagram messaging и webhooks по-прежнему используют **`v25.0`**.
- В changelog v26.0 нет изменений Instagram messaging или webhooks. Из связанного с Instagram/Messenger там только: убрано размещение Instagram Explore и значение `story` в `messenger_positions`.
- **Рекомендация:** держите версию в конфиге (`IG_GRAPH_VERSION=v25.0`), проверьте v26.0 на тестовом аккаунте и затем переключитесь.

---

## 2. Две конфигурации: сравнение

| | Instagram API with Instagram Login | Instagram API with Facebook Login for Business |
|---|---|---|
| Для кого | аккаунты «with a presence on Instagram only» | аккаунты, привязанные к Facebook Page |
| Логин | учётные данные Instagram | учётные данные Facebook |
| Хост | `graph.instagram.com` | `graph.facebook.com` |
| Токен | Instagram User | Facebook User или Page |
| Facebook Page | не нужна | обязательна |
| Разрешения (все) | `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`, `instagram_business_manage_messages` | `instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`, `instagram_manage_insights`, `instagram_manage_messages`, `pages_show_list`, `pages_read_engagement` |
| Для сообщений | `instagram_business_manage_messages` + feature **Human Agent** | `instagram_manage_messages` + Human Agent |
| Messaging | нативно | через Messenger Platform |

- **Старые scope** (`business_basic`, `business_manage_messages` и т. д.) устарели **27 января 2025**. Используйте значения `instagram_business_*`.
- **Instagram Basic Display API закрыт 4 декабря 2024** (блог Meta «Update on Instagram Basic Display API»).

---

## 3. Настройка приложения, разрешения, доступ, токены

### 3.1 Настройка приложения (Instagram Login)
Страница «Get started» для Instagram Login:
1. Нужны: Meta app типа **Business**, профессиональный аккаунт Instagram и access token.
2. В App Dashboard добавьте Instagram use case (Manage messaging & content on Instagram). Откройте **Instagram → API setup with Instagram business login**.
   - Там показаны **Instagram App ID / Instagram App Secret**. Они отличаются от Meta App ID / App Secret.
   - Страница «create-an-instagram-app» при чтении не отдала текст шагов, поэтому точные названия пунктов дашборда частично **не подтверждены**.
3. **Generate token** напротив нужного аккаунта → войти в Instagram → скопировать токен.
   - По Get Started, токены из дашборда «long-lived and are valid for 60 days».
   - Токены из Business Login flow изначально живут 1 час.
4. **Configure webhooks:** Callback URL (HTTPS с валидным сертификатом, self-signed не поддерживаются), Verify Token, подписка на поля.
5. **Set up Instagram business login** (OAuth redirect URI). Нужен, только если вы проводите OAuth сами. Instagram Login поддерживается только на платформе «Web or mobile Web» (страница App Review).
6. **Переведите приложение в Live.** Страница Instagram webhooks: *«Apps must be set to **Live** in the App Dashboard to receive webhook notifications.»* Messenger-вариант: *«Your app must be published, regardless of app review status, to receive webhooks.»*
7. **Проверьте ID аккаунта:** `GET https://graph.instagram.com/v25.0/me?fields=user_id,username`.
   - `user_id` — это «Instagram professional account ID» вашего пользователя. Именно он приходит в `entry[].id` webhooks.
   - `id` — app-scoped ID.
   - `user_id` из ответа обмена кода — «Instagram-scoped user ID». **Не используйте его** для сопоставления с `entry[].id`.

### 3.2 Разрешения
- `instagram_business_basic` — базовое.
- `instagram_business_manage_messages` — чтение и отправка сообщений.
- Поля webhooks `messages`, `message_reactions`, `messaging_seen`, `messaging_postbacks`, `messaging_referral`, `messaging_optins`, `messaging_handover`, `standby` требуют `instagram_business_basic` + `instagram_business_manage_messages`.

### 3.3 Уровни доступа и App Review
- **Standard Access.** Общая страница Access Levels: разрешения стандартного уровня можно запрашивать только у пользователей с ролью в приложении. Страница App Review: для «a business I own or manage» ревью не нужен.
- **Advanced Access** нужен, если вы обслуживаете чужие профессиональные аккаунты (Tech Provider). Требует App Review (пошаговая инструкция, скринкаст, минимум 1 успешный вызов на каждое разрешение) и Business Verification.
- **Webhooks.** Страница Instagram webhooks требует Advanced Access **только для `comments` и `live_comments`**. Для `messages` Advanced Access явно не требуется.
- **Не подтверждено:** будет ли Standard Access доставлять webhooks и позволять отвечать клиентам без роли в приложении. Для Messenger-варианта есть ограничение «only send messages to people that have a role on the app». Проверьте в первый же день.
- **Не подтверждено:** нужен ли сейчас переключатель «Allow access to messages» (Connected tools) в приложении Instagram. На текущих официальных страницах он не упоминается. Проверьте при подключении аккаунта.

### 3.4 Токены (Business Login for Instagram)

**Шаг 1. Authorize**
```
https://www.instagram.com/oauth/authorize
  ?client_id=<INSTAGRAM_APP_ID>
  &redirect_uri=<REDIRECT_URI>
  &response_type=code
  &scope=instagram_business_basic,instagram_business_manage_messages
  &state=<CSRF>
```
- Необязательные параметры: `enable_fb_login` (добавлен 2026-02-06) и `force_reauth`.
- Код «valid for 1 hour and can only be used once».
- `#_` в конце redirect не является частью кода, его нужно отрезать.

**Шаг 2. Code → short-lived token (1 час)**
```bash
curl -X POST https://api.instagram.com/oauth/access_token \
  -F 'client_id=<INSTAGRAM_APP_ID>' \
  -F 'client_secret=<INSTAGRAM_APP_SECRET>' \
  -F 'grant_type=authorization_code' \
  -F 'redirect_uri=<REDIRECT_URI>' \
  -F 'code=<CODE>'
```
```json
{
  "data": [
    {
      "access_token": "EAACEdEose0...",
      "user_id": "1020...",
      "permissions": "instagram_business_basic,instagram_business_manage_messages,instagram_business_manage_comments,instagram_business_content_publish"
    }
  ]
}
```

**Шаг 3. Short-lived → long-lived (60 дней)**
```
GET https://graph.instagram.com/access_token
  ?grant_type=ig_exchange_token
  &client_secret=<INSTAGRAM_APP_SECRET>
  &access_token=<SHORT_LIVED_TOKEN>
```
```json
{ "access_token": "EAACEdEose0...", "token_type": "bearer", "expires_in": 5183944 }
```

**Шаг 4. Refresh**
```
GET https://graph.instagram.com/refresh_access_token
  ?grant_type=ig_refresh_token
  &access_token=<LONG_LIVED_TOKEN>
```
```json
{ "access_token": "c3oxd...", "token_type": "bearer", "expires_in": 5183944 }
```
- **Условия refresh:** токен «at least 24 hours old but has not expired», long-lived, пользователь выдал `instagram_business_basic`.
- «Refreshed tokens are valid for 60 days from the date at which they are refreshed».
- Истёкший токен обновить нельзя: владелец аккаунта должен залогиниться заново.
- **План для бэкенда:**
  - храните `expires_at = now + expires_in`;
  - раз в сутки запускайте job: если осталось меньше ~30 дней и токену больше 24 часов, делайте refresh;
  - при ошибке показывайте алерт в админке (Next.js).

---

## 4. Webhooks

### 4.1 Верификация (GET)
Meta присылает:
```
GET https://<your-host>/webhooks/instagram?hub.mode=subscribe&hub.challenge=1158201444&hub.verify_token=<YOUR_VERIFY_TOKEN>
```
- `hub.mode` — «Always set to `subscribe`».
- `hub.challenge` — «An `int` you must pass back to us».
- `hub.verify_token` — строка из поля Verify Token в дашборде.
- **Ответ:** если токен совпал, вернуть значение `hub.challenge` телом ответа (200). Иначе 403.

### 4.2 Подпись (POST)
- **Заголовок:** `X-Hub-Signature-256: sha256=<hex>`.
- **Документация:** «Generate a **SHA256** signature using the payload and your app's **App Secret**. Compare your signature to the signature in the `X-Hub-Signature-256` header (everything after `sha256=`).» На практике это HMAC-SHA256 над **сырыми байтами** тела запроса.
- Страница Graph API Webhooks предупреждает, что payload содержит экранированный unicode. Поэтому **не пересериализуйте JSON**, считайте HMAC по `await request.body()`.
- **Не подтверждено:** какой секрет использовать, Instagram App Secret или Meta App Secret. В документации написано просто «App Secret». Сделайте секрет настраиваемым и проверьте на первой реальной доставке.

```python
import hmac, hashlib
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse

app = FastAPI()

@app.get("/webhooks/instagram")
async def verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(status_code=403)

@app.post("/webhooks/instagram")
async def receive(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401)
    await enqueue(raw)   # обработка в фоне; дедупликация по message.mid
    return Response(status_code=200)
```

### 4.3 Правила доставки (Graph API Webhooks, Getting Started)
- На каждое уведомление отвечайте `200 OK` (HTTPS).
- **Повторы:** при неудаче Meta «retry immediately, then try a few more times with decreasing frequency over the next 36 hours». Через 36 часов неподтверждённые уведомления отбрасываются. Нужна идемпотентность (дедупликация по `mid`).
- **Батчи:** уведомления агрегируются, до **1000 updates** в батче. Обходите все `entry[]` и все `messaging[]`.
- **TLS:** сертификат должен быть валидным, self-signed не поддерживаются.
- **mTLS (опционально):** CN клиентского сертификата `client.webhooks.fbclientcerts.com`, CA `meta-outbound-api-ca-2025-12.pem`.

### 4.4 Подписка аккаунта на поля
Официальный пример со страницы Instagram webhooks:
```bash
curl -i -X POST \
  "https://graph.instagram.com/v25.0/1755847768034402/subscribed_apps?subscribed_fields=comments,messages&access_token=EAAFB..."
```
Для пекарни:
```
POST https://graph.instagram.com/v25.0/me/subscribed_apps
  ?subscribed_fields=messages,message_reactions,messaging_seen,messaging_postbacks,messaging_referral,message_edit
  &access_token=<INSTAGRAM_USER_ACCESS_TOKEN>
```
Ответ: `{"success": true}`

- **Названия полей расходятся между страницами:**
  - страница Send Messages: `messaging_reactions`, `messaging_referrals`;
  - страница Webhooks и Webhooks Reference: `message_reactions`, `messaging_referral`.
  - Используйте названия со страницы Webhooks и сверьте со списком полей в дашборде.
- **Остальные поля:**
  - по таблице Webhooks: `messaging_optins`, `messaging_handover`, `standby`, `message_echoes`, `comments`, `live_comments`, `mentions`;
  - по Reference: `story_insights`, `message_edit`.
  - Какие разрешения требует `message_echoes`, **не подтверждено**. Эхо-сообщения в любом случае приходят в `messages` с `is_echo: true`.

### 4.5 Формат payload (страница Webhook Notification Examples)

**Конверт (официальный):**
```json
{
  "object": "instagram",
  "entry": [{
    "id": "<YOUR_APP_USERS_INSTAGRAM_USER_ID>",
    "time": <TIME_NOTIFICATION_WAS_SENT>,
    "messaging": [{
      "sender": {"id": "<SENDER_ID>"},
      "recipient": {"id": "<RECIPIENT_ID>"},
      "timestamp": <TIME_WEBHOOK_WAS_TRIGGERED>,
      "message": {"mid": "<MESSAGE_ID>"}
    }]
  }]
}
```
- `entry[].id` — «The Instagram professional account ID of your app user».
- **Причуда:** в официальных примерах postback и referral в `entry.id` стоит `<INSTAGRAM_SCOPED_ID>`. Поэтому бизнес надёжнее определять по сохранённому `user_id` или по `recipient.id` для входящих.

**Поля `message`:**
- `mid`, `text`
- `attachments[]` `{type, payload{url}}`
- флаги `is_deleted`, `is_echo`, `is_self` (сообщение самому себе, с 2025-11-10), `is_unsupported`
- `quick_reply{payload}`
- `reply_to{mid}` или `reply_to{story{url,id}}`. С 2025-12-12 для ответа на сторис добавлено поле `link_sticker_url`; его точное место в JSON **не подтверждено**.
- `referral{ref, ad_id, source:"ADS", type:"OPEN_THREAD", ads_context_data{ad_title, photo_url, video_url}}`

**Типы вложений (актуальная страница Instagram Login examples):**
> `media`, `audio`, `file`, `image` (image, gif, or sticker), `video`, `share` (legacy IG post share, deprecated after Feb 2026), `ig_post`, `story_mention`, `video`, `ig_reel`, `reel`, `story`, `ig_story`

- *Исправление черновика:* старый список «audio, file, image (image or sticker), share, story_mention, video, ig_reel or reel» и `ephemeral` без URL взяты со страницы **Messenger-варианта**. На странице Instagram Login тип `share` помечен deprecated после февраля 2026, добавлены `ig_post`, `story`, `ig_story`, `media`.
- Messenger-вариант также пишет: «Disappearing media (view once, allow replay) is not supported on Instagram media webhooks» и «Messages with gifs and stickers are not supported… a webhook will not be triggered». Instagram Login examples при этом указывают gif/sticker внутри `image`. Какое поведение актуально для Instagram Login, **не подтверждено**, проверьте на практике.
- Типы `post`, `sticker`, `fallback`, `template`, `appointment_booking` из черновика для Instagram Login **не подтверждены**.
- **Голосовые сообщения.** Тип `audio` в списке есть, но документация прямо не пишет, что голосовые приходят как `audio`. Контейнер и кодек файла тоже не описаны (**не подтверждено**). Определяйте MIME после скачивания и перекодируйте ffmpeg перед распознаванием речи (ru/tg).

**Эхо.** Сообщения, отправленные самим бизнесом, приходят с `is_echo: true`. Их нужно пропускать или сохранять как исходящую историю, иначе бот ответит сам себе.

#### Пример: входящий текст (русский)
ID и времена иллюстративные, структура по официальному примеру.
```json
{
  "object": "instagram",
  "entry": [
    {
      "id": "17841400000000000",
      "time": 1789459200123,
      "messaging": [
        {
          "sender":    { "id": "1234567890123456" },
          "recipient": { "id": "17841400000000000" },
          "timestamp": 1789459199876,
          "message": {
            "mid": "aWdfZAG1faXRlbToxOklHTWVzc2FnZAUlEOjE3ODQx...",
            "text": "Здравствуйте! Можно заказать торт Наполеон на субботу?"
          }
        }
      ]
    }
  ]
}
```

#### Пример: входящее голосовое (audio)
```json
{
  "object": "instagram",
  "entry": [
    {
      "id": "17841400000000000",
      "time": 1789459260456,
      "messaging": [
        {
          "sender":    { "id": "1234567890123456" },
          "recipient": { "id": "17841400000000000" },
          "timestamp": 1789459260111,
          "message": {
            "mid": "aWdfZAG1faXRlbToxOklHTWVzc2FnZAUlEOjE3ODQy...",
            "attachments": [
              { "type": "audio", "payload": { "url": "<URL_FOR_THE_MEDIA>" } }
            ]
          }
        }
      ]
    }
  ]
}
```

#### Другие события (официальные фрагменты внутри `messaging[]`)
```json
"reaction": { "mid": "<MESSAGE_ID>", "action": "react", "reaction": "love", "emoji": "\u{2764}\u{FE0F}" }
```
```json
"read": { "mid": "<MESSAGE_ID>" }
```
```json
"postback": { "mid": "<MESSAGE_ID>", "title": "<USER_SELECTED_ICEBREAKER_OPTION_OR_CTA_BUTTON>", "payload": "<OPTION_OR_BUTTON_PAYLOAD>" }
```
```json
"referral": { "ref": "<IGME_LINK_REF_PARAMETER_VALUE>", "source": "<IGME_SOURCE_LINK>", "type": "OPEN_THREAD" }
```
```json
"message_edit": { "mid": "<MESSAGE_ID>", "text": "<USER_EDITED_MESSAGE>", "num_edit": "<NUMBER_OF_TIMES_MESSAGE_IS_EDITED>" }
```
```json
"message": { "mid": "<MESSAGE_ID>", "is_deleted": true }
```

| Событие | Поле подписки |
|---|---|
| `reaction` | `message_reactions` |
| `read` | `messaging_seen` |
| `postback` (icebreaker или CTA-кнопка) | `messaging_postbacks` |
| `referral` (ссылка ig.me) | `messaging_referral` |
| `message_edit` | `message_edit` |

- В примере `message_edit` `num_edit` показан строковым плейсхолдером. Тип значения (число или строка) **не подтверждён**, парсите оба варианта.
- Messenger-вариант: при реакции самого бизнеса уведомление не отправляется.

### 4.6 Срок жизни CDN-ссылок и хранение медиа
- **Обзор Instagram Platform:** «The CDN URL is privacy-aware and will not return the media when the content has been deleted or has expired.» Точный TTL для вложений DM **не документирован**. Скачивайте голосовые и фото сразу при получении webhook.
- **Ограничение на хранение (story mentions).** Страница Story Mention (Messenger-вариант): «You must not store or cache the media content on your server». Хранить разрешено только CDN URL. Не сохраняйте медиа из `story_mention`.
- **Не подтверждено:** есть ли явное разрешение или запрет хранить обычные вложения DM (audio/image). Храните только то, что нужно для исполнения заказа, с ограниченным сроком хранения, и сверьтесь с Meta Platform Terms и Developer Policies.
- `profile_pic` из User Profile API: «The URL will expire in a few days».

---

## 5. Send API (Instagram Login)

**Endpoint** (официальный, версия из документации):
```
POST https://graph.instagram.com/v25.0/<IG_ID>/messages
Authorization: Bearer <INSTAGRAM_USER_ACCESS_TOKEN>
Content-Type: application/json
```
- Webhooks и Conversations API также используют форму `/me/...`. Что `/me/messages` работает на `graph.instagram.com`, **не подтверждено** явным примером на странице Send Messages. Используйте `<IG_ID>`, то есть `user_id` из `/me?fields=user_id`.
- **Ответ:** `{"recipient_id": "<IGSID>", "message_id": "<MESSAGE_ID>"}`
- **Предусловие:** «Only after an Instagram user has sent your app user's Instagram professional account a message can your app send a message to the Instagram user.»

### 5.1 Текст
```json
{
  "recipient": { "id": "1234567890123456" },
  "message":   { "text": "Здравствуйте! Торт «Наполеон» 1 кг на субботу можно. Подтвердите, пожалуйста, время доставки." }
}
```
- **Лимит:** «Message text must be UTF-8 and be a 1000 bytes or less». Считаются **байты**.
- Кириллица и таджикские буквы (ҳ ӣ ӯ ҷ қ ғ) занимают 2 байта в UTF-8, эмодзи — 4. Получается примерно 500 кириллических символов на сообщение.
- Messenger-вариант пишет «under 1,000 characters», но для Instagram Login ориентируйтесь на байты.

```python
def split_utf8(text: str, limit: int = 1000) -> list[str]:
    parts, cur = [], ""
    for ch in text:
        if len((cur + ch).encode("utf-8")) > limit:
            parts.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts
```
В проде лучше резать по границам предложений или слов.

### 5.2 Аудио
Официальный пример (для audio/video/file ключ `attachment` в единственном числе):
```json
{
  "recipient": { "id": "1234567890123456" },
  "message": {
    "attachment": {
      "type": "audio",
      "payload": { "url": "https://files.example-bakery.tj/voice/reply-8812.m4a" }
    }
  }
}
```

### 5.3 Изображение
По официальной странице:
```json
{
  "recipient": { "id": "1234567890123456" },
  "message": {
    "attachments": [
      { "type": "image", "payload": { "url": "https://files.example-bakery.tj/catalog/napoleon.jpg" } }
    ]
  }
}
```
- **Несогласованность документации:**
  - в примере с одним изображением ключ `"attachments"` содержит **объект** `{...}`;
  - в примере с несколькими изображениями `"attachments"` содержит **массив** (до 10 элементов);
  - в примере audio используется `"attachment"` (объект).
  - Для изображений используйте массив `attachments`: он покрывает и 1, и 10 изображений. Протестируйте также вариант с объектом.
- **`attachment_id` вместо `url`.** Официальный пример на `graph.instagram.com`:
  ```json
  "message": { "attachments": [ { "type": "image", "payload": { "attachment_id": "<attachment_ID>" } } ] }
  ```
  - Можно смешивать `url` и `attachment_id`.
  - Страница Attachment Upload API, на которую ссылается документация, описывает загрузку только через `POST https://graph.facebook.com/<VERSION>/<PAGE_ID>/message_attachments` с Page access token. Эндпоинт загрузки для Instagram Login (`graph.instagram.com`) **не подтверждён**. Отправляйте по публичному HTTPS URL.

### 5.4 Лимиты медиа (страница Send Messages)

| Тип | Форматы | Макс. размер |
|---|---|---|
| Audio | aac, m4a, wav, mp4 | 25MB |
| Image | png, jpeg | 8MB |
| Video | mp4, ogg, avi, mov, webm | 25MB |
| File | pdf | 25MB |

- До **10** изображений в одном сообщении.
- Страница Attachment Upload (Messenger-вариант) дополнительно указывает gif для image.
- **Прочие типы сообщений:**
  - стикер: `"attachment": {"type": "like_heart"}`;
  - пост: `"attachment": {"type": "MEDIA_SHARE", "payload": {"id": "<POST_ID>"}}`, только для постов, которыми владеет ваш пользователь;
  - реакция: `"sender_action": "react"` + `"payload": {"message_id": "<MID>", "reaction": "😊"}`; для снятия `"unreact"` без поля `reaction`.

### 5.5 Sender actions (`typing_on`, `typing_off`, `mark_seen`)
- Доступны для Instagram с 2025-09-23.
- **Правила:**
  - «Requests to display sender actions for typing indicators and `mark_seen` indicators should only include the `sender_action` parameter and the `recipient` object.»
  - Остальное (текст и т. д.) отправляется отдельным запросом.
  - «The recipient must be signed in for sender actions to be displayed.»
  - Интервал между `typing_on` и `typing_off` должен выглядеть естественно.
```json
{ "recipient": { "id": "1234567890123456" }, "sender_action": "typing_on" }
```
```json
{ "recipient": { "id": "1234567890123456" }, "sender_action": "mark_seen" }
```
- **Проблема документации.** Обе копии страницы Instagram Login (`/docs/` и `/documentation/`) показывают `https://graph.facebook.com/VERSION/me/messages?access_token=INSTAGRAM_ACCESS_TOKEN`. В копии `/documentation/` пример mark seen ошибочно содержит `typing_on`.
- Правильный хост для Instagram User token **не подтверждён**. Сначала пробуйте `POST https://graph.instagram.com/v25.0/<IG_ID>/messages` (тот же хост, что у Send API), при ошибке — вариант из документации.

---

## 6. 24-часовое окно и Human Agent

**24 часа**
- «Your app has 24 hours to respond to any message sent from an Instagram user to your app user.»
- Политика «Messenger Platform and IG Messaging API policy»: «Messages sent within the 24-hour window may contain promotional content.»
- **Что открывает окно.** Политика перечисляет, в формулировках Messenger: сообщение пользователя, CTA (Get Started), Click-to-Messenger ad, plugin, m.me с ref, реакция на сообщение. Для Instagram Login официально подтверждено только входящее сообщение пользователя. Остальные триггеры для IG **не подтверждены дословно**.

**Human Agent (7 дней)**
- **Feature reference:** живые агенты могут отвечать на сообщения пользователей с меткой `human_agent` **в течение 7 дней** с момента отправки. Разрешённое использование: поддержка живым агентом, когда вопрос нельзя решить в стандартном окне.
- **Политика:** «Human Agent tag that allows businesses to manually respond to user messages within a 7-day period». Только ручные ответы людей, боты под этим тегом нарушают политику.
- **Доступность в Instagram.** Обзор Instagram Platform указывает feature **Human Agent** в строке messaging для обеих конфигураций. На странице Send Messages (Instagram Login): «your app can tag the response to allow your app to send the message outside the 24 hour messaging window».
- **Одобрение нужно.** Feature reference: требуется прохождение **App Review** до доступа к live data и **Business Verification**. Значит, даже приложению одного бизнеса для этого нужны ревью и верификация.
- **JSON для `graph.instagram.com` в официальной документации не найден (не подтверждено).**
  - В официальном Postman workspace Meta есть запрос «Send a message with HUMAN_AGENT tag | Instagram API», но его содержимое прочитать не удалось.
  - Ожидаемая форма (по конвенции Messenger Send API: `messaging_type` = `RESPONSE` | `UPDATE` | `MESSAGE_TAG`, при теге нужен `MESSAGE_TAG`):
```json
{
  "recipient": { "id": "1234567890123456" },
  "messaging_type": "MESSAGE_TAG",
  "tag": "HUMAN_AGENT",
  "message": { "text": "Здравствуйте! Это Мадина из пекарни, отвечаю по вашему заказу." }
}
```

**Политика для ботов (исправлено)**
- Раскрытие автоматизации: «**When required by applicable law**, automated chat experiences must disclose that a person is interacting with an automated service: at the beginning of any conversation or message thread, after a significant lapse of time, or when a chat moves from human interaction to automated experience.»
  - В черновике это было подано как безусловное требование, но в тексте есть оговорка про применимое право.
  - Всё равно рекомендуется в первом сообщении писать, что отвечает бот. Это честно и дешево.
- «Automated bots must respond to user input within 30 seconds». Это раздел «Responsiveness policy», и там речь о ботах Messenger Platform. Применимость к Instagram явно **не указана**.

**Дизайн системы**
- Храните `last_customer_message_at` по диалогу и блокируйте автоответы после 24 часов.
- После одобрения Human Agent разрешайте ручные ответы из админки до 7 дней с тегом. Сначала проверьте JSON на тестовом аккаунте.

---

## 7. Профиль отправителя по IGSID

```
GET https://graph.instagram.com/v25.0/<INSTAGRAM_SCOPED_ID>?fields=name,username,profile_pic,follower_count,is_user_follow_business,is_business_follow_user,is_verified_user
Authorization: Bearer <INSTAGRAM_USER_ACCESS_TOKEN>
```
В официальном примере токен передаётся как `&access_token=...`. Header `Authorization: Bearer` работает на Send API. Для User Profile его работа **не подтверждена** отдельным примером, но это стандарт Graph API.

```json
{
  "name": "Peter Chang",
  "username": "peter_chang_live",
  "profile_pic": "https://fbcdn-profile-...",
  "follower_count": 1234,
  "is_user_follow_business": false,
  "is_business_follow_user": true
}
```

**Поля:**
- `name` — «can be null if name not set». Используйте `username` как запасной вариант.
- `profile_pic` — может быть null. «The URL will expire in a few days»: кешируйте картинку или периодически обновляйте URL.
- `is_verified_user` — boolean.

**Прочее:**
- Разрешения: `instagram_business_basic`, `instagram_business_manage_messages`.
- Согласие пользователя обязательно. Оно появляется, когда пользователь написал аккаунту или нажал icebreaker или persistent menu.
- Если пользователь заблокировал аккаунт бизнеса, доступ к профилю закрыт.

---

## 8. Conversations API (история, догрузка)

- **Список диалогов:**
  ```
  GET https://graph.instagram.com/v25.0/me/conversations?platform=instagram&access_token=<INSTAGRAM_ACCESS_TOKEN>
  ```
  Для одного клиента добавьте `&user_id=<IGSID>`. Сообщения диалога запрашиваются через `fields=messages`.
- **Детали сообщения:**
  ```
  GET https://graph.instagram.com/v25.0/<MESSAGE_ID>?fields=id,created_time,from,to,message
  ```
- **Ограничения:**
  - «You can only get details about the 20 most recent messages in the conversation.» Для более старых сообщений вернётся ошибка, что сообщение удалено.
  - Диалоги в папке Requests, неактивные 30 дней, не возвращаются.
- **Лимит:** 2 вызова в секунду на аккаунт.
- **Не подтверждено:** можно ли повторно получить вложения (audio/image) через этот API. Не рассчитывайте на него для восстановления медиа.

---

## 9. Rate limits (страница Graph API Rate Limiting)

| API | Лимит |
|---|---|
| Send API: текст, ссылки, реакции, стикеры | **100 вызовов/с** на аккаунт |
| Send API: audio, video | **10 вызовов/с** на аккаунт |
| Conversations API | **2 вызова/с** на аккаунт |
| Private Replies: Live comments | 100/с |
| Private Replies: комментарии к постам и Reels | 750/час |
| Прочие эндпоинты Instagram Platform (кроме messaging) | «Calls within 24 hours = 4800 * Number of Impressions» |

- Заголовок `X-Business-Use-Case-Usage`: `call_count`, `total_cputime`, `total_time`, `estimated_time_to_regain_access`.
- При троттлинге возвращается ошибка **80002**.
- Для пекарни лимиты некритичны. Всё равно ставьте ретраи с экспоненциальной задержкой при 80002.

---

## 10. Ограничения и риски

1. **Standard Access и клиенты без роли в приложении (главный риск).** Что сообщения обычных клиентов приходят и ответы доставляются при Standard Access, **не подтверждено**. Проверьте в первый день. План Б: App Review + Business Verification (Advanced Access).
2. **Приложение должно быть в Live**, иначе webhooks не приходят.
3. **Секрет для подписи** (Instagram App Secret или Meta App Secret) **не подтверждён**. Сделайте его настраиваемым.
4. **Хост sender actions** в документации указан как `graph.facebook.com` при Instagram-токене, правильный хост **не подтверждён**. В одной из копий страницы пример mark seen с ошибкой.
5. **JSON для HUMAN_AGENT на `graph.instagram.com` не подтверждён.** Сам Human Agent требует App Review и Business Verification, и использовать его можно только для ручных ответов.
6. **Названия полей webhooks расходятся** (`messaging_reactions`/`message_reactions`, `messaging_referrals`/`messaging_referral`). Сверяйтесь с дашбордом.
7. **Формат `attachments` для изображений** в примерах непоследователен (объект или массив). Тестируйте.
8. **Лимит текста 1000 байт**, для кириллицы и таджикского это около 500 символов. Режьте по байтам.
9. **Медиа:**
   - CDN-URL перестаёт работать после удаления или истечения контента, TTL не документирован: скачивайте сразу;
   - медиа из story mentions хранить нельзя;
   - условия хранения обычных вложений DM **не подтверждены**, сверьтесь с Platform Terms;
   - `profile_pic` истекает через несколько дней.
10. **Голосовые:** что они приходят как `type: "audio"`, прямо не документировано. Формат файла не описан. Нужны ffmpeg и STT с поддержкой ru/tg.
11. **Типы вложений меняются:** `share` устарел после февраля 2026, появились `ig_post`, `story`, `ig_story`. GIF и стикеры в Messenger-варианте не вызывают webhook. Обрабатывайте неизвестные типы и `is_unsupported` мягко.
12. **Нет групповых чатов:** «An Instagram professional account can only converse with one customer per conversation.»
13. **Shares:** в webhook приходит только URL на shared media или post.
14. **Эхо и self-сообщения:** пропускайте `is_echo` и `is_self`, иначе бот зациклится.
15. **Токены:** long-lived живёт 60 дней, refresh возможен только для токена старше 24 часов и ещё не истёкшего. Истёкший токен требует повторного логина владельца. Нужен ежедневный job и алерт в админке.
16. **Версия API:** документация на v25.0 (доступна до 2028-07-29), последняя v26.0. Держите версию в конфиге.
17. **Conversations API** отдаёт только 20 последних сообщений диалога. История должна храниться в своей БД с первого webhook.
18. **Качество документации:** опечатки (trailing commas, неверный `sender_action`, неверный хост) и две копии (`/docs/` и `/documentation/`). Каждый вызов проверяйте на реальном аккаунте.

### Рекомендуемый поток на бэкенде
1. Webhook POST → проверка подписи по сырым байтам → сразу 200 → задача в очередь.
2. Воркер обходит `entry[]` и `messaging[]`:
   - пропускает `is_echo` и `is_self`;
   - дедуплицирует по `mid`;
   - делает upsert клиента по IGSID (User Profile API с кешем).
3. **Вложения:** сразу скачать по CDN URL (кроме `story_mention`). `audio` → ffmpeg → STT (ru/tg). `image` → референс-фото к заказу.
4. **Ответ:**
   - `typing_on` (хост проверить);
   - текст частями до 1000 байт;
   - audio (m4a/aac до 25MB) и image (jpeg/png до 8MB) по публичному HTTPS URL;
   - в пределах 100 сообщений/с и 10 audio/video/с.
5. Контроль 24-часового окна. Human Agent (7 дней, только вручную) подключить после App Review и Business Verification.
6. Ежедневный refresh токена, версия Graph API (`v25.0`) в конфиге.

---

## 11. Источники

- Instagram Platform Overview: https://developers.facebook.com/docs/instagram-platform/overview
- Get started (Instagram Login): https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started
- Business Login for Instagram: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login
- Refresh access token (reference): https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token
- App Review (Instagram Platform): https://developers.facebook.com/docs/instagram-platform/app-review
- Access Levels (Graph API): https://developers.facebook.com/docs/graph-api/overview/access-levels
- Send Messages (Instagram Login):
  - https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api
  - https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api
- Sender Actions (Instagram Login):
  - https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/sender-actions
  - https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api/sender-actions
- User Profile API: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/user-profile
- Conversations API: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/conversations-api
- Instagram Webhooks: https://developers.facebook.com/docs/instagram-platform/webhooks
- Webhook Notification Examples:
  - https://developers.facebook.com/docs/instagram-platform/webhooks/examples
  - https://developers.facebook.com/documentation/instagram-platform/webhooks/examples
- Webhooks Reference: Instagram: https://developers.facebook.com/docs/graph-api/webhooks/reference/instagram
- Graph API Webhooks, Getting Started: https://developers.facebook.com/docs/graph-api/webhooks/getting-started
- Instagram Platform Changelog: https://developers.facebook.com/docs/instagram-platform/changelog
- Graph API Changelog (versions): https://developers.facebook.com/docs/graph-api/changelog
- Graph API v26.0: https://developers.facebook.com/docs/graph-api/changelog/version26.0
- Rate Limiting: https://developers.facebook.com/docs/graph-api/overview/rate-limiting
- Human Agent feature: https://developers.facebook.com/docs/features-reference/human-agent
- Messenger Platform and IG Messaging API policy:
  - https://developers.facebook.com/docs/messenger-platform/policy/policy-overview
  - https://developers.facebook.com/documentation/business-messaging/messenger-platform/policy
- Messenger Platform, Send a message (messaging_type): https://developers.facebook.com/documentation/business-messaging/messenger-platform/send-messages
- Instagram Messaging (Messenger-вариант):
  - Send Message: https://developers.facebook.com/docs/messenger-platform/instagram/features/send-message
  - Webhooks: https://developers.facebook.com/docs/messenger-platform/instagram/features/webhook/
  - Story Mention: https://developers.facebook.com/docs/messenger-platform/instagram/features/story-mention/
  - Attachment Upload: https://developers.facebook.com/documentation/business-messaging/instagram-messaging/features/attachment-upload
- Update on Instagram Basic Display API (блог Meta): https://developers.facebook.com/blog/post/2024/09/04/update-on-instagram-basic-display-api/
- Meta Postman, Instagram API, запрос «Send a message with HUMAN_AGENT tag» (содержимое не прочитано): https://www.postman.com/meta/instagram/request/23987686-3f06ebc8-c5ad-4b8a-be9f-81acdc79245c
