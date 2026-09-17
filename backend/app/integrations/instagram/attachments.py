"""Classification of incoming Instagram attachment types (06-integrations.md §1).

Attachment types on the "Instagram API with Instagram Login" examples page change over time
(``share`` deprecated after Feb 2026, ``ig_post``/``story``/``ig_story``/``media`` added), so the
mapping is deliberately tolerant: anything that is not audio or an image becomes a TEXT message
with a ``[вложение: <type>]`` label and its media is never downloaded.
"""

import re

from app.models.enums import MessageType

UNKNOWN_ATTACHMENT_TYPE = "unknown"
# Meta's attachment types are short lowercase identifiers (``audio``, ``story_mention``, ``ig_reel``).
# Anything else is reported as "unknown": the type is echoed into ``messages.text`` as
# ``[вложение: <type>]`` and from there into the LLM prompt, so it must stay a short, predictable
# token whatever arrives in the payload.
_ATTACHMENT_TYPE_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]{0,39}")

# Voice messages arrive as ``audio`` (not stated explicitly by Meta, see docs/research/voice.md §2.1).
VOICE_ATTACHMENT_TYPES: frozenset[str] = frozenset({"audio"})
# ``image`` covers "image, gif, or sticker"; bare ``sticker``/``gif`` are accepted defensively.
IMAGE_ATTACHMENT_TYPES: frozenset[str] = frozenset({"image", "sticker", "gif"})
# Story media must not be stored or cached (Story Mention policy); only the CDN URL may be kept.
STORY_ATTACHMENT_TYPES: frozenset[str] = frozenset({"story_mention", "story", "ig_story"})


def normalize_attachment_type(value: object) -> str:
    """``" Audio "`` → ``"audio"``; missing/blank/non-string/implausible → ``"unknown"``."""
    if isinstance(value, str) and value.strip():
        normalized = value.strip().lower()
        if _ATTACHMENT_TYPE_RE.fullmatch(normalized):
            return normalized
    return UNKNOWN_ATTACHMENT_TYPE


def classify_attachment(attachment_type: str | None) -> MessageType:
    """``audio`` → VOICE; ``image`` (incl. sticker/gif) → IMAGE; everything else → TEXT (with a label)."""
    normalized = normalize_attachment_type(attachment_type)
    if normalized in VOICE_ATTACHMENT_TYPES:
        return MessageType.VOICE
    if normalized in IMAGE_ATTACHMENT_TYPES:
        return MessageType.IMAGE
    return MessageType.TEXT


def attachment_label(attachment_type: str | None) -> str | None:
    """Text placeholder for attachments that are stored as TEXT: ``"[вложение: video]"``.

    VOICE and IMAGE attachments have no label (their content is the transcript / the image).
    """
    if classify_attachment(attachment_type) is not MessageType.TEXT:
        return None
    return f"[вложение: {normalize_attachment_type(attachment_type)}]"


def is_story_attachment(attachment_type: str | None) -> bool:
    return normalize_attachment_type(attachment_type) in STORY_ATTACHMENT_TYPES


def should_store_media(attachment_type: str | None) -> bool:
    """Only voice and image media are downloaded and stored; story media never is."""
    return not is_story_attachment(attachment_type) and classify_attachment(attachment_type) in (
        MessageType.VOICE,
        MessageType.IMAGE,
    )
