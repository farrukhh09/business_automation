# Голосовые сообщения (RU + TG) для Instagram-бота домашней кондитерской: STT и TTS, сентябрь 2026

Дата проверки: 2026-09-15. Всё, что ниже не помечено **«не подтверждено»**, сверено с официальной страницей, URL которой указан рядом (или в разделе «Источники»). Вторичные источники помечены явно.

---

## 1. Вывод и рекомендация

| Решение | Рекомендация |
|---|---|
| **STT по умолчанию** | **ElevenLabs Scribe v2** (`POST https://api.elevenlabs.io/v1/speech-to-text`, `multipart/form-data`, заголовок `xi-api-key`). Единственный крупный хостинговый вендор, который публикует уровень точности для таджикского: **Tajik (tgk) — "Good (>10% to ≤20% WER)"**; русский — "Excellent (≤ 5% WER)". Принимает AAC/M4A (аудио) и MP4 (видео-контейнер). Цена $0.22 за час аудио (~$0.0037/мин). Таджикистан **не** входит в список стран с ограниченным доступом (ограничены Belarus, Cuba, Iran, North Korea, Russia, Syria, Crimea, Donetsk, Luhansk). |
| **STT fallback / A/B-кандидат** | **Gemini 3.5 Transcribe** (`gemini-3.5-transcribe`, Gemini API, `POST https://generativelanguage.googleapis.com/v1beta/interactions`). В таблице языков есть `tg-TJ` и `ru-RU`. **Статус — Public Preview** (анонс Google от 26.08.2026), поэтому не ставить основным в прод. Цена: $0.003/мин аудио на входе + $0.002/мин текста на выходе, итого около $0.005/мин. Таджикистан есть в списке доступных регионов Gemini API. |
| **Третий вариант** | Google Cloud Speech-to-Text v2, модель `chirp_2`, регионы `europe-west4` или `asia-southeast1` (`tg-TJ` указан). Риски: на странице Chirp 2 все регионы помечены **"Private GA"** (может понадобиться доступ по заявке); синхронный `recognize` ограничен 10 MB / 1 минутой аудио. |
| **Не использовать для таджикского STT** | OpenAI (Tajik не упомянут в документации), Azure AI Speech (нет `tg-TJ`), Yandex SpeechKit (нет таджикского), AssemblyAI (Tajik только в Universal-2, уровень **"Fair accuracy (>50% WER)"**). Для русского все четыре подходят. |
| **TTS на таджикском** | **Ни один хостинговый вендор не документирует таджикский голос**: OpenAI, Google Cloud TTS (список голосов, Chirp 3 HD, Gemini-TTS), Gemini API TTS, Azure, Yandex, ElevenLabs. **Таджикоязычным клиентам по умолчанию отвечать текстом.** Если голос обязателен, остаются только self-hosted модели: `re-skill/orpheus-tj-early` (тег apache-2.0, ранний чекпойнт на 35 часах аудио, база Llama 3.2 3B, нужна юридическая проверка) или `facebook/mms-tts-tgk` (**CC-BY-NC 4.0**, коммерческое использование запрещено). |
| **TTS на русском** | **OpenAI `gpt-4o-mini-tts`** с `response_format: "wav"` (или `"aac"`). WAV и AAC есть в списке аудиоформатов Instagram (aac, m4a, wav, mp4; до 25MB). Альтернатива: ElevenLabs с `output_format=wav_44100`. Надёжнее всего получить WAV и перекодировать в `.m4a` (AAC) через ffmpeg. |
| **Подсказка языка или автоопределение** | **Первый проход с автоопределением, затем проверка результата.** Второй проход с явной таджикской подсказкой (`language_code=tgk` у ElevenLabs, `language_codes: ["tg-TJ"]` у Gemini) запускать, если определён не ru/tg язык (например, fa/uz), если в тексте латиница или арабица вместо кириллицы, если `language_probability` низкая или если клиент ранее писал по-таджикски. Не форсировать `ru`: в Душанбе русская и таджикская речь часто смешиваются. |

Порядок затрат при 30 голосовых в день по ~30 с (около 450 мин/мес): ElevenLabs около $1.65, Gemini около $2.25, OpenAI `gpt-transcribe` около $2.03, Google STT v2 около $7.20 (по цене из вторичного источника). Выбирать провайдера нужно по качеству на реальных записях клиентов, а не по цене (план теста — в разделе 7).

---

## 2. Instagram: входящее и исходящее аудио

### 2.1 Входящее голосовое сообщение
- Webhook Instagram Messaging перечисляет типы вложений: "audio, file, image (image or sticker), share, story_mention, video, ig_reel or reel". У каждого вложения есть `payload.url`. В примере JSON в документации показаны `image` и `video`; для `audio` структура та же по аналогии (отдельного примера с audio нет):
  ```json
  "attachments": [ { "type": "audio", "payload": { "url": "LINK" } } ]
  ```
  Источники: https://developers.facebook.com/docs/messenger-platform/instagram/features/webhook, https://developers.facebook.com/documentation/business-messaging/instagram-messaging/webhooks
- **Формат ссылки** описан у стороннего CPaaS CM.com, не у Meta: `https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1234&signature=AAAA`. CM.com пишет, что CDN URL "will not return the media when the content is deleted or expired". 24 часа указано только для медиа из stories (https://developers.cm.com/messaging/docs/instagram-messaging-inbound). Требование CM.com хранить только ссылку, а не файл, — это правило самого CM.com, а не документированное правило Meta.
- **Контейнер и кодек голосового в документации Meta не описаны.** Вторичные источники (блоги MiniTool и др.) пишут, что голосовые Instagram скачиваются как audio-only MP4. Вероятно, это AAC в MP4, но это **не подтверждено**. У URL нет расширения файла.
  - Практика: сразу при получении webhook скачать байты, сохранить у себя, залогировать `Content-Type` и прогнать `ffprobe`. В STT отправлять исходные байты: ElevenLabs, Gemini (как `audio/m4a`), Google STT v2 (`MP4_AAC`/`M4A_AAC`) и OpenAI (`mp4`/`m4a`) принимают MP4/M4A. Перекодирование нужно только для Yandex (LPCM/OggOpus/MP3).
- **Длительность:** по новостям (Social Media Today и др., май 2025) лимит голосового в DM увеличен с 1 до 5 минут. **В документации для разработчиков не подтверждено.**
- Нужен ли access token для скачивания lookaside-URL, **не подтверждено** (в документации не описано). Сначала пробовать обычный GET.

### 2.2 Исходящее аудио (голосовой ответ)
Источник: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/

| Media type | Format | Max size |
|---|---|---|
| Audio | aac, m4a, wav, mp4 | 25MB |
| Video | mp4, ogg, avi, mov, webm | 25MB |
| Image | png, jpeg | 8MB |
| File | pdf | 25MB |

- Endpoint: `POST https://graph.instagram.com/v25.0/<IG_ID>/messages` (в примерах документации v25.0; перед внедрением сверить актуальную версию Graph API). Токен — Instagram User access token; разрешения `instagram_business_basic`, `instagram_business_manage_messages`.
- Вложение передаётся через `payload.url` (публичный HTTPS URL) или `payload.attachment_id`.
- **Attachment Upload API** задокументирован как `POST https://graph.facebook.com/<LATEST-API-VERSION>/<PAGE_ID>/message_attachments` с **Page access token** и `"platform":"instagram"`; поддерживает `audio` (в документе опечатка "acc, m4a, wav, mp4", до 25MB) и возвращает `attachment_id` (https://developers.facebook.com/docs/messenger-platform/instagram/features/attachment-upload/). **Эквивалентный endpoint загрузки для варианта «Instagram API with Instagram Login» (`graph.instagram.com`, без привязанной Facebook-страницы) не подтверждён.** Самый простой путь — отдавать файл по публичному HTTPS URL.
- Окно ответа: "Your app has 24 hours to respond to any message sent from an Instagram user to your app user."
- **Форматов, которых нет в списке Instagram:** MP3, Opus/OGG, raw PCM. Такой вывод TTS нужно перекодировать (например, ffmpeg в `.m4a` AAC) или сразу запрашивать `wav`/`aac`.

---

## 3. Матрица поддержки (таджикский = `tg` / `tgk` / `tg-TJ`, кириллица)

| Провайдер / модель | STT TG | STT RU | TTS TG | TTS RU | Подтверждение |
|---|---|---|---|---|---|
| OpenAI `gpt-transcribe`, `gpt-4o(-mini)-transcribe`, `whisper-1` | **Не документирован** (в токенизаторе open-source Whisper есть `"tg": "tajik"`) | Да | **Нет** (Tajik нет в списке языков TTS) | Да | STT guide, TTS guide, tokenizer.py |
| Gemini 3.5 Transcribe (Gemini API, **Public Preview**) | **Да, `tg-TJ`** | Да, `ru-RU` | — | — | model page, transcribe guide, Google blog |
| Gemini API TTS | — | — | **Нет** | Да (`ru`) | speech-generation |
| Google Cloud STT v2 | **Да: `chirp` и `chirp_2` в `asia-southeast1` и `europe-west4`**; нет в `chirp_3` | Да | — | — | supported languages, Chirp 3 page |
| Google Cloud TTS (voices, Chirp 3 HD, Gemini-TTS) | — | — | **Нет** | Да | chirp3-hd, gemini-tts (список голосов при загрузке обрезался, но Chirp 3 HD и Gemini-TTS проверены полностью) |
| Azure AI Speech | **Нет** (`tg-TJ` отсутствует; страница обновлена 2026-09-10) | Да | **Нет** | Да | language-support |
| Yandex SpeechKit | **Нет** | Да | **Нет** | Да | stt/models, tts/voices |
| ElevenLabs Scribe v2 / TTS | **Да, "Good (>10% to ≤20% WER)", `tgk`** | Да, "Excellent (≤ 5% WER)" | **Нет** (v3, v3 conversational, Multilingual v2, Flash v2.5) | Да | STT capability, models |
| AssemblyAI | Да, но только Universal-2, **"Fair accuracy (>50% WER)"**; нет в Universal-3.5 Pro | Да ("High accuracy (≤ 10% WER)") | — | — | supported-languages |
| Gladia (`solaria-1`) | Да (`tg`, auto-discovery/code-switch = Yes); точность не публикуется | Да | — | — | supported-languages |
| Meta MMS `mms-1b-all` / `mms-tts-tgk` | Да (адаптер `tgk`) | Да | Да (VITS) | — | HF cards, **CC-BY-NC-4.0** |
| Meta Omnilingual ASR | Да (`tgk_Cyrl`) | Да (`rus_Cyrl`) | — | — | GitHub, **Apache 2.0** |
| `re-skill/orpheus-tj-early` | — | — | Да (ранний чекпойнт) | — | HF card, тег apache-2.0 |

---

## 4. Детали по провайдерам

### 4.1 ElevenLabs

**STT (Scribe v2)**
- Возможности: https://elevenlabs.io/docs/overview/capabilities/speech-to-text. Модели: Scribe v2, Scribe v2 Medical, Scribe v2 Realtime (90+ языков). `model_id`: `scribe_v2`, `scribe_v2_medical`, `scribe_v2_realtime` (https://elevenlabs.io/docs/overview/models).
- Уровни точности: Russian (rus) — "Excellent (≤ 5% WER)"; Tajik (tgk) — "Good (>10% to ≤20% WER)".
- Форматы: аудио "AAC, AIFF, OGG, MP3, OPUS, WAV, FLAC, M4A, WebM"; видео "MP4, AVI, MKV, MOV, WMV, FLV, WebM, MPEG, 3GPP".
- Лимиты: на странице возможностей указано 3 GB и до 10 часов, в API reference — файл "under 5.0GB" и минимум 100 ms. Для голосовых это неважно.
- API (https://elevenlabs.io/docs/api-reference/speech-to-text/convert): `POST https://api.elevenlabs.io/v1/speech-to-text`, заголовок `xi-api-key`, `multipart/form-data`.
  - Обязательный параметр: `model_id`. Источник аудио (ровно один): `file`, `source_url` или устаревший `cloud_storage_url`.
  - Параметры: `language_code` (ISO-639-1 или ISO-639-3), `tag_audio_events`, `num_speakers`, `diarize`, `timestamps_granularity` (`word`/`character`), `file_format` (`pcm_s16le_16` или `other`), `webhook`, `temperature`, `seed`, `use_multi_channel`, `keyterms` (до 1000 терминов, каждый короче 50 символов), `entity_detection`.
  - Ответ: `text`, `words`, `language_code`, `language_probability`, `entities`. (`audio_duration_secs` из черновика в этой проверке не подтверждён.)
- Цены (https://elevenlabs.io/pricing/api): Scribe v2 $0.22/час; Scribe v2 Realtime $0.39/час; надбавки: keyterm prompting $0.050/час, entity detection $0.070/час.
- Доступность по странам: ограничены "Belarus, Cuba, Iran, North Korea, Russia, Syria, Crimea, and the Donetsk and Luhansk oblasts of Ukraine" (https://elevenlabs.io/docs/help-center/legal/do-you-restrict-access-to-the-service-and-platform-for-any-specific-countries). Таджикистана в списке нет. **Если бэкенд или прокси стоит на российском IP, запросы будут заблокированы.**
- Сможет ли `source_url` скачать подписанный lookaside-URL Meta, **не подтверждено**. Надёжнее скачать файл самим и передать через `file`.

**TTS**
- Модели (https://elevenlabs.io/docs/overview/models): `eleven_v3` и `eleven_v3_conversational` (70+ языков), `eleven_multilingual_v2` (29), `eleven_flash_v2_5` (32). Русский есть везде, **таджикского нет нигде**.
- API (https://elevenlabs.io/docs/api-reference/text-to-speech/convert): `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=...`, JSON-тело (`text`, `model_id` — по умолчанию `eleven_multilingual_v2`, `language_code`, `voice_settings`).
- `output_format`: `mp3_22050_32` … `mp3_44100_192`, `opus_48000_32…192`, `pcm_8000…pcm_48000`, `wav_8000…wav_48000`, `ulaw_8000`, `alaw_8000`. **`wav_*` Instagram принимает; AAC/M4A среди вариантов нет.**
- Цена за 1K символов: Flash/Turbo $0.05, Multilingual v2 $0.10, v3 $0.10.

### 4.2 Google Gemini API — Gemini 3.5 Transcribe
- Страница модели: https://ai.google.dev/gemini-api/docs/models/gemini-3.5-transcribe. Код `gemini-3.5-transcribe`, "Latest update: August 2026". Лимит: "Up to 1 hour per request (up to 30 minutes when speaker diarization or word-level timestamps are enabled)". Также есть `gemini-3.5-transcribe-live` для стриминга.
- **Статус:** в анонсе Google — "In public preview in the Gemini API via Google AI Studio" (https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5-transcribe/, 26.08.2026).
- Языки: 85+, включая **Tajik `tg-TJ`** и Russian `ru-RU` (страница модели и https://ai.google.dev/gemini-api/docs/transcribe).
- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/interactions`, заголовок `x-goog-api-key`, JSON. Текст результата — в `interaction.output_text`.
- Поля `generation_config.transcription_config`:
  - `language_codes` — "BCP-47 language codes (e.g., `["en-US"]`). If omitted or empty (`[]`), the model automatically detects the language and handles code-switching." Массив, но пример с несколькими кодами (например, `["ru-RU","tg-TJ"]`) в документации не показан — поведение **не подтверждено**.
  - `custom_vocabulary` — до 1000 терминов; "Incompatible with speaker diarization and word-level timestamps".
  - `mode` — `"smart"` или объект verbatim `{"type": "verbatim", ...}`; "Defaults to verbatim transcription". Режим smart делает "Disfluency removal" и автоформатирование в абзацы и списки. **Для заказов безопаснее verbatim по умолчанию**: smart может выбросить самопоправки клиента.
  - `mode.timestamp_granularities` (`["word"]`) и `mode.diarization_mode` (`"speaker"`) — **только внутри verbatim-объекта `mode`**, а не на верхнем уровне `transcription_config`.
- Аудио: в примере transcribe guide используется URI из Files API. Inline base64 (`{"type":"audio","data":"<base64>","mime_type":"..."}`, "Maximum request size is 20 MB total") показан на странице Interactions audio (https://ai.google.dev/gemini-api/docs/interactions/audio) для моделей Gemini в целом. **Работает ли inline именно с `gemini-3.5-transcribe`, не подтверждено.**
- MIME-типы (https://ai.google.dev/gemini-api/docs/audio): `audio/wav`, `audio/mp3`, `audio/aiff`, `audio/aac`, `audio/ogg`, `audio/flac`, `audio/mpeg`, `audio/m4a`, `audio/l16`, `audio/opus`, `audio/alaw`, `audio/mulaw`, `audio/webm`. **`audio/mp4` в списке нет**, поэтому MP4-аудио из Instagram передавать как `audio/m4a` (что модель это примет, **не подтверждено**, хотя контейнер тот же).
- Files API (https://ai.google.dev/gemini-api/docs/files): "Files are stored for 48 hours"; до 20 GB на проект, до 2 GB на файл. Загрузка resumable на `${BASE_URL}/upload/v1beta/files`, заголовки `X-Goog-Upload-Protocol: resumable`, `X-Goog-Upload-Command: start`, `X-Goog-Upload-Header-Content-Length`, затем `X-Goog-Upload-Offset: 0` и `X-Goog-Upload-Command: upload, finalize`. URI берётся из `.file.uri` в ответе.
- Цены (https://ai.google.dev/gemini-api/docs/pricing): вход "$2.00 or $0.003/min* (audio)", выход "$12.00 or $0.002/min* (text)", "effective blended rate of ~$0.005 per min". Есть бесплатный уровень.
- Доступность: Таджикистан есть на https://ai.google.dev/gemini-api/docs/available-regions.

### 4.3 OpenAI

**STT**
- Guide: https://developers.openai.com/api/docs/guides/speech-to-text. Рекомендуемая модель — `gpt-transcribe` (выпущена 28.07.2026 вместе с `gpt-live-transcribe`; дату дают вторичные источники и форум OpenAI). Также доступны `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `gpt-4o-transcribe-diarize` и `whisper-1`.
- Endpoint: `POST https://api.openai.com/v1/audio/transcriptions`, `Authorization: Bearer $OPENAI_API_KEY`, `multipart/form-data`.
- Форматы: "Files can be up to 25 MB. Supported input formats are `mp3`, `mp4`, `mpeg`, `mpga`, `m4a`, `wav`, and `webm`." Отдельного `aac` нет; AAC в MP4 покрывается `mp4`/`m4a`.
- Подсказки: у `gpt-transcribe` — `prompt`, `keywords`, `languages` (ISO 639-1, отдельные ISO 639-3, региональные `zh`). У старых моделей — один `language`; оба поля сразу не отправлять. В ответе `gpt-transcribe` возвращает `"languages": [{ "code": "..." }]`.
- **Таджикский:** в guide и на странице модели (https://developers.openai.com/api/docs/models/gpt-transcribe) списка языков нет, Tajik не упомянут. В open-source токенизаторе Whisper есть `"tg": "tajik"` (https://github.com/openai/whisper/blob/main/whisper/tokenizer.py), то есть выдать таджикский модель может, но OpenAI заявлений о качестве не делает.
- В API reference (https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create) `gpt-transcribe`, `languages` и `keywords` пока не перечислены (есть `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `gpt-4o-transcribe-diarize`, `whisper-1` и параметр `language`). Как кодировать массивы в сыром multipart, **не подтверждено**; используйте SDK с `extra_body`.
- Цены (https://developers.openai.com/api/docs/pricing): `gpt-transcribe` $0.0045/мин; `gpt-4o-transcribe` около $0.006/мин (оценка); `gpt-4o-mini-transcribe` около $0.003/мин (оценка); Whisper $0.006/мин.
- Таджикистан есть в https://developers.openai.com/api/docs/supported-countries.

**TTS**
- Guide: https://developers.openai.com/api/docs/guides/text-to-speech. Reference: https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create
- `POST https://api.openai.com/v1/audio/speech`, JSON. Модели: `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`, `gpt-4o-mini-tts-2025-12-15`.
- `input` до 4096 символов; `instructions` не поддерживается у tts-1/tts-1-hd; `speed` 0.25–4.0; `stream_format` `sse`/`audio`.
- Голоса (13): alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse, marin, cedar ("For best quality, we recommend using `marin` or `cedar`").
- `response_format`: `mp3`, `opus`, `aac`, `flac`, `wav`, `pcm` (PCM — 24kHz 16-bit без заголовка). Является ли `aac` потоком ADTS или MP4-контейнером, **не подтверждено**; безопаснее `wav` → ffmpeg → `.m4a`.
- Языки: "generally follows the Whisper model"; в списке есть Russian, Kazakh, Persian, **нет Tajik и Uzbek**.
- Цены: tts-1 $15/1M символов; tts-1-hd $30/1M символов; gpt-4o-mini-tts $0.60/1M токенов текста на входе и $12/1M аудио-токенов на выходе.

### 4.4 Google Cloud Speech-to-Text v2 (Chirp)
- Языки (https://docs.cloud.google.com/speech-to-text/v2/docs/speech-to-text-supported-languages): в `asia-southeast1` и `europe-west4` — "Tajik (Tajikistan) | tg-TJ | `chirp` | Automatic punctuation" и "… | `chirp_2` | Automatic punctuation, Model adaptation, Word-level confidence, Profanity filter". В `chirp_3` таджикского нет (https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3; регионы `us`/`eu`).
- Chirp 2 (https://docs.cloud.google.com/speech-to-text/docs/models/chirp-2): регионы `us-central1`, `europe-west4`, `asia-southeast1` — все **"Private GA"**. "Language support however differs depending on the method used. Specifically `BatchRecognize` offers the most extensive language support." Работа таджикского в `Recognize` (не только Batch) **не подтверждена** — нужен тестовый вызов. Автоопределение: `language_codes=["auto"]`.
- REST: `POST https://speech.googleapis.com/v2/projects/{project}/locations/{location}/recognizers/{recognizer}:recognize`; тело `config`, `configMask`, `content` (base64, до 10 MB) или `uri`. `_` — неявный recognizer. В образце Chirp 2 используется `us-central1-speech.googleapis.com`; хост `europe-west4-speech.googleapis.com` выведен по аналогии — в REST reference указан только `https://speech.googleapis.com`, так что **региональный хост не подтверждён** официальной страницей.
- `AutoDetectDecodingConfig`: "WAV_LINEAR16, WAV_MULAW, WAV_ALAW, RFC4867_5_AMR, RFC4867_5_AMRWB, FLAC, MP3, OGG_OPUS, WEBM_OPUS, MP4_AAC, M4A_AAC, MOV_AAC" (https://docs.cloud.google.com/speech-to-text/v2/docs/reference/rest/v2/projects.locations.recognizers).
- Лимиты (https://docs.cloud.google.com/speech-to-text/docs/quotas): синхронно "10 MB or 1 minute of audio duration (whichever is reached first)"; Batch — до 8 часов на файл; стрим — 25 KB на запрос, до 5 минут.
- Авторизация: OAuth 2.0 bearer token (сервисный аккаунт GCP) — стандартная практика GCP, отдельную страницу не цитирую.
- Цена: официальная страница https://cloud.google.com/speech-to-text/pricing не прочиталась (контент обрезан). По вторичным данным (блог Google 2023, поиск): $0.016/мин, при объёмах до $0.004/мин, Dynamic Batch на 75% дешевле. **Не подтверждено для 2026.**

### 4.5 Google Cloud Text-to-Speech и Gemini API TTS
- Chirp 3 HD (https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd): Russian `ru-RU` есть, Tajik нет.
- Gemini-TTS (https://docs.cloud.google.com/text-to-speech/docs/gemini-tts): модели `gemini-3.1-flash-tts-preview`, `gemini-2.5-flash-tts`, `gemini-2.5-flash-lite-preview-tts`, `gemini-2.5-pro-tts`. Russian — GA; в Preview есть `fa-IR`, но нет Tajik. Кодировки unary: "LINEAR16 (default), ALAW, MULAW, MP3, OGG_OPUS, PCM" — **AAC/M4A нет**, для Instagram нужен LINEAR16 (WAV) или перекодирование.
- Полный список голосов (https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types) при загрузке обрезался; в загруженной части Tajik не найден. Итоговый вывод «таджикского голоса нет» опирается на Chirp 3 HD и Gemini-TTS; для стандартных голосов это **частично не подтверждено**.
- Gemini API TTS (https://ai.google.dev/gemini-api/docs/speech-generation): Russian (`ru`) есть, Tajik нет; выход — PCM 24000 Hz, 16-bit, mono.

### 4.6 Azure AI Speech
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=stt (ms.date 2026-09-09, обновлено 2026-09-10): `tg-TJ` нет ни в STT, ни в TTS.
- Для сравнения есть `uz-UZ` (`uz-UZ-MadinaNeural`), `kk-KZ` (`kk-KZ-AigulNeural`), `fa-IR` (`fa-IR-DilaraNeural`), `ru-RU` (включая `ru-RU-SvetlanaNeural`, `ru-RU-DmitryNeural`, HD и Multilingual).
- Вывод: только русский.

### 4.7 Yandex SpeechKit
- STT (https://aistudio.yandex.ru/docs/en/speechkit/stt/models): `auto`, de-DE, en-US, es-ES, fi-FI, fr-FR, he-IL, it-IT, kk-KZ, nl-NL, pl-PL, pt-PT, pt-BR, ru-RU, sv-SE, tr-TR, uz-UZ. **Таджикского нет.** Про `auto`: "If a sentence contains words in different languages, the language may be detected incorrectly".
- TTS (https://aistudio.yandex.ru/docs/en/speechkit/tts/voices): de, en, he, kk, ru, uz. **Таджикского нет.**
- Форматы (https://aistudio.yandex.ru/docs/en/speechkit/formats): LPCM, OggOpus, MP3; "The MP3 format is not supported in the API v1 for synchronous recognition and the API v2 for streaming recognition". MP4/AAC из Instagram придётся перекодировать.

### 4.8 AssemblyAI (добавлено: в черновике не было)
- https://www.assemblyai.com/docs/pre-recorded-audio/supported-languages: Tajik (`tg`) есть только у Universal-2, в уровне "Fair accuracy (>50% WER)"; в Universal-3.5 Pro таджикского нет. Russian — "High accuracy (≤ 10% WER)".
- Вывод: для таджикского не рекомендуется. Цены и endpoint в этой проверке **не подтверждены** (страница цен не прочиталась).

### 4.9 Gladia
- Языки (https://docs.gladia.io/chapters/language/supported-languages): "| Tajik | tg | Yes | No | Yes | Yes |" (Solaria-1 / Solaria-3 / Auto-discovery and code-switch / Translation). Solaria-3 — только en/fr/de/es/it.
- Схема (https://docs.gladia.io/chapters/pre-recorded-stt/getting-started): `POST https://api.gladia.io/v2/upload` → `POST https://api.gladia.io/v2/pre-recorded` (`audio_url`, `language_config.languages`) → опрос `GET https://api.gladia.io/v2/pre-recorded/:id` или webhook/callback. Заголовок `x-gladia-key`. Только асинхронно.
- Форматы (https://docs.gladia.io/chapters/limits-and-specifications/supported-formats): аудио "aac, ac3, eac3, flac, m4a, mp2, mp3, ogg, opus, wav", видео включает mp4; до 1000 MB; 135 минут (Standard), 4h15 (Enterprise).
- Цены (https://www.gladia.io/pricing): Starter async $0.61/час; Growth — "as low as $0.20/hour".
- Опубликованной точности для таджикского нет; брать в тест, только если первые два варианта разочаруют.

### 4.10 Открытые модели Meta (self-hosted)
- **MMS-1b-all** (https://huggingface.co/facebook/mms-1b-all): адаптеры `tgk` (варианты скрипта arabic, cyrillic, latin); `processor.tokenizer.set_target_lang(...)` + `model.load_adapter(...)`; вход 16 kHz; **лицензия cc-by-nc-4.0 — не для коммерческого бота**.
- **MMS-TTS-tgk** (https://huggingface.co/facebook/mms-tts-tgk): VITS; `sampling_rate: 16000` (config.json); `is_uroman: false` (в tokenizer_config.json, кириллица читается напрямую); **CC-BY-NC 4.0**.
- **Omnilingual ASR** (https://github.com/facebookresearch/omnilingual-asr): **Apache 2.0**; `tgk_Cyrl` и `rus_Cyrl` есть в `lang_ids.py`; семейства W2V, CTC, LLM (300M–7B). "Currently only audio files shorter than 40 seconds are accepted for inference on CTC and LLM model suites", но есть варианты unlimited-length. VRAM от ~2 GiB (300M) до ~17–20 GiB (7B). Лучший коммерчески допустимый открытый STT, но для небольшого бизнеса трудоёмок.

### 4.11 Таджикские и community-ресурсы
- **`re-skill/orpheus-tj-early`** (https://huggingface.co/re-skill/orpheus-tj-early; блог https://re-skill.io/blog/tajik-llm-tts-release, 27.06.2025): Orpheus-3B, дообучена от `canopylabs/orpheus-3b-0.1-pretrained` на базе `meta-llama/Llama-3.2-3B-Instruct`, тег лицензии Apache 2.0, "trained only 35 hours of audio data", кодек SNAC, демо Space `re-skill/tajik-tts`, есть MLX-версия. Из-за базы Llama 3.2 могут применяться условия Llama Community License — нужна юридическая проверка.
- `muhtasham/whisper-tg` (https://huggingface.co/muhtasham/whisper-tg): fine-tune `openai/whisper-small`, apache-2.0, eval WER 18.9518 (датасет "CUSTOM").
- `Tohirju/tajik-asr-runs-2026-09` (https://huggingface.co/datasets/Tohirju/tajik-asr-runs-2026-09): записи экспериментов без чекпойнтов. Whisper-large-v3-turbo (1,100 h) — WER 10.24, CER 3.90; Qwen3-ASR-1.7B — WER 18.79. Замечание про отсутствие букв ғ ӣ қ ӯ ҳ ҷ относится **к словарю Parakeet** (из-за этого вывод шёл латиницей). На каком именно тестовом наборе (FLEURS-tg или другом) получены цифры, **не подтверждено**. Годится как ориентир качества, но не как сервис.
- Коммерческого хостингового API, специализированного на таджикской речи и с публичной документацией, найдено не было.

---

## 5. Подсказка языка или автоопределение (логика)
1. **Первый проход без подсказки.** ElevenLabs: не передавать `language_code`. Gemini: `language_codes: []` или без поля (документация: автоопределение "and handles code-switching"). OpenAI `gpt-transcribe`: без `languages`.
2. **Проверка результата.** Второй проход с таджикской подсказкой (`language_code=tgk` / `language_codes: ["tg-TJ"]`) запускать, если:
   - определён язык не из {ru/rus, tg/tgk} (типичные ошибки: `fa`/`fas`, `uz`), **или**
   - в тексте арабица или латиница вместо кириллицы, **или**
   - `language_probability` (ElevenLabs) ниже порога, например 0.6.

   Из двух вариантов оставить тот, у которого выше уверенность или есть таджикские буквы ғ ӣ қ ӯ ҳ ҷ.
3. **Учитывать историю переписки:** если клиент раньше писал по-таджикски, подсказку давать сразу.
4. **Не форсировать `ru`:** при смешанной речи слова второго языка теряются. Смешанную речь RU/TG ни один вендор не бенчмаркал.
5. **Словарь продукции** («Наполеон», «медовик», начинки, районы Душанбе): `keyterms` у ElevenLabs (платно), `keywords`/`prompt` у OpenAI `gpt-transcribe`, `custom_vocabulary` у Gemini (несовместим с диаризацией и таймстемпами). Диаризация для голосовых не нужна.

---

## 6. Пайплайн (FastAPI) и HTTP-примеры

### 6.1 Пайплайн
1. Webhook получает `attachments[].type == "audio"`, сразу ставит задачу в очередь и отвечает 200.
2. Воркер немедленно скачивает `payload.url`, сохраняет байты (S3/R2), пишет `Content-Type`, результат `ffprobe` и SHA-256 для идемпотентности.
3. STT: ElevenLabs Scribe v2 (multipart, исходные байты). При ошибке, таймауте или низкой уверенности — Gemini 3.5 Transcribe.
4. Проверка языка (раздел 5). Транскрипт, язык и уверенность сохраняются в заказ и показываются оператору в админке с возможностью правки.
5. Транскрипт передаётся на шаг извлечения заказа (LLM).
6. Ответ: русскоязычному клиенту — текст или OpenAI TTS (`wav` → `.m4a`), по публичному HTTPS URL, в пределах 24 часов. Таджикоязычному — текст.
7. Аудио — персональные данные: хранить минимально необходимое время. Условия использования данных у вендоров в этой проверке **не проверялись**.

### 6.2 Входящий webhook (фрагмент)
```json
{
  "message": {
    "mid": "<MID>",
    "attachments": [
      { "type": "audio", "payload": { "url": "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=...&signature=..." } }
    ]
  }
}
```
Структура `attachments[].payload.url` взята из документации Meta (там пример на image/video); форма URL — из документации CM.com. Внешняя обёртка (`object`, `entry[]`, `messaging[]`, `sender`, `recipient`) сокращена.

### 6.3 ElevenLabs Scribe v2 (STT по умолчанию)
```bash
curl -X POST https://api.elevenlabs.io/v1/speech-to-text \
  -H "xi-api-key: $ELEVENLABS_API_KEY" \
  -F "model_id=scribe_v2" \
  -F "file=@voice.m4a" \
  -F "tag_audio_events=false" \
  -F "diarize=false"
# второй проход с таджикской подсказкой:
#   -F "language_code=tgk"
```
```python
import httpx

async def transcribe_elevenlabs(audio_bytes: bytes, lang: str | None = None) -> dict:
    data = {"model_id": "scribe_v2", "tag_audio_events": "false", "diarize": "false"}
    if lang:
        data["language_code"] = lang  # "tgk" или "rus"
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            data=data,
            files={"file": ("voice.m4a", audio_bytes, "audio/mp4")},
        )
        r.raise_for_status()
        j = r.json()
        return {"text": j["text"], "lang": j["language_code"], "p": j["language_probability"]}
```

### 6.4 Gemini 3.5 Transcribe (fallback, Public Preview)
Документированная форма (URI из Files API):
```bash
curl -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.5-transcribe",
    "input": [ { "type": "audio", "uri": "YOUR_FILE_URI", "mime_type": "audio/m4a" } ],
    "generation_config": { "transcription_config": { "language_codes": ["tg-TJ"] } }
  }'
```
Автоопределение: `"language_codes": []`. Смарт-режим без филлеров: `"mode": "smart"` (для заказов лучше оставить verbatim по умолчанию).

Inline-вариант (поле `data` задокументировано для Interactions audio в целом, лимит 20 MB; **для `gemini-3.5-transcribe` не подтверждено**):
```json
{
  "model": "gemini-3.5-transcribe",
  "input": [ { "type": "audio", "data": "<BASE64_OF_VOICE_M4A>", "mime_type": "audio/m4a" } ]
}
```
Загрузка в Files API (resumable):
```bash
# 1) старт
curl "https://generativelanguage.googleapis.com/upload/v1beta/files" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -D headers.tmp \
  -H "X-Goog-Upload-Protocol: resumable" \
  -H "X-Goog-Upload-Command: start" \
  -H "X-Goog-Upload-Header-Content-Length: $NUM_BYTES" \
  -H "X-Goog-Upload-Header-Content-Type: audio/m4a" \
  -H "Content-Type: application/json" \
  -d '{"file": {"display_name": "voice"}}'
# 2) из headers.tmp взять x-goog-upload-url, затем:
curl "$UPLOAD_URL" \
  -H "Content-Length: $NUM_BYTES" \
  -H "X-Goog-Upload-Offset: 0" \
  -H "X-Goog-Upload-Command: upload, finalize" \
  --data-binary "@voice.m4a"
# 3) в ответе: .file.uri
```
Имена заголовков и путь `/upload/v1beta/files` взяты из документации; тело start-запроса (`display_name`) воспроизведено по образцу документации и перед использованием должно быть сверено со страницей.

### 6.5 OpenAI gpt-transcribe (русский или эксперимент)
```bash
curl --request POST \
  --url https://api.openai.com/v1/audio/transcriptions \
  --header "Authorization: Bearer $OPENAI_API_KEY" \
  --header 'Content-Type: multipart/form-data' \
  --form file=@voice.m4a \
  --form model=gpt-transcribe
```
```python
tr = client.audio.transcriptions.create(
    model="gpt-transcribe",
    file=open("voice.m4a", "rb"),
    prompt="Заказ в домашней кондитерской в Душанбе: торты, пирожные, доставка.",
    extra_body={"keywords": ["Наполеон", "медовик"], "languages": ["ru", "tg"]},
)
```
Передавать `"tg"` в `languages` синтаксически допустимо (ISO 639-1), но заявлений о качестве для таджикского у OpenAI нет.

### 6.6 Google Cloud STT v2, chirp_2, таджикский (≤ 60 s / 10 MB)
```http
POST https://europe-west4-speech.googleapis.com/v2/projects/PROJECT_ID/locations/europe-west4/recognizers/_:recognize
Authorization: Bearer <OAUTH2_ACCESS_TOKEN>
Content-Type: application/json

{
  "config": {
    "autoDecodingConfig": {},
    "model": "chirp_2",
    "languageCodes": ["tg-TJ"],
    "features": { "enableAutomaticPunctuation": true }
  },
  "content": "<BASE64_OF_VOICE_M4A>"
}
```
Региональный хост выведен по образцу `us-central1-speech.googleapis.com` (**не подтверждён**). Chirp 2 — "Private GA". Для аудио длиннее 60 s нужен `:batchRecognize` с `gs://` URI.

### 6.7 OpenAI TTS (русский голосовой ответ)
```bash
curl https://api.openai.com/v1/audio/speech \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini-tts",
    "voice": "marin",
    "input": "Здравствуйте! Ваш торт «Наполеон» будет готов завтра к 15:00.",
    "instructions": "Говори тепло и дружелюбно, умеренный темп.",
    "response_format": "wav"
  }' --output reply.wav
ffmpeg -i reply.wav -c:a aac -b:a 64k -movflags +faststart reply.m4a
```

### 6.8 ElevenLabs TTS (русский, WAV)
```bash
curl -X POST "https://api.elevenlabs.io/v1/text-to-speech/$VOICE_ID?output_format=wav_44100" \
  -H "xi-api-key: $ELEVENLABS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Здравствуйте! Ваш заказ принят.", "model_id": "eleven_multilingual_v2"}' \
  --output reply.wav
```

### 6.9 Instagram Send API: аудио-ответ
```http
POST https://graph.instagram.com/v25.0/<IG_ID>/messages
Authorization: Bearer <INSTAGRAM_USER_ACCESS_TOKEN>
Content-Type: application/json

{
  "recipient": { "id": "<IGSID>" },
  "message": {
    "attachment": {
      "type": "audio",
      "payload": { "url": "https://files.example.tj/replies/7f3a.m4a" }
    }
  }
}
```
Правила: только aac/m4a/wav/mp4, до 25MB, в пределах 24 часов. Вместо `url` можно передать `"attachment_id": "<ID>"`, но endpoint загрузки для потока Instagram Login не подтверждён (см. 2.2).

---

## 7. План проверки (1 день)
1. Собрать 40–60 реальных голосовых: 20 русских, 20 таджикских, 10–20 смешанных. Включить шум кухни и улицы, числа, цены, даты, адреса.
2. Носитель языка делает эталонные расшифровки.
3. Прогнать: ElevenLabs Scribe v2 (auto и `tgk`), Gemini 3.5 Transcribe (auto и `tg-TJ`, verbatim), Google `chirp_2` (`tg-TJ`, если доступ к Private GA есть), OpenAI `gpt-transcribe` (`["ru","tg"]`).
4. Метрики:
   - WER и CER;
   - доля вывода в кириллице;
   - сохранение ғ ӣ қ ӯ ҳ ҷ;
   - точность чисел, дат и телефонов;
   - точность определения языка.
5. По результатам утвердить основной и резервный сервисы.

---

## 8. Ограничения и риски
1. **Кодек и контейнер голосовых Instagram Meta не документирует.** «Audio-only MP4» — из неофициальных блогов. Проверять каждый файл через `ffprobe`.
2. **Время жизни media URL** для обычных вложений DM не документировано. Скачивать сразу.
3. **Лимит 5 минут** на голосовое — из новостей, не из документации разработчика.
4. **Gemini 3.5 Transcribe — Public Preview**: возможны изменения API и цены. Не подтверждены inline `data` для этой модели, приём `audio/m4a` для MP4 и поведение `language_codes` с несколькими кодами.
5. **Google STT v2:** `chirp_2` в регионах с таджикским — "Private GA"; таджикский только в `chirp`/`chirp_2`; работа таджикского в синхронном `Recognize` не проверена; региональный хост и цена 2026 года не подтверждены.
6. **OpenAI:** качество таджикского не документировано. `gpt-transcribe`, `languages` и `keywords` пока нет в API reference. Формат `aac` на выходе (ADTS или MP4) не подтверждён.
7. **Таджикский TTS:** хостинговых вариантов нет. MMS-TTS некоммерческий; `orpheus-tj-early` — ранний чекпойнт на базе Llama 3.2, лицензию нужно проверить юридически.
8. **ElevenLabs:** доступ блокируется с российских IP (Russia в списке ограничений) — важно при выборе хостинга и прокси. Не подтверждено, скачает ли `source_url` подписанный lookaside-URL.
9. **Attachment Upload API** задокументирован для Page token (`graph.facebook.com`); для Instagram Login не подтверждён. Использовать публичный URL.
10. **AssemblyAI** для таджикского — ">50% WER"; Gladia не публикует точность для таджикского.
11. **Встроенная расшифровка голосовых в Instagram** (по новостям) не имеет поля в Messaging API. На неё не полагаться.
12. Условия обработки персональных данных у вендоров в этой проверке не изучались.

---

## 9. Источники
**Instagram / Meta**
- https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/messaging-api
- https://developers.facebook.com/docs/messenger-platform/instagram/features/webhook
- https://developers.facebook.com/documentation/business-messaging/instagram-messaging/webhooks
- https://developers.facebook.com/docs/messenger-platform/instagram/features/attachment-upload/
- https://developers.cm.com/messaging/docs/instagram-messaging-inbound (сторонний)
- https://www.socialmediatoday.com/news/instagram-adds-voice-message-dm-transcription-longer-clips/748937/ (вторичный)
- https://moviemaker.minitool.com/news/how-to-download-instagram-voice-messages.html (вторичный)

**ElevenLabs**
- https://elevenlabs.io/docs/overview/capabilities/speech-to-text
- https://elevenlabs.io/docs/api-reference/speech-to-text/convert
- https://elevenlabs.io/docs/overview/models
- https://elevenlabs.io/docs/api-reference/text-to-speech/convert
- https://elevenlabs.io/pricing/api
- https://elevenlabs.io/docs/help-center/legal/do-you-restrict-access-to-the-service-and-platform-for-any-specific-countries

**Google Gemini API**
- https://ai.google.dev/gemini-api/docs/models/gemini-3.5-transcribe
- https://ai.google.dev/gemini-api/docs/transcribe
- https://ai.google.dev/gemini-api/docs/interactions/audio
- https://ai.google.dev/gemini-api/docs/audio
- https://ai.google.dev/gemini-api/docs/files
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/available-regions
- https://ai.google.dev/gemini-api/docs/speech-generation
- https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5-transcribe/

**OpenAI**
- https://developers.openai.com/api/docs/guides/speech-to-text
- https://developers.openai.com/api/docs/models/gpt-transcribe
- https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create
- https://developers.openai.com/api/docs/guides/text-to-speech
- https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create
- https://developers.openai.com/api/docs/pricing
- https://developers.openai.com/api/docs/supported-countries
- https://github.com/openai/whisper/blob/main/whisper/tokenizer.py
- https://community.openai.com/t/gpt-live-transcribe-and-gpt-transcribe-two-new-transcription-models-in-the-api/1388318 (дата релиза)

**Google Cloud**
- https://docs.cloud.google.com/speech-to-text/v2/docs/speech-to-text-supported-languages
- https://docs.cloud.google.com/speech-to-text/docs/models/chirp-2
- https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3
- https://docs.cloud.google.com/speech-to-text/v2/docs/reference/rest/v2/projects.locations.recognizers
- https://docs.cloud.google.com/speech-to-text/v2/docs/reference/rest
- https://docs.cloud.google.com/speech-to-text/docs/quotas
- https://cloud.google.com/speech-to-text/pricing (не прочиталась)
- https://cloud.google.com/blog/products/ai-machine-learning/google-cloud-speech-to-text-v2-api (вторичный, цены 2023)
- https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types
- https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd
- https://docs.cloud.google.com/text-to-speech/docs/gemini-tts

**Azure / Yandex / AssemblyAI / Gladia**
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=stt
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
- https://aistudio.yandex.ru/docs/en/speechkit/stt/models
- https://aistudio.yandex.ru/docs/en/speechkit/tts/voices
- https://aistudio.yandex.ru/docs/en/speechkit/formats
- https://www.assemblyai.com/docs/pre-recorded-audio/supported-languages
- https://docs.gladia.io/chapters/language/supported-languages
- https://docs.gladia.io/chapters/pre-recorded-stt/getting-started
- https://docs.gladia.io/chapters/limits-and-specifications/supported-formats
- https://www.gladia.io/pricing

**Открытые модели и таджикские ресурсы**
- https://huggingface.co/facebook/mms-1b-all
- https://huggingface.co/facebook/mms-tts-tgk (config.json, tokenizer_config.json)
- https://github.com/facebookresearch/omnilingual-asr
- https://huggingface.co/re-skill/orpheus-tj-early
- https://re-skill.io/blog/tajik-llm-tts-release
- https://huggingface.co/muhtasham/whisper-tg
- https://huggingface.co/datasets/Tohirju/tajik-asr-runs-2026-09
