"""Deterministic small talk: greetings, thanks, goodbyes and bare acknowledgements (05-ai.md §5, step 3a).

A message made *only* of such words needs no model: "Добрый день" is answered in kind, "спасибо" after
a confirmed order is not a question the manager has to answer, "ок" while the bot is asking for a
phone number means "go on". Anything with more content ("спасибо, а сколько стоит доставка?",
"Добрый день, есть фисташковые?") is left to understanding.

Phrases are stored normalized + Tajik-folded like every other keyword list (``text_normalize``).
"""

import re
from collections import Counter
from enum import StrEnum

from app.ai.text_normalize import build_phrase_index, normalize_fold, scan_phrases

__all__ = ["Greeting", "SmallTalk", "contains_thanks", "detect_greeting", "detect_small_talk"]


class SmallTalk(StrEnum):
    GREETING = "greeting"  # "Добрый день", "Салом" — which one: ``detect_greeting``
    THANKS = "thanks"
    GOODBYE = "goodbye"
    ACK = "ack"  # "ок", "хорошо", "дальше" — the customer agrees to continue
    DONE = "done"  # "это всё", "больше ничего" — nothing more to add to the order; go on
    DECLINE = "decline"  # "тогда не надо", "дорого, спасибо" — the customer is backing out
    PING = "ping"  # "алло", "вы тут?", "?" — the customer checks that somebody is there
    CONFUSED = "confused"  # "не понял", "что?", "в смысле?" — our last reply was not clear
    HOW = "how"  # "как дела?", "шумо нағз ми?" — asked out of politeness
    WHO = "who"  # "вы кто?", "вы бот?", "как вас зовут?" — who is writing
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
        # typed as one word, as many Khujand customers do
        "саломалейкум",
        "саломалайкум",
        "ассаломуалейкум",
        "ассаломуалайкум",
        "salomaleykum",
        "assalomualeykum",
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
    "рахмат калон",
    "рахмат ба шумо",
    "катта рахмат",
    "ташаккур",
    "ташакур",
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
    "хайр хуш",
    "то дидор",
    "то боздид",
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
    "майли",
    "маъкул",
    "фахмидам",
    "фахмидем",
    "фахмидм",
    "давом дихед",
    "давом",
    "ха хуб",
    # Khujand: "боша" (fine), "нағз" (good)
    "боша",
    "ха боша",
    "хуб боша",
    "нагз",
    "ха нагз",
)

#: A bare "все"/"всё" is not here: after "какие именно?" it may mean "all of them".
_DONE = (
    "это все",
    "все на этом",
    "на этом все",
    "вот и все",
    "больше ничего",
    "ничего больше",
    "больше не надо",
    "больше не нужно",
    "больше ничего не надо",
    "только это",
    "хватит",
    "достаточно",
    "бас",
    "хамин бас",
    "бас хамин",
    "хамин",
    "хамин халос",
    "тамом",
    "хамин тамом",
    "дигар не",
    "дигар чиз не",
    "дигар лозим не",
    "дигар лозим нест",
)

#: Backing out before anything is placed: "тогда не надо", "не буду" (dialog #46, 21.09.2026). Only
#: whole messages count (``detect_small_talk``). "Подумаю" is deliberately absent — that is a maybe,
#: not a no — and so is "дорого": a complaint about the price is an objection the FAQ answers
#: ("Почему так дорого?", dialog #58, 22.09.2026), not a goodbye. "Потом напишу" is a "later", not a
#: "no": it used to drop the draft here while the FAQ answered it "Хорошо, ждём!" (audit 23.09.2026).
_DECLINE = (
    "не надо",
    "тогда не надо",
    "уже не надо",
    "не нужно",
    "тогда не нужно",
    "не буду",
    "не хочу",
    "передумал",
    "передумала",
    "откажусь",
    "отказываюсь",
    "в другой раз",
    "как нибудь потом",
    "нет не надо",
    "даркор не",
    "даркор нест",
    "лозим не",
    "лозим нест",
    "намегирам",
    "намехохам",
    "намехоҳам",
    "дигар вакт",
    "дигар вақт",
)

#: "Is anybody there?" — after a silence or an answer the customer did not expect. The reply says we
#: are here and repeats the open question, if there is one (audit 23.09.2026).
_PING = (
    "алло",
    "ало",
    "аллоо",
    "ау",
    "аууу",
    "вы тут",
    "вы здесь",
    "вы где",
    "есть кто",
    "есть кто нибудь",
    "кто нибудь",
    "ответьте",
    "ответьте пожалуйста",
    "почему не отвечаете",
    "вы отвечаете",
    "хастед",
    "хастед ми",
    "шумо хастед",
    "кужоед",
    "чаво",
    "чавоб дихед",
    "ҷавоб диҳед",
)

#: "Извините, что?)", "не понял", "в смысле?", "нафаҳмидам" — the customer did not understand our last
#: message (archive, 24.09.2026). The reply apologises and asks the open question again; the old
#: "не получилось понять сообщение" put the misunderstanding on the customer.
_CONFUSED = (
    "что",
    "чего",
    "извините что",
    "простите что",
    "не понял",
    "не поняла",
    "не понятно",
    "непонятно",
    "в смысле",
    "как это",
    "нафахмидам",
    "нафахмидем",
    "чи",
    "чи гуфтед",
)

#: "Как дела?", "Shumo nagzmi?" (archive, 24.09.2026) — answered politely, without the model.
_HOW = (
    "как дела",
    "как ваши дела",
    "как вы",
    "как поживаете",
    "шумо нагз ми",
    "шумо нагзми",
    "нагз ми",
    "нагзми",
    "чи хел шумо",
    "чихел шумо",
    "корхо нагз",
    "shumo nagzmi",
    "kak dela",
)

#: "Вас как зовут?", "вы бот?" — the bot says honestly that it is the bakery's assistant (prompt rule 14).
_WHO = (
    "вы кто",
    "кто вы",
    "кто это",
    "вас как зовут",
    "как вас зовут",
    "как тебя зовут",
    "вы бот",
    "ты бот",
    "это бот",
    "вы робот",
    "вы человек",
    "шумо бот",
    "шумо ки",
    "номатон чи",
)

_GREETING_CATEGORY = "greeting:"

_MARKERS, _MAX_PHRASE_LEN = build_phrase_index(
    [
        ("ack", _ACK),
        ("thanks", _THANKS),
        ("goodbye", _GOODBYE),
        ("done", _DONE),
        ("decline", _DECLINE),
        ("ping", _PING),
        ("confused", _CONFUSED),
        ("how", _HOW),
        ("who", _WHO),
        *((f"{_GREETING_CATEGORY}{greeting.value}", phrases) for greeting, phrases in _GREETINGS.items()),
    ]
)

#: Words that may accompany small talk without turning it into a request ("спасибо вам большое!").
_FILLER = frozenset(
    {"вам", "тебе", "вас", "большое", "огромное", "очень", "всем", "калон", "зиед", "ба", "шумо", "ну", "эй", "ми"}
)
#: A message of nothing but question marks (and maybe "!" or dots): "?", "??", "?!".
_QUESTION_MARKS_RE = re.compile(r"^\s*[?？]+[?？!.\s]*$")


def _scan(text: str | None) -> tuple[list[str], dict[str, list[str]]]:
    tokens = normalize_fold(text).split()
    return tokens, scan_phrases(tokens, _MARKERS, _MAX_PHRASE_LEN)


def detect_small_talk(text: str | None) -> SmallTalk:
    """Classify a message that consists only of small-talk phrases (plus fillers). Never raises.

    A greeting with anything else in it counts as the other part: "Здравствуйте, спасибо" is thanks;
    "Спасибо, это всё" is done — while an order is being filled, the "это всё" is what matters.
    """
    tokens, found = _scan(text)
    if not tokens and _QUESTION_MARKS_RE.match(str(text or "")):
        return SmallTalk.PING  # "?", "??", "?!" — nothing but a question mark
    if not found:
        return SmallTalk.NONE
    matched = Counter(token for phrases in found.values() for phrase in phrases for token in phrase.split())
    # Only the words outside the matched phrases may be fillers: "ба" inside "рӯз ба хайр" does not excuse "нарх".
    if any(token not in _FILLER for token in (Counter(tokens) - matched).elements()):
        return SmallTalk.NONE  # there is more in the message than small talk
    if "decline" in found:
        return SmallTalk.DECLINE  # before "thanks": "дорого, спасибо" is a refusal, not gratitude
    if "ping" in found:
        return SmallTalk.PING  # "Здравствуйте, вы тут?" asks whether anybody answers
    if "confused" in found:
        return SmallTalk.CONFUSED
    if "who" in found:
        return SmallTalk.WHO
    if "how" in found:
        return SmallTalk.HOW
    if "done" in found:
        return SmallTalk.DONE
    if "thanks" in found:
        return SmallTalk.THANKS
    if "goodbye" in found:
        return SmallTalk.GOODBYE
    if "ack" in found:
        return SmallTalk.ACK
    return SmallTalk.GREETING


def contains_thanks(text: str | None) -> bool:
    """True when the message thanks anywhere in it ("Спасибо! А доставка есть?"), not only as a whole.

    ``Responder`` uses this to tell a real thank-you from the gratitude the model invents at the
    start of an answer (23.09.2026): a question may be answered warmly, but not thanked for.
    """
    _, found = _scan(text)
    return "thanks" in found


def detect_greeting(text: str | None) -> Greeting | None:
    """The greeting used anywhere in the message ("Добрый вечер, есть синнамоны?" → EVENING), or ``None``."""
    _, found = _scan(text)
    return next((greeting for greeting in Greeting if f"{_GREETING_CATEGORY}{greeting.value}" in found), None)
