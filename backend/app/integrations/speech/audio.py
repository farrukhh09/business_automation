"""Audio conversion helpers built on the ffmpeg CLI (06 §3: wav → .m4a AAC when ffmpeg is available).

ffmpeg is optional: every helper returns ``None`` (and logs ``speech.ffmpeg_unavailable`` /
``speech.ffmpeg_failed``) instead of raising, so callers can fall back to sending WAV.
"""

import logging
import re
import shutil
import subprocess  # noqa: S404 - fixed argv, no shell
import tempfile
from pathlib import Path

from app.core.logging import get_logger, log_event
from app.integrations.speech.base import SynthesizedAudio

logger = get_logger(__name__)

FFMPEG_TIMEOUT_SECONDS = 60.0
INSTAGRAM_AUDIO_MAX_BYTES = 25 * 1024 * 1024  # 25MB (docs/research/voice.md §2.2)
INSTAGRAM_AUDIO_EXTENSIONS = frozenset({"aac", "m4a", "wav", "mp4"})
_STDERR_TAIL = 300
_SAFE_EXT_RE = re.compile(r"^[a-z0-9]{1,5}$")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run(cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
    """Fixed argv, no shell, no inherited stdin (ffmpeg reads stdin and would block a worker)."""
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
    )


def _safe_extension(ext: str) -> str:
    cleaned = (ext or "").strip().lstrip(".").lower()
    return cleaned if _SAFE_EXT_RE.match(cleaned) else "bin"


def _temporary_directory() -> "tempfile.TemporaryDirectory[str]":
    """Scratch directory whose cleanup never raises (Windows keeps handles open a little longer)."""
    return tempfile.TemporaryDirectory(prefix="speech-", ignore_cleanup_errors=True)


def convert_to_m4a(data: bytes, input_ext: str = "wav", *, timeout: float = FFMPEG_TIMEOUT_SECONDS) -> bytes | None:
    """Transcode ``data`` to AAC in an .m4a container. ``None`` if ffmpeg is missing or fails.

    Never raises: a full disk, an unwritable temp directory or a failed cleanup must not break the
    caller's fallback to sending WAV (06 §3).
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        log_event(logger, "speech.ffmpeg_unavailable", level=logging.WARNING)
        return None
    if not data:
        log_event(logger, "speech.ffmpeg_failed", level=logging.WARNING, reason="empty_input")
        return None
    ext = _safe_extension(input_ext)
    try:
        with _temporary_directory() as tmp:
            source = Path(tmp) / f"input.{ext}"
            target = Path(tmp) / "output.m4a"
            source.write_bytes(data)
            cmd = [
                ffmpeg, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-vn", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
                str(target),
            ]  # fmt: skip
            try:
                completed = _run(cmd, timeout=timeout)
            except subprocess.TimeoutExpired:
                log_event(logger, "speech.ffmpeg_failed", level=logging.WARNING, reason="timeout", timeout_s=timeout)
                return None
            if completed.returncode != 0:
                stderr = (completed.stderr or b"").decode("utf-8", errors="replace")[-_STDERR_TAIL:]
                log_event(
                    logger, "speech.ffmpeg_failed", level=logging.WARNING, reason="exit_code",
                    returncode=completed.returncode, stderr_tail=stderr,
                )  # fmt: skip
                return None
            if not target.exists() or target.stat().st_size == 0:
                log_event(logger, "speech.ffmpeg_failed", level=logging.WARNING, reason="no_output")
                return None
            output = target.read_bytes()
    except OSError as exc:  # ffmpeg missing at exec time, unwritable/unreadable temp files, full disk
        log_event(
            logger, "speech.ffmpeg_failed", level=logging.WARNING,
            reason="os_error", error=type(exc).__name__,
        )  # fmt: skip
        return None
    log_event(logger, "speech.ffmpeg_converted", input_ext=ext, input_bytes=len(data), output_bytes=len(output))
    return output


def probe_duration(data: bytes, input_ext: str = "mp4", *, timeout: float = 30.0) -> float | None:
    """Duration in seconds via ffprobe; ``None`` if ffprobe is missing, fails or reports nothing.

    Never raises, for the same reason as ``convert_to_m4a``.
    """
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None or not data:
        return None
    ext = _safe_extension(input_ext)
    try:
        with _temporary_directory() as tmp:
            source = Path(tmp) / f"input.{ext}"
            source.write_bytes(data)
            cmd = [
                ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(source),
            ]  # fmt: skip
            try:
                completed = _run(cmd, timeout=timeout)
            except subprocess.TimeoutExpired:
                return None
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    try:
        value = float((completed.stdout or b"").decode("ascii", errors="ignore").strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def prepare_instagram_audio(audio: SynthesizedAudio) -> SynthesizedAudio | None:
    """Make audio sendable to Instagram (aac/m4a/wav/mp4, ≤25MB).

    WAV/other → .m4a via ffmpeg when possible; if conversion is impossible WAV is returned as is
    (06 §3), any other unsupported format → ``None``. Oversized results → ``None``.
    """
    ext = _safe_extension(audio.extension)
    result: SynthesizedAudio | None
    if ext in ("m4a", "aac", "mp4"):
        result = audio
    else:
        converted = convert_to_m4a(audio.data, ext)
        if converted is not None:
            result = SynthesizedAudio(data=converted, mime_type="audio/mp4", extension="m4a")
        elif ext in INSTAGRAM_AUDIO_EXTENSIONS:
            result = audio
        else:
            result = None
    if result is not None and len(result.data) > INSTAGRAM_AUDIO_MAX_BYTES:
        log_event(logger, "speech.audio_too_large", level=logging.WARNING, size=len(result.data))
        return None
    return result
