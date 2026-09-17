"""Deterministic small talk: greetings, thanks, goodbyes and bare acknowledgements (05-ai.md §5, step 3a).

A message made *only* of such words needs no model: "Добрый день" is answered in kind, "спасибо" after
a confirmed order is not a question the manager has to answer, "ок" while the bot is asking for a
phone number means "go on". Anything with more content ("спасибо, а сколько стоит доставка?",
"Добрый день, есть фисташковые?") is left to understanding.

Phrases are stored normalized + Tajik-folded like every other keyword list (``text_normalize``).
"""

from collections import Counter
from enum import StrEnum

from app.ai.text_normalize import build_phrase_index, normalize_fold, scan_phrases

__all__ = ["Greeting", "SmallTalk", "detect_greeting", "detect_small_talk"]


class SmallTalk(StrEnum):
    GREETING = "greeting"  # "Добрый день", "Салом" — which one: ``detect_greeting``
    THANKS = "thanks"
    GOODBYE = "goodbye"
    ACK = "ack"  # "ок", "хорошо", "дальше" — the customer agrees to continue
    NONE = "none"


class Greeting(StrEnum):
    """The customer's greeting, returned in kind ("Добрый вечер" → "Добрый вечер!").

    Declared from the most specific one: "Здравствуйте, добрый день" is answered "Добрый день".
    """

    SALAM = "salam"  # "Ассалому алейкум" → "Ва алейкум ассалом"
    MORNING = "morning"
    DAY = "day"
    EVENING = "evening"
    HELLO = "hello"


_GREETINGS: dict[Greeting, tuple[str, ...]] = {
    Greeting.SALAM: (
        *(
            f"{first} {second}"
            for first in (
                "ассалому",
                "ассалом",
                "ассаламу",
                "ассалам",
                "ассаляму",
                "ассалям",
                "салом",
                "салам",
                "салям",
            )
            for second in ("алейкум", "алайкум", "алекум")
        ),
        *(
            f"{first} {second}"
            for first in ("assalomu", "assalom", "assalamu", "salom", "salam")
            for second in ("aleykum", "alaykum", "aleikum", "alaikum")
        ),
    ),
    Greeting.MORNING: ("доброе утро", "утро доброе", "субх ба хайр", "dobroe utro"),
    Greeting.DAY: ("добрый день", "день добрый", "руз ба хайр", "dobryy den", "dobriy den"),
    Greeting.EVENING: ("добрый вечер", "вечер добрый", "шом ба хайр", "dobryy vecher", "dobriy vecher"),
    Greeting.HELLO: (
        "здравствуйте",
        "здраствуйте",
        "здравствуй",
        "здрасте",
        "здрасьте",
        "привет",
        "приветик",
        "приветствую",
        "добрый",
        "доброго времени суток",
        "салом",
        "салам",
        "слм",
        "ассалом",
        "ассалому",
        "hello",
        "hi",
        "salom",
        "salam",
        "privet",
        "zdravstvuyte",
    ),
}

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

_GREETING_CATEGORY = "greeting:"

_MARKERS, _MAX_PHRASE_LEN = build_phrase_index(
    [
        ("ack", _ACK),
        ("thanks", _THANKS),
        ("goodbye", _GOODBYE),
        *((f"{_GREETING_CATEGORY}{greeting.value}", phrases) for greeting, phrases in _GREETINGS.items()),
    ]
)

#: Words that may accompany small talk without turning it into a request ("спасибо вам большое!").
_FILLER = frozenset({"вам", "тебе", "вас", "большое", "огромное", "очень", "всем", "калон", "зиед", "ба", "шумо"})


def _scan(text: str | None) -> tuple[list[str], dict[str, list[str]]]:
    tokens = normalize_fold(text).split()
    return tokens, scan_phrases(tokens, _MARKERS, _MAX_PHRASE_LEN)


def detect_small_talk(text: str | None) -> SmallTalk:
    """Classify a message that consists only of small-talk phrases (plus fillers). Never raises.

    A greeting with anything else in it counts as the other part: "Здравствуйте, спасибо" is thanks.
    """
    tokens, found = _scan(text)
    if not found:
        return SmallTalk.NONE
    matched = Counter(token for phrases in found.values() for phrase in phrases for token in phrase.split())
    # Only the words outside the matched phrases may be fillers: "ба" inside "рӯз ба хайр" does not excuse "нарх".
    if any(token not in _FILLER for token in (Counter(tokens) - matched).elements()):
        return SmallTalk.NONE  # there is more in the message than small talk
    if "thanks" in found:
        return SmallTalk.THANKS
    if "goodbye" in found:
        return SmallTalk.GOODBYE
    if "ack" in found:
        return SmallTalk.ACK
    return SmallTalk.GREETING


def detect_greeting(text: str | None) -> Greeting | None:
    """The greeting used anywhere in the message ("Добрый вечер, есть синнамоны?" → EVENING), or ``None``."""
    _, found = _scan(text)
    return next((greeting for greeting in Greeting if f"{_GREETING_CATEGORY}{greeting.value}" in found), None)
