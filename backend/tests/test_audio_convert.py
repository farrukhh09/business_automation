"""ffmpeg helpers (06 §3: wav → .m4a when ffmpeg exists). No real ffmpeg: shutil.which and subprocess.run are faked."""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from app.integrations.speech import audio as audio_module
from app.integrations.speech.audio import (
    convert_to_m4a,
    ffmpeg_available,
    prepare_instagram_audio,
    probe_duration,
)
from app.integrations.speech.base import SynthesizedAudio

FAKE_FFMPEG = str(Path("C:/fake-bin/ffmpeg.exe"))
FAKE_FFPROBE = str(Path("C:/fake-bin/ffprobe.exe"))
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt fake-wav"
M4A = b"\x00\x00\x00\x20ftypM4A fake-m4a"


def patch_which(monkeypatch: pytest.MonkeyPatch, *, ffmpeg: bool = True, ffprobe: bool = True) -> None:
    paths = {"ffmpeg": FAKE_FFMPEG if ffmpeg else None, "ffprobe": FAKE_FFPROBE if ffprobe else None}
    monkeypatch.setattr(shutil, "which", lambda name, *args, **kwargs: paths.get(name))


class FakeRun:
    def __init__(
        self,
        *,
        returncode: int = 0,
        output: bytes | None = M4A,
        stdout: bytes = b"",
        stderr: bytes = b"",
        exc: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self.output = output
        self.stdout = stdout
        self.stderr = stderr
        self.exc = exc
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.input_bytes: bytes | None = None
        self.input_name: str | None = None
        self.workdir: Path | None = None

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        cmd = list(cmd)
        self.calls.append((cmd, kwargs))
        source = Path(cmd[cmd.index("-i") + 1]) if "-i" in cmd else Path(cmd[-1])
        self.input_bytes = source.read_bytes()
        self.input_name = source.name
        self.workdir = source.parent
        if self.exc is not None:
            raise self.exc
        if self.returncode == 0 and self.output is not None and cmd[-1].endswith(".m4a"):
            Path(cmd[-1]).write_bytes(self.output)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def forbid_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(audio_module.subprocess, "run", _fail)


def events(caplog: pytest.LogCaptureFixture) -> dict[str, logging.LogRecord]:
    return {record.event: record for record in caplog.records if hasattr(record, "event")}


class BoomTempDir:
    """TemporaryDirectory that cannot be created (full disk, unwritable TMPDIR)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")


_REAL_TEMP_DIR = tempfile.TemporaryDirectory  # captured before any monkeypatching


class CleanupFailsTempDir:
    """Real temp dir whose cleanup raises, as Windows does while a handle is still open."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.pop("ignore_cleanup_errors", None)
        self._real = _REAL_TEMP_DIR(**kwargs)

    def __enter__(self) -> str:
        return self._real.__enter__()

    def __exit__(self, *exc: Any) -> None:
        self._real.__exit__(*exc)
        raise PermissionError(32, "The process cannot access the file because it is being used")


def _deny_write(self: Path, data: bytes) -> int:
    raise PermissionError(13, "Permission denied")


def _deny_read(self: Path) -> bytes:
    raise OSError(5, "Input/output error")


# --------------------------------------------------------------------------- ffmpeg missing


def test_ffmpeg_missing(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.integrations.speech")
    patch_which(monkeypatch, ffmpeg=False, ffprobe=False)
    forbid_run(monkeypatch)

    assert ffmpeg_available() is False
    assert convert_to_m4a(WAV, "wav") is None
    assert probe_duration(M4A, "m4a") is None
    assert "speech.ffmpeg_unavailable" in events(caplog)


def test_ffmpeg_available(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch)
    assert ffmpeg_available() is True


# --------------------------------------------------------------------------- conversion


def test_convert_success_command_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch)
    fake = FakeRun()
    monkeypatch.setattr(audio_module.subprocess, "run", fake)

    result = convert_to_m4a(WAV, ".WAV")

    assert result == M4A
    assert len(fake.calls) == 1
    cmd, kwargs = fake.calls[0]
    assert cmd[0] == FAKE_FFMPEG
    assert cmd[1] == "-y"
    assert fake.input_bytes == WAV
    assert fake.input_name == "input.wav"
    joined = " ".join(cmd)
    assert "-c:a aac" in joined
    assert "-b:a 64k" in joined
    assert cmd[-1].endswith("output.m4a")
    assert Path(cmd[-1]).parent == fake.workdir
    assert kwargs["timeout"] == 60
    assert kwargs["capture_output"] is True
    assert kwargs.get("shell", False) is False
    assert fake.workdir is not None and not fake.workdir.exists()  # TemporaryDirectory removed


@pytest.mark.parametrize(
    ("ext", "expected_name"), [("m4a", "input.m4a"), ("../../evil", "input.bin"), ("", "input.bin")]
)
def test_input_extension_is_sanitized(monkeypatch: pytest.MonkeyPatch, ext: str, expected_name: str) -> None:
    patch_which(monkeypatch)
    fake = FakeRun()
    monkeypatch.setattr(audio_module.subprocess, "run", fake)

    assert convert_to_m4a(WAV, ext) == M4A
    assert fake.input_name == expected_name


@pytest.mark.parametrize(
    ("fake", "reason"),
    [
        (FakeRun(returncode=1, output=None, stderr=b"Invalid data found when processing input"), "exit_code"),
        (FakeRun(exc=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60)), "timeout"),
        (FakeRun(exc=FileNotFoundError("ffmpeg")), "os_error"),
        (FakeRun(output=None), "no_output"),
        (FakeRun(output=b""), "no_output"),
    ],
)
def test_convert_failures_return_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, fake: FakeRun, reason: str
) -> None:
    caplog.set_level(logging.INFO, logger="app.integrations.speech")
    patch_which(monkeypatch)
    monkeypatch.setattr(audio_module.subprocess, "run", fake)

    assert convert_to_m4a(WAV, "wav") is None
    record = events(caplog)["speech.ffmpeg_failed"]
    assert record.reason == reason
    assert record.levelno == logging.WARNING
    assert fake.workdir is not None and not fake.workdir.exists()


def test_convert_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch)
    forbid_run(monkeypatch)
    assert convert_to_m4a(b"", "wav") is None


def test_ffmpeg_and_ffprobe_never_inherit_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """ffmpeg reads stdin by default: a worker's stdin must not be consumed or block the call."""
    patch_which(monkeypatch)
    convert = FakeRun()
    monkeypatch.setattr(audio_module.subprocess, "run", convert)
    convert_to_m4a(WAV, "wav")

    cmd, kwargs = convert.calls[0]
    assert "-nostdin" in cmd
    assert cmd.index("-nostdin") < cmd.index("-i")
    assert kwargs["stdin"] == subprocess.DEVNULL

    probe = FakeRun(stdout=b"1.0\n")
    monkeypatch.setattr(audio_module.subprocess, "run", probe)
    probe_duration(M4A, "mp4")
    assert probe.calls[0][1]["stdin"] == subprocess.DEVNULL


def test_temp_dir_ignores_cleanup_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch)
    monkeypatch.setattr(audio_module.subprocess, "run", FakeRun())
    seen: list[dict[str, Any]] = []

    def spy(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return _REAL_TEMP_DIR(**kwargs)

    monkeypatch.setattr(audio_module.tempfile, "TemporaryDirectory", spy)

    assert convert_to_m4a(WAV, "wav") == M4A
    assert seen and seen[0]["ignore_cleanup_errors"] is True


# --------------------------------------------------------------------------- filesystem failures never escape


def test_convert_survives_temp_dir_creation_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.integrations.speech")
    patch_which(monkeypatch)
    forbid_run(monkeypatch)
    monkeypatch.setattr(audio_module.tempfile, "TemporaryDirectory", BoomTempDir)

    assert convert_to_m4a(WAV, "wav") is None
    record = events(caplog)["speech.ffmpeg_failed"]
    assert record.reason == "os_error"
    assert record.levelno == logging.WARNING


def test_convert_survives_temp_dir_cleanup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch)
    monkeypatch.setattr(audio_module.subprocess, "run", FakeRun())
    monkeypatch.setattr(audio_module.tempfile, "TemporaryDirectory", CleanupFailsTempDir)

    assert convert_to_m4a(WAV, "wav") is None


def test_convert_survives_unwritable_temp_file(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch)
    forbid_run(monkeypatch)
    monkeypatch.setattr(Path, "write_bytes", _deny_write)

    assert convert_to_m4a(WAV, "wav") is None


def test_convert_survives_unreadable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch)

    def writing_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        Path(cmd[-1]).write_bytes(M4A)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(audio_module.subprocess, "run", writing_run)
    monkeypatch.setattr(Path, "read_bytes", _deny_read)

    assert convert_to_m4a(WAV, "wav") is None


@pytest.mark.parametrize("temp_dir", [BoomTempDir, CleanupFailsTempDir])
def test_probe_duration_survives_filesystem_failures(monkeypatch: pytest.MonkeyPatch, temp_dir: type) -> None:
    patch_which(monkeypatch)
    monkeypatch.setattr(audio_module.subprocess, "run", FakeRun(stdout=b"1.0\n"))
    monkeypatch.setattr(audio_module.tempfile, "TemporaryDirectory", temp_dir)

    assert probe_duration(M4A, "mp4") is None


# --------------------------------------------------------------------------- probe_duration


@pytest.mark.parametrize(
    ("fake", "expected"),
    [
        (FakeRun(stdout=b"3.520000\n"), 3.52),
        (FakeRun(stdout=b"N/A\n"), None),
        (FakeRun(returncode=1), None),
        (FakeRun(exc=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)), None),
    ],
)
def test_probe_duration(monkeypatch: pytest.MonkeyPatch, fake: FakeRun, expected: float | None) -> None:
    patch_which(monkeypatch)
    monkeypatch.setattr(audio_module.subprocess, "run", fake)

    assert probe_duration(M4A, "mp4") == expected
    assert fake.calls[0][0][0] == FAKE_FFPROBE
    assert fake.input_bytes == M4A


# --------------------------------------------------------------------------- prepare_instagram_audio


def test_prepare_converts_wav_to_m4a(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch)
    monkeypatch.setattr(audio_module.subprocess, "run", FakeRun())

    result = prepare_instagram_audio(SynthesizedAudio(data=WAV, mime_type="audio/wav", extension="wav"))

    assert result == SynthesizedAudio(data=M4A, mime_type="audio/mp4", extension="m4a")


def test_prepare_falls_back_to_wav_without_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch, ffmpeg=False)
    forbid_run(monkeypatch)
    original = SynthesizedAudio(data=WAV, mime_type="audio/wav", extension="wav")

    assert prepare_instagram_audio(original) is original


def test_prepare_keeps_m4a_without_calling_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch)
    forbid_run(monkeypatch)
    original = SynthesizedAudio(data=M4A, mime_type="audio/mp4", extension="m4a")

    assert prepare_instagram_audio(original) is original


def test_prepare_unsupported_format_without_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch, ffmpeg=False)
    forbid_run(monkeypatch)

    assert prepare_instagram_audio(SynthesizedAudio(data=b"ID3fake", mime_type="audio/mpeg", extension="mp3")) is None


def test_prepare_falls_back_to_wav_when_conversion_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """06 §3: a broken ffmpeg step must degrade to sending WAV, never raise out of the task."""
    patch_which(monkeypatch)
    forbid_run(monkeypatch)
    monkeypatch.setattr(audio_module.tempfile, "TemporaryDirectory", BoomTempDir)
    original = SynthesizedAudio(data=WAV, mime_type="audio/wav", extension="wav")

    assert prepare_instagram_audio(original) is original


def test_prepare_rejects_oversized_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_which(monkeypatch, ffmpeg=False)
    monkeypatch.setattr(audio_module, "INSTAGRAM_AUDIO_MAX_BYTES", 10)

    assert prepare_instagram_audio(SynthesizedAudio(data=WAV, mime_type="audio/wav", extension="wav")) is None
