"""Deterministic small talk: thanks, goodbyes and bare acknowledgements (05-ai.md §5, step 3a).

A message made *only* of such words needs no model: "спасибо" after a confirmed order is not a
question the manager has to answer, "ок" while the bot is asking for a phone number means "go on".
Anything with more content ("спасибо, а сколько стоит доставка?") is left to understanding.

Phrases are stored normalized + Tajik-folded like every other keyword list (``text_normalize``).
"""

from enum import StrEnum

from app.ai.text_normalize import build_phrase_index, normalize_text, scan_phrases, tajik_fold

__all__ = ["SmallTalk", "detect_small_talk"]


class SmallTalk(StrEnum):
    THANKS = "thanks"
    GOODBYE = "goodbye"
    ACK = "ack"  # "ок", "хорошо", "дальше" — the customer agrees to continue
    NONE = "none"


_THANKS = (
    "спасибо",
    "спасибо большое",
    "большое спасибо",
    "спасибо вам",
    "благодарю",
    "благодарим",
    "спс",
    "сенкс",
    "рахмат",
    "рахмати калон",
    "ташаккур",
    "ташаккури зиёд",
    "сипос",
    "thanks",
    "thank you",
    "rahmat",
    "tashakkur",
    "spasibo",
)

_GOODBYE = (
    "до свидания",
    "досвидания",
    "до встречи",
    "всего доброго",
    "всего хорошего",
    "хорошего дня",
    "хорошего вечера",
    "пока",
    "пока пока",
    "удачи",
    "хайр",
    "то дидор",
    "саломат бошед",
    "рузи хуш",
    "шаби хуш",
    "bye",
    "poka",
)

_ACK = (
    "ок",
    "оке",
    "окей",
    "ok",
    "okay",
    "ага",
    "угу",
    "хорошо",
    "ладно",
    "понял",
    "поняла",
    "понятно",
    "ясно",
    "принято",
    "дальше",
    "далее",
    "продолжим",
    "продолжайте",
    "давайте",
    "давай",
    "давайте дальше",
    "оформляйте",
    "оформляем",
    "хуб",
    "хуб аст",
    "майлаш",
    "маъкул",
    "фахмидам",
    "фахмидем",
    "давом дихед",
    "давом",
    "ха хуб",
)

_MARKERS, _MAX_PHRASE_LEN = build_phrase_index([("ack", _ACK), ("thanks", _THANKS), ("goodbye", _GOODBYE)])

#: Words that may accompany small talk without turning it into a request ("спасибо вам большое!").
_FILLER = frozenset({"вам", "тебе", "вас", "большое", "огромное", "очень", "всем", "калон", "зиед", "ба", "шумо"})


def detect_small_talk(text: str | None) -> SmallTalk:
    """Classify a message that consists only of small-talk phrases (plus fillers). Never raises."""
    normalized = normalize_text(text)
    if not normalized:
        return SmallTalk.NONE
    tokens = tajik_fold(normalized).split()
    found = scan_phrases(tokens, _MARKERS, _MAX_PHRASE_LEN)
    if not found:
        return SmallTalk.NONE
    covered = sum(len(phrase.split()) for phrases in found.values() for phrase in phrases)
    fillers = sum(1 for token in tokens if token in _FILLER)
    if covered + fillers < len(tokens):
        return SmallTalk.NONE  # there is more in the message than small talk
    if "thanks" in found:
        return SmallTalk.THANKS
    if "goodbye" in found:
        return SmallTalk.GOODBYE
    return SmallTalk.ACK
