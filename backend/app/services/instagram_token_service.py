"""Instagram access token rotation (docs/architecture/06-integrations.md §1, §5).

A long-lived Instagram User token lives 60 days and can be refreshed once it is at least 24 hours old.
The daily beat task ``refresh_instagram_token`` refreshes it and stores the new token in
``app_settings["instagram_token"]``; the messenger then uses the stored token instead of
``INSTAGRAM_ACCESS_TOKEN``.

The stored token remembers a fingerprint of the env token it descends from: when an admin puts a new
token into ``.env``, the fingerprints differ and the env token wins again. The token itself is never
logged.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger, log_event
from app.core.time import ensure_utc, now_utc
from app.integrations.instagram.client import InstagramClient, InstagramMessenger, get_instagram_client
from app.repositories.app_settings import AppSettingRepository

logger = get_logger(__name__)

INSTAGRAM_TOKEN_KEY = "instagram_token"  # noqa: S105 - the app_settings key name, not a secret


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class InstagramTokenService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.repository = AppSettingRepository(db)

    @property
    def env_token(self) -> str:
        return (self.settings.INSTAGRAM_ACCESS_TOKEN or "").strip()

    def active_token(self) -> str:
        """The refreshed token when it descends from the current env token and has not expired."""
        stored: Any = self.repository.get_value(INSTAGRAM_TOKEN_KEY)
        env = self.env_token
        if not isinstance(stored, dict) or not env:
            return env
        token = stored.get("access_token")
        if not isinstance(token, str) or not token or stored.get("base_fingerprint") != _fingerprint(env):
            return env
        expires_at = stored.get("expires_at")
        if isinstance(expires_at, str):
            try:
                if ensure_utc(datetime.fromisoformat(expires_at)) <= now_utc():
                    return env
            except ValueError:
                return env
        return token

    def messenger(self) -> InstagramClient:
        return get_instagram_client(self.settings, access_token=self.active_token() or None)

    def refresh(self, messenger: InstagramMessenger | None = None) -> bool:
        """Refresh and store the token (commits). ``False`` when Instagram is not configured or refused."""
        if not self.env_token:
            log_event(logger, "instagram.token_refresh_skipped", reason="not_configured")
            return False
        client = messenger or self.messenger()
        try:
            token, expires_in = client.refresh_long_lived_token()
        except IntegrationError as exc:
            log_event(
                logger,
                "instagram.token_refresh_failed",
                level=logging.WARNING,
                code=exc.code,
                error=type(exc).__name__,
            )
            return False
        finally:
            if messenger is None and isinstance(client, InstagramClient):
                client.close()
        moment = now_utc()
        self.repository.set_value(
            INSTAGRAM_TOKEN_KEY,
            {
                "access_token": token,
                "expires_at": (moment + timedelta(seconds=expires_in)).isoformat(),
                "refreshed_at": moment.isoformat(),
                "base_fingerprint": _fingerprint(self.env_token),
            },
        )
        self.db.commit()
        log_event(logger, "instagram.token_stored", expires_in=expires_in)
        return True
