"""Media files in ``MEDIA_ROOT`` (docs/architecture/04-api.md §13, 06-integrations.md §1, §3).

Two kinds of files, told apart by the name prefix:

- ``in-*`` — media of incoming messages (voice, images), downloaded right away because Instagram
  CDN links expire; shown in the admin panel ("Диалоги");
- ``tts-*`` — synthesized voice replies; Instagram fetches them by public URL; removed after 7 days;
- ``pl-*`` — the price list picture the owner uploads in the settings (03 §1.4); Instagram fetches
  it by public URL on every product question, so it is never cleaned up — only replaced.

Names are ``<prefix>-<32 random url-safe chars>.<ext>``: unguessable, so the public
``GET /api/media/{filename}`` works as a capability link. The name is validated by a strict pattern
and the resolved path must stay inside the root, so no path traversal is possible.
"""

import logging
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from app.core.logging import get_logger, log_event
from app.core.time import now_utc

logger = get_logger(__name__)

__all__ = [
    "INCOMING_PREFIX",
    "PRICE_LIST_NAME_PATTERN",
    "PRICE_LIST_PREFIX",
    "TTS_PREFIX",
    "TTS_RETENTION",
    "MediaStorage",
    "extension_for",
    "media_type_for",
]

INCOMING_PREFIX = "in"
TTS_PREFIX = "tts"
PRICE_LIST_PREFIX = "pl"
TTS_RETENTION = timedelta(days=7)
MEDIA_URL_PREFIX = "/api/media/"

_TOKEN_BYTES = 24  # → 32 url-safe characters
_NAME_BODY = r"-[A-Za-z0-9_-]{16,64}\.[a-z0-9]{2,4}$"
_FILENAME_RE = re.compile(r"^(in|tts|pl)" + _NAME_BODY)
#: The name of a stored price list picture — the only media name a setting may hold (04 §12).
PRICE_LIST_NAME_PATTERN = f"^{PRICE_LIST_PREFIX}{_NAME_BODY}"

_MEDIA_TYPES: dict[str, str] = {
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "aac": "audio/aac",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "webm": "audio/webm",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bin": "application/octet-stream",
}

_EXTENSIONS: dict[str, str] = {
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "video/mp4": "mp4",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/webm": "webm",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def extension_for(content_type: str | None, default: str = "bin") -> str:
    return _EXTENSIONS.get((content_type or "").split(";", 1)[0].strip().lower(), default)


def media_type_for(filename: str) -> str:
    return _MEDIA_TYPES.get(filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")


class MediaStorage:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------ names and urls

    @staticmethod
    def is_valid_name(filename: str) -> bool:
        return bool(_FILENAME_RE.fullmatch(filename or ""))

    @staticmethod
    def url_path(filename: str) -> str:
        """Relative URL stored in ``messages.audio_url`` / ``media_url`` (the admin panel proxies ``/api``)."""
        return f"{MEDIA_URL_PREFIX}{filename}"

    @staticmethod
    def filename_from_url(url: str | None) -> str | None:
        if not url:
            return None
        name = url.rsplit("/", 1)[-1]
        return name if MediaStorage.is_valid_name(name) else None

    @staticmethod
    def public_url(filename: str, public_base_url: str) -> str:
        """Absolute URL Instagram downloads a voice reply from (06 §3)."""
        return f"{public_base_url.rstrip('/')}{MEDIA_URL_PREFIX}{filename}"

    # ------------------------------------------------------------------ files

    def save(self, data: bytes, *, prefix: str, extension: str) -> str:
        if prefix not in (INCOMING_PREFIX, TTS_PREFIX, PRICE_LIST_PREFIX):
            raise ValueError(f"unknown media prefix: {prefix!r}")
        ext = extension.strip().lstrip(".").lower()
        if ext not in _MEDIA_TYPES:
            ext = "bin"
        filename = f"{prefix}-{secrets.token_urlsafe(_TOKEN_BYTES)}.{ext}"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / filename).write_bytes(data)
        log_event(logger, "media.saved", prefix=prefix, extension=ext, size_bytes=len(data))
        return filename

    def path_for(self, filename: str) -> Path | None:
        """Existing file inside the root for a valid name, else ``None``."""
        if not self.is_valid_name(filename):
            return None
        root = self.root.resolve()
        path = (root / filename).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        return path

    def read(self, filename: str) -> bytes | None:
        path = self.path_for(filename)
        return path.read_bytes() if path is not None else None

    def delete(self, filename: str | None) -> bool:
        """Remove one file by name (the replaced price list picture). Missing or invalid → ``False``."""
        path = self.path_for(filename or "")
        if path is None:
            return False
        try:
            path.unlink()
        except OSError as exc:
            log_event(logger, "media.delete_failed", level=logging.WARNING, error=type(exc).__name__)
            return False
        return True

    def cleanup(
        self, *, prefix: str = TTS_PREFIX, older_than: timedelta = TTS_RETENTION, now: datetime | None = None
    ) -> int:
        """Delete ``prefix`` files older than ``older_than`` (06 §5 ``cleanup_media``). Returns the count."""
        if not self.root.is_dir():
            return 0
        threshold = (now or now_utc()).timestamp() - older_than.total_seconds()
        removed = 0
        for path in self.root.glob(f"{prefix}-*"):
            if not path.is_file() or not self.is_valid_name(path.name):
                continue
            try:
                if path.stat().st_mtime < threshold:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                log_event(logger, "media.cleanup_failed", level=logging.WARNING, error=type(exc).__name__)
        if removed:
            log_event(logger, "media.cleaned", prefix=prefix, removed=removed)
        return removed
