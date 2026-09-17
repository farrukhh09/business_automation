"""Integration status for the admin panel (04-api.md §12 ``GET /settings/integrations``).

Every status is derived **only from configuration presence** (``app.core.config.Settings``): no
external service is contacted and no secret value is ever returned. ``details`` may name an
environment variable, a provider, a model or a voice — never a key, token or secret.

Sources of the rules: 06-integrations.md §1 (Instagram), §2 (maps/routing), §3 (speech), §4 (Maxim)
and 05-ai.md §2 (LLM). The provider names repeat the ones the adapters use (``anthropic``,
``elevenlabs``, ``openai``, ``nominatim``/``google``, ``osrm``/``haversine``, ``manual``); they are
duplicated here on purpose so that the settings endpoint does not import the adapter packages.
"""

from collections.abc import Sequence

from app.core.config import Settings, get_settings
from app.schemas.settings import IntegrationsOut, IntegrationStatus

# --------------------------------------------------------------------------- constants

INSTAGRAM_PROVIDER = "instagram"
LLM_PROVIDER = "anthropic"
ROUTING_OSRM = "osrm"
ROUTING_HAVERSINE = "haversine"
GEOCODER_NOMINATIM = "nominatim"
GEOCODER_GOOGLE = "google"
MAXIM_MANUAL = "manual"
MAXIM_API = "api"

# 06 §1: the webhook needs the verify token and at least one app secret for the signature check.
INSTAGRAM_REQUIRED: tuple[str, ...] = (
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_ACCOUNT_ID",
    "INSTAGRAM_VERIFY_TOKEN",
)
INSTAGRAM_SECRET_REQUIREMENT = "INSTAGRAM_APP_SECRET или META_APP_SECRET"

# A provider name that means "turned off" (mirrors app/integrations/speech/factory.py).
DISABLED_PROVIDERS = frozenset({"", "none", "disabled", "off"})

EXAMPLE_CONTACT = "example.com"
PLACEHOLDER_CONTACT = "contact not configured"

MAXIM_NO_API = "Официальный API Maxim отсутствует"
MAXIM_MANUAL_DETAILS = f"{MAXIM_NO_API} — ручной режим"
MAXIM_API_DETAILS = f"{MAXIM_NO_API} — режим api не реализован, переключите MAXIM_MODE=manual"

ROUTING_ESTIMATE_DETAILS = (
    "OSRM не задан: расстояния и время рассчитываются по прямой (haversine × 1.3) "
    "и средней скорости — это приблизительная оценка, а не маршрут по дорогам"
)

TTS_TAJIK_NOTE = "таджикский синтез речи недоступен, таджикоязычным клиентам отвечаем текстом"


def _text(value: object) -> str:
    return str(value or "").strip()


def _missing_details(missing: Sequence[str]) -> str:
    label = "Не задана переменная" if len(missing) == 1 else "Не заданы переменные"
    return f"{label}: {', '.join(missing)}"


# --------------------------------------------------------------------------- per-integration


def instagram_status(settings: Settings) -> IntegrationStatus:
    """Configured when token, account id, verify token and one app secret are present (06 §1)."""
    missing = [name for name in INSTAGRAM_REQUIRED if not _text(getattr(settings, name, ""))]
    if not settings.instagram_app_secrets:
        missing.append(INSTAGRAM_SECRET_REQUIREMENT)
    if missing:
        return IntegrationStatus(configured=False, provider=INSTAGRAM_PROVIDER, details=_missing_details(missing))
    return IntegrationStatus(
        configured=True,
        provider=INSTAGRAM_PROVIDER,
        details="Instagram API with Instagram Login: токен, аккаунт, verify-токен и подпись webhook заданы",
    )


def llm_status(settings: Settings) -> IntegrationStatus:
    """Configured when ``LLM_API_KEY`` is present; ``details`` always names the model (05 §2)."""
    model = _text(settings.LLM_MODEL) or "не задана"
    provider = _text(settings.LLM_PROVIDER) or LLM_PROVIDER
    if not _text(settings.LLM_API_KEY):
        return IntegrationStatus(
            configured=False,
            provider=provider,
            details=f"{_missing_details(['LLM_API_KEY'])}. Модель: {model}",
        )
    return IntegrationStatus(configured=True, provider=provider, details=f"Модель: {model}")


def stt_status(settings: Settings) -> IntegrationStatus:
    """Configured when ``STT_PROVIDER`` is set (and not disabled) and ``STT_API_KEY`` is present."""
    provider = _text(settings.STT_PROVIDER).lower()
    if provider in DISABLED_PROVIDERS:
        return IntegrationStatus(
            configured=False,
            provider=None,
            details="Распознавание голосовых сообщений выключено: не задан STT_PROVIDER",
        )
    if not _text(settings.STT_API_KEY):
        return IntegrationStatus(configured=False, provider=provider, details=_missing_details(["STT_API_KEY"]))
    return IntegrationStatus(
        configured=True,
        provider=provider,
        details=f"Распознавание голосовых сообщений: провайдер {provider}",
    )


def tts_status(settings: Settings) -> IntegrationStatus:
    """Configured when ``TTS_PROVIDER`` is set (and not disabled) and ``TTS_API_KEY`` is present."""
    provider = _text(settings.TTS_PROVIDER).lower()
    if provider in DISABLED_PROVIDERS:
        return IntegrationStatus(
            configured=False,
            provider=None,
            details="Голосовые ответы выключены: не задан TTS_PROVIDER",
        )
    if not _text(settings.TTS_API_KEY):
        return IntegrationStatus(
            configured=False,
            provider=provider,
            details=f"{_missing_details(['TTS_API_KEY'])}. {TTS_TAJIK_NOTE.capitalize()}",
        )
    voice = _text(settings.TTS_VOICE) or "по умолчанию"
    return IntegrationStatus(
        configured=True,
        provider=provider,
        details=f"Голосовые ответы: провайдер {provider}, голос {voice}; {TTS_TAJIK_NOTE}",
    )


def geocoder_status(settings: Settings) -> IntegrationStatus:
    """Nominatim needs URL + User-Agent; Google needs ``MAPS_API_KEY`` (06 §2)."""
    provider = _text(settings.GEOCODER_PROVIDER).lower()
    if provider == GEOCODER_GOOGLE:
        if not _text(settings.MAPS_API_KEY):
            return IntegrationStatus(configured=False, provider=provider, details=_missing_details(["MAPS_API_KEY"]))
        return IntegrationStatus(
            configured=True,
            provider=provider,
            details=(
                "Google Geocoding API; по лицензии координаты кэшируются не дольше 30 дней "
                "и показываются только на карте Google"
            ),
        )

    missing = [name for name in ("NOMINATIM_URL", "NOMINATIM_USER_AGENT") if not _text(getattr(settings, name, ""))]
    if missing:
        return IntegrationStatus(configured=False, provider=provider, details=_missing_details(missing))
    details = "Nominatim (OpenStreetMap), не более 1 запроса в секунду"
    user_agent = _text(settings.NOMINATIM_USER_AGENT).lower()
    if EXAMPLE_CONTACT in user_agent:
        details += (
            f" — в NOMINATIM_USER_AGENT остался пример с {EXAMPLE_CONTACT}: публичный сервер отвечает "
            "403 на такие запросы, укажите реальный контакт"
        )
    elif PLACEHOLDER_CONTACT in user_agent:
        details += " — в NOMINATIM_USER_AGENT не указан контакт, укажите e-mail или сайт"
    return IntegrationStatus(configured=True, provider=provider, details=details)


def routing_status(settings: Settings) -> IntegrationStatus:
    """Always available: OSRM when ``OSRM_URL`` is set, otherwise the haversine estimate (06 §2)."""
    if _text(settings.OSRM_URL):
        return IntegrationStatus(
            configured=True,
            provider=ROUTING_OSRM,
            details="Матрица расстояний и времени OSRM; при ошибке используется оценка haversine",
        )
    return IntegrationStatus(configured=True, provider=ROUTING_HAVERSINE, details=ROUTING_ESTIMATE_DETAILS)


def maxim_status(settings: Settings) -> IntegrationStatus:
    """06 §4: there is no official Maxim API — only the manual mode is implemented."""
    if _text(settings.MAXIM_MODE).lower() == MAXIM_API:
        return IntegrationStatus(configured=False, provider=MAXIM_API, details=MAXIM_API_DETAILS)
    return IntegrationStatus(configured=True, provider=MAXIM_MANUAL, details=MAXIM_MANUAL_DETAILS)


# --------------------------------------------------------------------------- public API


def build_integration_status(settings: Settings | None = None) -> IntegrationsOut:
    """``{instagram, llm, stt, tts, geocoder, routing, maxim}`` — no network calls, no secrets."""
    settings = settings or get_settings()
    return IntegrationsOut(
        instagram=instagram_status(settings),
        llm=llm_status(settings),
        stt=stt_status(settings),
        tts=tts_status(settings),
        geocoder=geocoder_status(settings),
        routing=routing_status(settings),
        maxim=maxim_status(settings),
    )
