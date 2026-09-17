"""Application settings (pydantic-settings).

Every environment variable from docs/architecture/06-integrations.md §6 is declared here;
names are fixed by the contract. Values come from the process environment and from ``.env``
files: the repository root ``.env`` first, then ``backend/.env`` (the later file wins, the
process environment wins over both). When ``APP_ENV=test`` is set in the process environment,
``.env`` files are ignored so that tests are hermetic.
"""

import os
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
ENV_FILES: tuple[Path, ...] = (REPO_ROOT / ".env", BACKEND_DIR / ".env")

AppEnv = Literal["development", "production", "test"]

# Fields whose values must never appear in logs or reprs.
SECRET_FIELDS: tuple[str, ...] = (
    "APP_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "JWT_SECRET",
    "FIRST_ADMIN_PASSWORD",
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_VERIFY_TOKEN",
    "INSTAGRAM_APP_SECRET",
    "META_APP_SECRET",
    "LLM_API_KEY",
    "STT_API_KEY",
    "TTS_API_KEY",
    "MAPS_API_KEY",
    "MAXIM_API_KEY",
)

# Literal secret values shorter than this are not masked by value (too many false positives);
# key-based masking in app.core.logging still applies to them.
MIN_MASKABLE_SECRET_LENGTH = 8


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    """Parse ``"lat_min,lng_min,lat_max,lng_max"`` (south, west, north, east)."""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("CITY_BBOX must look like 'lat_min,lng_min,lat_max,lng_max'")
    lat_min, lng_min, lat_max, lng_max = (float(part) for part in parts)
    if not (-90 <= lat_min < lat_max <= 90 and -180 <= lng_min < lng_max <= 180):
        raise ValueError("CITY_BBOX has invalid bounds")
    return lat_min, lng_min, lat_max, lng_max


def parse_point(value: str) -> tuple[float, float]:
    """Parse ``"lat,lng"``."""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise ValueError("Point must look like 'lat,lng'")
    lat, lng = (float(part) for part in parts)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Point coordinates are out of range")
    return lat, lng


def parse_hhmm(value: str) -> time:
    """Parse ``"HH:MM"`` (24h)."""
    try:
        hours, minutes = value.strip().split(":")
        if len(hours) != 2 or len(minutes) != 2:
            raise ValueError
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise ValueError(f"Expected time in HH:MM format, got {value!r}") from exc


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    APP_ENV: AppEnv = "development"
    APP_SECRET_KEY: str = Field(default="", repr=False)
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    FRONTEND_PUBLIC_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000"
    BUSINESS_TIMEZONE: str = "Asia/Dushanbe"
    LOG_LEVEL: str = "INFO"
    MEDIA_ROOT: str = "/app/media"

    # --- DB / Redis ---
    DATABASE_URL: str = Field(default="postgresql+psycopg://bakery:bakery@postgres:5432/bakery", repr=False)
    REDIS_URL: str = Field(default="redis://redis:6379/0", repr=False)
    POSTGRES_USER: str = "bakery"
    POSTGRES_PASSWORD: str = Field(default="", repr=False)
    POSTGRES_DB: str = "bakery"

    # --- Auth ---
    JWT_SECRET: str = Field(default="", repr=False)
    JWT_ACCESS_TTL_MINUTES: int = 30
    JWT_REFRESH_TTL_DAYS: int = 14
    FIRST_ADMIN_USERNAME: str = "admin"
    FIRST_ADMIN_PASSWORD: str = Field(default="", repr=False)

    # --- Instagram ---
    INSTAGRAM_ACCESS_TOKEN: str = Field(default="", repr=False)
    INSTAGRAM_VERIFY_TOKEN: str = Field(default="", repr=False)
    INSTAGRAM_APP_SECRET: str = Field(default="", repr=False)
    META_APP_SECRET: str = Field(default="", repr=False)
    INSTAGRAM_ACCOUNT_ID: str = ""
    INSTAGRAM_GRAPH_URL: str = "https://graph.instagram.com"
    INSTAGRAM_API_VERSION: str = "v25.0"

    # --- LLM ---
    LLM_PROVIDER: Literal["anthropic", "gemini"] = "anthropic"
    LLM_API_KEY: str = Field(default="", repr=False)
    LLM_MODEL: str = "claude-opus-5"
    LLM_EFFORT: str = "medium"
    LLM_TIMEOUT_SECONDS: float = 60
    LLM_FALLBACKS_ENABLED: bool = True
    # 05 §2: request economy (free tiers). Off → reply texts come from templates / the understanding
    # step runs without read tools (catalog and FAQ are in the prompt anyway): 1 request per message.
    LLM_REPLY_WORDING_ENABLED: bool = True
    LLM_TOOLS_ENABLED: bool = True

    # --- Speech ---
    STT_PROVIDER: str = "elevenlabs"
    STT_API_KEY: str = Field(default="", repr=False)
    TTS_PROVIDER: str = "openai"
    TTS_API_KEY: str = Field(default="", repr=False)
    TTS_VOICE: str = "alloy"

    # --- Maps / routing ---
    GEOCODER_PROVIDER: Literal["nominatim", "google"] = "nominatim"
    MAPS_API_KEY: str = Field(default="", repr=False)
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org"
    # The public Nominatim answers 403 to stock and placeholder agents (anything with "example.com"):
    # the default is accepted, but the integration status asks for a real contact (06 §2).
    NOMINATIM_USER_AGENT: str = "bakery-bot/1.0 (+contact not configured; set NOMINATIM_USER_AGENT)"
    OSRM_URL: str = ""
    CITY_NAME: str = "Худжанд"
    CITY_BBOX: str = "40.2623896,69.5612523,40.3316825,69.6740681"
    # Not listed in 06 §6 explicitly; the centre is given in 06 §2 ("центр 40.2842,69.6191").
    CITY_CENTER: str = "40.2842191,69.6191174"

    # --- Maxim ---
    MAXIM_MODE: Literal["manual", "api"] = "manual"
    MAXIM_API_KEY: str = Field(default="", repr=False)
    MAXIM_API_URL: str = ""

    # --- Frontend (shared .env; unused by the backend itself) ---
    NEXT_PUBLIC_APP_NAME: str = "Домашняя выпечка"
    BACKEND_INTERNAL_URL: str = "http://backend:8000"

    # --- Background jobs (06 §5: beat runs generate_daily_report at DAILY_REPORT_TIME) ---
    DAILY_REPORT_TIME: str = "21:00"

    # ------------------------------------------------------------------ validators

    @model_validator(mode="before")
    @classmethod
    def _ignore_empty_non_string_values(cls, data: Any) -> Any:
        """``JWT_ACCESS_TTL_MINUTES=`` in a copied .env.example means "use the default"."""
        if isinstance(data, dict):
            for name, field in cls.model_fields.items():
                if field.annotation in (int, float, bool) and isinstance(data.get(name), str):
                    if not data[name].strip():
                        data.pop(name)
        return data

    @field_validator(
        "APP_ENV", "LLM_PROVIDER", "GEOCODER_PROVIDER", "MAXIM_MODE", "STT_PROVIDER", "TTS_PROVIDER", mode="before"
    )
    @classmethod
    def _lowercase(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _uppercase(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("BUSINESS_TIMEZONE")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone: {value!r}") from exc
        return value

    @field_validator("CITY_BBOX")
    @classmethod
    def _validate_bbox(cls, value: str) -> str:
        parse_bbox(value)
        return value

    @field_validator("CITY_CENTER")
    @classmethod
    def _validate_center(cls, value: str) -> str:
        parse_point(value)
        return value

    @field_validator("DAILY_REPORT_TIME")
    @classmethod
    def _validate_report_time(cls, value: str) -> str:
        parse_hhmm(value)
        return value

    # ------------------------------------------------------------------ helpers

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def is_test(self) -> bool:
        return self.APP_ENV == "test"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def city_bbox(self) -> tuple[float, float, float, float]:
        """``(lat_min, lng_min, lat_max, lng_max)``."""
        return parse_bbox(self.CITY_BBOX)

    @property
    def city_center(self) -> tuple[float, float]:
        """``(lat, lng)``."""
        return parse_point(self.CITY_CENTER)

    @property
    def daily_report_time(self) -> time:
        return parse_hhmm(self.DAILY_REPORT_TIME)

    @property
    def media_root(self) -> Path:
        return Path(self.MEDIA_ROOT)

    @property
    def instagram_app_secrets(self) -> list[str]:
        """Secrets to try for ``X-Hub-Signature-256`` verification (06 §1)."""
        return [secret for secret in (self.INSTAGRAM_APP_SECRET, self.META_APP_SECRET) if secret]

    @property
    def celery_task_always_eager(self) -> bool:
        """Tests, and local development without Redis: there is no broker/worker, so tasks run inline
        (otherwise they would be queued into the in-memory broker and silently lost)."""
        return self.is_test or (self.is_development and not self.REDIS_URL.strip())

    def secret_values(self) -> list[str]:
        """Configured secret values (for log redaction). Never log the result."""
        values = [getattr(self, name) for name in SECRET_FIELDS]
        for url in (self.DATABASE_URL, self.REDIS_URL):
            try:
                password = urlsplit(url).password
            except ValueError:
                password = None
            if password:
                values.append(password)
        return sorted(
            {value for value in values if value and len(value) >= MIN_MASKABLE_SECRET_LENGTH},
            key=len,
            reverse=True,
        )


@lru_cache
def get_settings() -> Settings:
    if os.environ.get("APP_ENV", "").strip().lower() == "test":
        return Settings(_env_file=None)
    return Settings()
