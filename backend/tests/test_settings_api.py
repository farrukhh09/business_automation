"""Settings API and the integration status (04-api.md §12, 06-integrations.md).

The integration status must be computed from configuration presence only: no external call and no
secret value in the answer. ``Settings`` reaches the endpoint through ``Depends(get_settings)``, so
the tests inject a hermetic ``Settings`` object (``_env_file=None``: ``.env`` files are ignored).
"""

import json
from datetime import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import AppSetting
from app.schemas.settings import BusinessSettings
from app.services.integration_status import (
    MAXIM_MANUAL_DETAILS,
    MAXIM_NO_API,
    build_integration_status,
    geocoder_status,
    instagram_status,
    llm_status,
    maxim_status,
    routing_status,
    stt_status,
    tts_status,
)
from app.services.settings_service import BUSINESS_SETTINGS_KEY, SettingsService

URL = "/api/settings"
INTEGRATIONS_URL = f"{URL}/integrations"

INTEGRATION_KEYS = ("instagram", "llm", "stt", "tts", "geocoder", "routing", "maxim")

# Defaults of .env.example with every secret empty — the baseline of the unit tests below.
BLANK_ENV: dict[str, Any] = {
    "INSTAGRAM_ACCESS_TOKEN": "",
    "INSTAGRAM_ACCOUNT_ID": "",
    "INSTAGRAM_VERIFY_TOKEN": "",
    "INSTAGRAM_APP_SECRET": "",
    "META_APP_SECRET": "",
    "LLM_API_KEY": "",
    "LLM_MODEL": "claude-opus-5",
    "STT_PROVIDER": "elevenlabs",
    "STT_API_KEY": "",
    "TTS_PROVIDER": "openai",
    "TTS_API_KEY": "",
    "TTS_VOICE": "alloy",
    "GEOCODER_PROVIDER": "nominatim",
    "MAPS_API_KEY": "",
    "NOMINATIM_URL": "https://nominatim.openstreetmap.org",
    "NOMINATIM_USER_AGENT": "bakery-bot/1.0 (contact@example.com)",
    "OSRM_URL": "",
    "MAXIM_MODE": "manual",
    "MAXIM_API_KEY": "",
}

INSTAGRAM_READY: dict[str, Any] = {
    "INSTAGRAM_ACCESS_TOKEN": "ig-access-token-value",
    "INSTAGRAM_ACCOUNT_ID": "17841400000000000",
    "INSTAGRAM_VERIFY_TOKEN": "ig-verify-token-value",
    "INSTAGRAM_APP_SECRET": "ig-app-secret-value",
}

SECRET_ENV: dict[str, Any] = {
    "INSTAGRAM_ACCESS_TOKEN": "ig-access-token-QQQQ",
    "INSTAGRAM_ACCOUNT_ID": "17841400000000000",
    "INSTAGRAM_VERIFY_TOKEN": "ig-verify-token-WWWW",
    "INSTAGRAM_APP_SECRET": "ig-app-secret-EEEE",
    "META_APP_SECRET": "meta-app-secret-RRRR",
    "LLM_API_KEY": "llm-api-key-TTTT",
    "STT_API_KEY": "stt-api-key-YYYY",
    "TTS_API_KEY": "tts-api-key-UUUU",
    "MAPS_API_KEY": "maps-api-key-IIII",
    "MAXIM_API_KEY": "maxim-api-key-OOOO",
}


def make_settings(**overrides: Any) -> Settings:
    """Hermetic ``Settings``: ``.env`` files ignored, integrations blank unless overridden."""
    return Settings(_env_file=None, **{**BLANK_ENV, **overrides})


def use_settings(app: FastAPI, settings: Settings) -> None:
    app.dependency_overrides[get_settings] = lambda: settings


# --------------------------------------------------------------------------- GET /settings


def test_get_settings_requires_authentication(client: TestClient) -> None:
    response = client.get(URL)

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_operator_can_read_settings(client: TestClient, operator_headers: dict[str, str]) -> None:
    response = client.get(URL, headers=operator_headers)

    assert response.status_code == 200
    data = response.json()
    assert set(data) == set(BusinessSettings.model_fields)
    assert data["business_name"] == "Домашняя выпечка"
    assert data["ai_enabled"] is True
    assert data["voice_replies_enabled"] is False
    assert data["warehouse"] == {"name": "Склад", "address": "", "latitude": None, "longitude": None}
    assert data["min_lead_time_hours"] == 24
    assert data["max_days_ahead"] == 60
    assert data["route_start_time"] == "09:00"
    assert data["daily_report_time"] == "21:00"


def test_get_settings_does_not_write_to_the_database(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    assert client.get(URL, headers=admin_headers).status_code == 200

    # SettingsService.get() only reads: nothing is stored until PUT.
    db.expire_all()
    assert db.get(AppSetting, BUSINESS_SETTINGS_KEY) is None


# --------------------------------------------------------------------------- PUT /settings


def test_put_settings_requires_admin(client: TestClient, operator_headers: dict[str, str]) -> None:
    response = client.put(URL, json={"business_name": "Пекарня"}, headers=operator_headers)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_put_settings_requires_authentication(client: TestClient) -> None:
    response = client.put(URL, json={"business_name": "Пекарня"})

    assert response.status_code == 401


def test_put_settings_applies_partial_update(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    response = client.put(
        URL,
        json={
            "business_name": "Пекарня Ширин",
            "ai_enabled": False,
            "min_lead_time_hours": 12,
            "warehouse": {"name": "Кухня", "latitude": 38.56, "longitude": 68.77},
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["business_name"] == "Пекарня Ширин"
    assert data["ai_enabled"] is False
    assert data["min_lead_time_hours"] == 12
    assert data["warehouse"] == {"name": "Кухня", "address": "", "latitude": 38.56, "longitude": 68.77}
    # Untouched fields keep their defaults.
    assert data["max_days_ahead"] == 60
    assert data["daily_report_time"] == "21:00"

    again = client.get(URL, headers=admin_headers)
    assert again.json() == data

    db.expire_all()
    assert SettingsService(db).get().business_name == "Пекарня Ширин"
    assert SettingsService(db).get().route_start_time == time(9, 0)


def test_put_settings_with_empty_body_keeps_current_values(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    before = client.get(URL, headers=admin_headers).json()

    response = client.put(URL, json={}, headers=admin_headers)

    assert response.status_code == 200
    assert response.json() == before


def test_put_settings_rejects_out_of_range_value(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.put(URL, json={"max_days_ahead": 0}, headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_put_settings_rejects_half_a_warehouse_point(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.put(URL, json={"warehouse": {"latitude": 38.56}}, headers=admin_headers)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["fields"] == ["warehouse"]


# --------------------------------------------------------------------------- GET /settings/integrations


def test_integrations_requires_admin(
    client: TestClient, operator_headers: dict[str, str]
) -> None:
    response = client.get(INTEGRATIONS_URL, headers=operator_headers)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_integrations_requires_authentication(client: TestClient) -> None:
    response = client.get(INTEGRATIONS_URL)

    assert response.status_code == 401


def test_integrations_returns_every_contract_key(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.get(INTEGRATIONS_URL, headers=admin_headers)

    assert response.status_code == 200
    data = response.json()
    assert set(data) == set(INTEGRATION_KEYS)
    for key in INTEGRATION_KEYS:
        assert set(data[key]) == {"configured", "provider", "details"}
        assert isinstance(data[key]["configured"], bool)
        assert isinstance(data[key]["details"], str) and data[key]["details"]


def test_integrations_reflect_configuration(
    app: FastAPI, client: TestClient, admin_headers: dict[str, str]
) -> None:
    use_settings(
        app,
        make_settings(
            **INSTAGRAM_READY,
            LLM_API_KEY="llm-key",
            LLM_MODEL="claude-opus-5",
            STT_API_KEY="stt-key",
            TTS_API_KEY="tts-key",
            OSRM_URL="http://osrm:5000",
        ),
    )

    data = client.get(INTEGRATIONS_URL, headers=admin_headers).json()

    assert data["instagram"] == {
        "configured": True,
        "provider": "instagram",
        "details": data["instagram"]["details"],
    }
    assert data["llm"]["configured"] is True
    assert data["llm"]["provider"] == "anthropic"
    assert "claude-opus-5" in data["llm"]["details"]
    assert data["stt"]["configured"] is True
    assert data["tts"]["configured"] is True
    assert data["routing"] == {
        "configured": True,
        "provider": "osrm",
        "details": data["routing"]["details"],
    }
    assert data["maxim"] == {"configured": True, "provider": "manual", "details": MAXIM_MANUAL_DETAILS}


def test_integrations_report_missing_configuration(
    app: FastAPI, client: TestClient, admin_headers: dict[str, str]
) -> None:
    use_settings(app, make_settings())

    data = client.get(INTEGRATIONS_URL, headers=admin_headers).json()

    assert data["instagram"]["configured"] is False
    assert data["llm"]["configured"] is False
    assert data["stt"]["configured"] is False
    assert data["tts"]["configured"] is False
    assert data["geocoder"]["configured"] is True  # Nominatim needs no key
    assert data["routing"] == {
        "configured": True,
        "provider": "haversine",
        "details": data["routing"]["details"],
    }
    assert data["maxim"]["configured"] is True


# --------------------------------------------------------------------------- instagram


def test_instagram_lists_every_missing_variable() -> None:
    status = instagram_status(make_settings())

    assert status.configured is False
    assert status.provider == "instagram"
    for name in ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID", "INSTAGRAM_VERIFY_TOKEN"):
        assert name in status.details
    assert "INSTAGRAM_APP_SECRET или META_APP_SECRET" in status.details


def test_instagram_needs_an_app_secret() -> None:
    without_secret = dict(INSTAGRAM_READY)
    without_secret.pop("INSTAGRAM_APP_SECRET")

    status = instagram_status(make_settings(**without_secret))

    assert status.configured is False
    assert "INSTAGRAM_APP_SECRET или META_APP_SECRET" in status.details
    assert "INSTAGRAM_ACCESS_TOKEN" not in status.details


def test_instagram_accepts_meta_app_secret() -> None:
    env = dict(INSTAGRAM_READY)
    env.pop("INSTAGRAM_APP_SECRET")

    status = instagram_status(make_settings(**env, META_APP_SECRET="meta-secret"))

    assert status.configured is True
    assert status.provider == "instagram"


def test_instagram_configured_with_all_variables() -> None:
    status = instagram_status(make_settings(**INSTAGRAM_READY))

    assert status.configured is True


# --------------------------------------------------------------------------- llm / speech


def test_llm_status_names_the_model() -> None:
    missing = llm_status(make_settings(LLM_MODEL="claude-test-model"))
    configured = llm_status(make_settings(LLM_API_KEY="key", LLM_MODEL="claude-test-model"))

    assert missing.configured is False
    assert missing.provider == "anthropic"
    assert "LLM_API_KEY" in missing.details
    assert "claude-test-model" in missing.details
    assert configured.configured is True
    assert configured.provider == "anthropic"
    assert "claude-test-model" in configured.details


def test_stt_status_needs_provider_and_key() -> None:
    missing_key = stt_status(make_settings())
    disabled = stt_status(make_settings(STT_PROVIDER="none"))
    configured = stt_status(make_settings(STT_API_KEY="key"))

    assert (missing_key.configured, missing_key.provider) == (False, "elevenlabs")
    assert "STT_API_KEY" in missing_key.details
    assert (disabled.configured, disabled.provider) == (False, None)
    assert "STT_PROVIDER" in disabled.details
    assert (configured.configured, configured.provider) == (True, "elevenlabs")


def test_tts_status_needs_provider_and_key() -> None:
    missing_key = tts_status(make_settings())
    disabled = tts_status(make_settings(TTS_PROVIDER=""))
    configured = tts_status(make_settings(TTS_API_KEY="key", TTS_VOICE="alloy"))

    assert (missing_key.configured, missing_key.provider) == (False, "openai")
    assert "TTS_API_KEY" in missing_key.details
    assert (disabled.configured, disabled.provider) == (False, None)
    assert (configured.configured, configured.provider) == (True, "openai")
    assert "alloy" in configured.details
    # 06 §3: Tajik speech synthesis does not exist — the admin must know.
    assert "таджик" in configured.details.lower()


# --------------------------------------------------------------------------- geocoder / routing / maxim


def test_nominatim_warns_about_the_example_contact() -> None:
    status = geocoder_status(make_settings())

    assert status.configured is True
    assert status.provider == "nominatim"
    assert "example.com" in status.details


def test_nominatim_with_real_contact_has_no_warning() -> None:
    status = geocoder_status(make_settings(NOMINATIM_USER_AGENT="bakery-bot/1.0 (info@shirin.tj)"))

    assert status.configured is True
    assert "example.com" not in status.details


@pytest.mark.parametrize("missing", ["NOMINATIM_URL", "NOMINATIM_USER_AGENT"])
def test_nominatim_without_url_or_user_agent_is_not_configured(missing: str) -> None:
    status = geocoder_status(make_settings(**{missing: ""}))

    assert status.configured is False
    assert missing in status.details


def test_google_geocoder_needs_the_api_key() -> None:
    missing = geocoder_status(make_settings(GEOCODER_PROVIDER="google"))
    configured = geocoder_status(make_settings(GEOCODER_PROVIDER="google", MAPS_API_KEY="maps-key"))

    assert missing.configured is False
    assert missing.provider == "google"
    assert "MAPS_API_KEY" in missing.details
    assert configured.configured is True
    assert configured.provider == "google"


def test_routing_falls_back_to_the_haversine_estimate() -> None:
    estimate = routing_status(make_settings())
    osrm = routing_status(make_settings(OSRM_URL="http://osrm:5000"))

    assert estimate.configured is True
    assert estimate.provider == "haversine"
    assert "оценка" in estimate.details
    assert osrm.configured is True
    assert osrm.provider == "osrm"


def test_maxim_manual_mode_is_the_only_supported_one() -> None:
    manual = maxim_status(make_settings())
    api = maxim_status(make_settings(MAXIM_MODE="api"))

    assert manual.configured is True
    assert manual.provider == "manual"
    assert manual.details == MAXIM_MANUAL_DETAILS
    assert api.configured is False
    assert api.provider == "api"
    assert MAXIM_NO_API in api.details


# --------------------------------------------------------------------------- no secrets


@pytest.mark.parametrize("geocoder", ["nominatim", "google"])
def test_status_never_contains_a_secret_value(geocoder: str) -> None:
    settings = make_settings(**SECRET_ENV, GEOCODER_PROVIDER=geocoder, OSRM_URL="http://osrm:5000")

    payload = json.dumps(build_integration_status(settings).model_dump(mode="json"), ensure_ascii=False)

    for name, value in SECRET_ENV.items():
        if name == "INSTAGRAM_ACCOUNT_ID":  # an account id is public, but it is not echoed either
            continue
        assert value not in payload, f"{name} leaked into the integration status"


def test_build_integration_status_falls_back_to_application_settings() -> None:
    status = build_integration_status()

    assert set(status.model_dump()) == set(INTEGRATION_KEYS)
    assert status.maxim.provider == "manual"
