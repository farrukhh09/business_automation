"""Speech-to-text and text-to-speech adapters (06 §3)."""

from app.integrations.speech.audio import (
    convert_to_m4a,
    ffmpeg_available,
    prepare_instagram_audio,
    probe_duration,
)
from app.integrations.speech.base import (
    SpeechProviderError,
    SpeechToText,
    SynthesizedAudio,
    TextToSpeech,
    TranscriptionResult,
    normalize_language_code,
)
from app.integrations.speech.elevenlabs import ElevenLabsSTT
from app.integrations.speech.factory import get_stt, get_tts
from app.integrations.speech.openai_tts import OpenAITTS
from app.integrations.speech.policy import transcribe_with_retry

__all__ = [
    "ElevenLabsSTT",
    "OpenAITTS",
    "SpeechProviderError",
    "SpeechToText",
    "SynthesizedAudio",
    "TextToSpeech",
    "TranscriptionResult",
    "convert_to_m4a",
    "ffmpeg_available",
    "get_stt",
    "get_tts",
    "normalize_language_code",
    "prepare_instagram_audio",
    "probe_duration",
    "transcribe_with_retry",
]
