"""Splitting outgoing text for the Instagram Send API byte limit.

"Message text must be UTF-8 and be a 1000 bytes or less" (docs/research/instagram.md §5.1).
Cyrillic and Tajik letters (ҳ ӣ ӯ ҷ қ ғ) take 2 bytes, emoji 4 bytes, so ~500 Cyrillic characters
fit in one message.

``split_text`` packs text greedily into chunks of at most ``limit`` UTF-8 bytes, cutting at the
coarsest boundary that works: paragraphs → lines → sentences → words → grapheme clusters
(an approximation that keeps combining marks, variation selectors, skin tones, ZWJ emoji sequences
and flag pairs together) → code points. A character is never split, order is preserved, and
whitespace at chunk edges is dropped.
"""

import re
import unicodedata

MAX_TEXT_BYTES = 1000
_MIN_LIMIT = 4  # the longest UTF-8 encoded code point

_SEPARATORS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\n[ \t\r\f\v]*\n\s*"),  # paragraph: a blank line
    re.compile(r"\n\s*"),  # line break
    re.compile(r"[.!?…]+[\"'»”’)\]]*\s+"),  # sentence end (punctuation stays with the sentence)
    re.compile(r"\s+"),  # word
)
_CLUSTER_LEVEL = len(_SEPARATORS)
_CODEPOINT_LEVEL = _CLUSTER_LEVEL + 1

_ZWJ = "‍"


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _is_regional_indicator(char: str) -> bool:
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def _extends_cluster(char: str) -> bool:
    code = ord(char)
    return (
        char == _ZWJ
        or unicodedata.category(char) in ("Mn", "Me", "Mc")  # combining marks, incl. U+FE0F
        or 0xFE00 <= code <= 0xFE0F  # variation selectors
        or 0x1F3FB <= code <= 0x1F3FF  # emoji skin tone modifiers
        or 0xE0020 <= code <= 0xE007F  # emoji tag sequences (subdivision flags)
    )


def grapheme_clusters(text: str) -> list[str]:
    """Approximate user-perceived characters (sufficient for safe cutting, not full UAX #29)."""
    clusters: list[str] = []
    for char in text:
        if clusters:
            last = clusters[-1]
            joins_flag = len(last) == 1 and _is_regional_indicator(last) and _is_regional_indicator(char)
            if _extends_cluster(char) or last.endswith(_ZWJ) or joins_flag:
                clusters[-1] = last + char
                continue
        clusters.append(char)
    return clusters


def _split_keeping_separators(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Pieces whose concatenation is ``text``; each separator stays at the end of its piece."""
    pieces: list[str] = []
    start = 0
    for match in pattern.finditer(text):
        if match.end() > start:
            pieces.append(text[start : match.end()])
            start = match.end()
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def _pieces(text: str, level: int) -> list[str]:
    if level < _CLUSTER_LEVEL:
        return _split_keeping_separators(text, _SEPARATORS[level])
    if level == _CLUSTER_LEVEL:
        return grapheme_clusters(text)
    return list(text)


def _pack(text: str, limit: int, level: int) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if utf8_len(stripped) <= limit:
        return [stripped]
    pieces = _pieces(stripped, level)
    if len(pieces) <= 1:
        # No boundary of this kind; at the code point level there are always ≥ 2 pieces here.
        return _pack(stripped, limit, level + 1)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = current + piece
        if utf8_len(candidate.rstrip()) <= limit:
            current = candidate
            continue
        if current.strip():
            chunks.append(current.strip())
        if utf8_len(piece.rstrip()) <= limit:
            current = piece
        else:
            chunks.extend(_pack(piece, limit, level + 1))
            current = ""
    if current.strip():
        chunks.append(current.strip())
    return chunks


def split_text(text: str, limit: int = MAX_TEXT_BYTES) -> list[str]:
    """Split ``text`` into ordered chunks of at most ``limit`` UTF-8 bytes (blank text → ``[]``)."""
    if limit < _MIN_LIMIT:
        raise ValueError(f"limit must be at least {_MIN_LIMIT} bytes")
    return _pack(text, limit, 0)
