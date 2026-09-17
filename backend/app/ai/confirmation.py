"""Deterministic order-confirmation classifier (03-business-rules.md §5, 05-ai.md §1).

The LLM never confirms an order: ``DialogService`` moves ``WAITING_CONFIRMATION → CONFIRMED``
only when this function returns :attr:`ConfirmationDecision.YES` *and* the order has not
changed since the summary was shown (03 §5, last paragraph).

Decision order (each step is a rule from 03 §5; the first match wins)::

    0. empty message / emoji-only                          → UNCERTAIN
    1. cancellation word ("отмена", "бекор")               → NO      (a refusal is never a YES)
    2. negation or change verb  + substantive details      → CHANGE  (extract what to change)
                                without details            → NO
    3. doubt marker ("наверное", "ну", "шояд")             → UNCERTAIN
       (with details but no agreement: "может быть в 19:00" → CHANGE)
    4. explicit agreement       + substantive details      → CHANGE  ("да, но время 19:00")
                                + more than 6 words        → UNCERTAIN (03 §5: YES is short)
                                otherwise                  → YES
    5. anything else            + substantive details      → CHANGE  (a normal message: re-understand)
                                otherwise                  → UNCERTAIN

Documented decisions where 03 §5 leaves room:

* **negation + details → CHANGE.**  "нет, время другое" is answered by extracting the change
  (05 §5, step 3: ``CHANGE`` continues to ``understand()``), not by a bare "что изменить?".
* **a bare change verb → NO.**  "поменяйте" alone carries nothing to extract, so the safe
  answer is the ``ASK_WHAT_TO_CHANGE`` plan that ``NO`` produces (03 §5 lists those verbs
  under ``NO``).
* **cancellation → NO even after "да"** ("да, отмените").  ``classify_confirmation`` serves
  both ``awaiting="confirmation"`` and ``awaiting="cancel_confirmation"`` (05 §5, step 3); in
  the first flow confirming an order the customer wants gone would be the one unrecoverable
  mistake, so cancellation words always resolve to the safe side.
* **agreement + any number → CHANGE.**  "да, подтверждаю заказ на 16.09" re-runs extraction
  and shows the summary again instead of confirming on a possibly changed value.
* The YES vocabulary is exactly the 03 §5 lists (plus inflections of those words).  Words like
  "конечно" or "давайте" are *not* agreement here — they fall through to UNCERTAIN and the bot
  asks the customer to write "Да" (03 §5, weak-acknowledgement rule).
"""

from enum import StrEnum

from app.ai.text_normalize import build_phrase_index, normalize_text, scan_phrases, tajik_fold

__all__ = [
    "MAX_YES_WORDS",
    "ConfirmationDecision",
    "classify_cancel_confirmation",
    "classify_confirmation",
    "is_hesitation",
    "mentions_cancellation",
]


class ConfirmationDecision(StrEnum):
    YES = "YES"
    NO = "NO"
    UNCERTAIN = "UNCERTAIN"
    CHANGE = "CHANGE"


#: 03 §5: "YES только если сообщение короткое (≤ 6 слов)".
MAX_YES_WORDS = 6

# Marker categories.  Every phrase is stored normalized + Tajik-folded (see text_normalize),
# so "ҳа" is listed once as "ха" and "тасдиқ мекунам" once as "тасдик мекунам".
_AGREEMENT = (
    # RU (03 §5)
    "да",
    "да да",
    "подтверждаю",
    "подтверждаем",
    "да подтверждаю",
    "верно",
    "все верно",
    "да верно",
    "да все верно",
    "правильно",
    "все правильно",
    "да правильно",
    "да все правильно",
    "согласен",
    "согласна",
    "согласны",
    "да согласен",
    "да согласна",
    # TG (03 §5), folded
    "ха",
    "ха ха",
    "бале",
    "дуруст",
    "ха дуруст",
    "бале дуруст",
    "хама дуруст",
    "хамааш дуруст",
    "хама чиз дуруст",
    "тасдик",
    "тасдик мекунам",
    "ха тасдик мекунам",
)

_HEDGE = (
    # RU (03 §5: "маркеры сомнения") — SPEC §13 lists «ну вроде», «наверное», «может быть»,
    # «посмотрим», «думаю да» as messages that must never confirm.
    "наверное",
    "наверно",
    "вроде",
    "вроде бы",
    "может",
    "может быть",
    "возможно",
    "посмотрим",
    "поглядим",
    "думаю",
    "кажется",
    "похоже",
    "скорее всего",
    "не знаю",
    "не уверен",
    "не уверена",
    "не уверены",
    "ну",
    "типа",
    # TG (03 §5), folded
    "шояд",
    "эхтимол",
    "мебинем",
    "фикр мекунам",
    "намедонам",
    "намедонем",
)

_NEGATION = (
    # RU (03 §5: "отрицание")
    "нет",
    "не",
    "неверно",
    "не верно",
    "неправильно",
    "не правильно",
    "не так",
    "не то",
    "не надо",
    "не нужно",
    "нет не надо",
    # TG (03 §5), folded
    "нест",
    "хато",
    "не дуруст",
    "нодуруст",
    "намехохам",
)

_CANCELLATION = (
    "отмена",
    "отмени",
    "отмените",
    "отменить",
    "отменяю",
    "бекор",
    "бекор кун",
    "бекор кунед",
    "бекор мекунам",
)

_CHANGE_VERBS = (
    # 03 §5 lists these under NO; with details they become CHANGE (see module docstring).
    "измените",
    "изменить",
    "изменим",
    "поменяйте",
    "поменять",
    "поменяем",
    "перенесите",
    "перенести",
    "замените",
    "заменить",
    "добавьте",
    "добавить",
    "уберите",
    "убрать",
    "исправьте",
    "исправить",
    "иваз кунед",
    "илова кунед",
    "тагйир дихед",
)

_WEAK_ACK = (
    # 03 §5: "при одиночных эмодзи/стикерах (👍, ✅, ок, ok, ага, угу) — бот просит написать «Да»".
    # Emoji are removed by normalization, so a lone 👍 arrives here as an empty message.
    "ок",
    "оке",
    "окей",
    "ok",
    "okay",
    "ага",
    "угу",
    "угум",
    "хорошо",
    "ладно",
    "спасибо",
    "рахмат",
    "ташаккур",
    "хуб",
    "майлаш",
    "маъкул",
)

_MARKERS, _MAX_PHRASE_LEN = build_phrase_index(
    [
        ("ack", _WEAK_ACK),
        ("agree", _AGREEMENT),
        ("hedge", _HEDGE),
        ("negate", _NEGATION),
        ("cancel", _CANCELLATION),
        ("change", _CHANGE_VERBS),
    ]
)

# "Содержательные детали" (03 §5): anything that can alter the order — times, dates, amounts,
# products, order fields, and the contrast/addition words that introduce them.
_DETAIL_WORDS = frozenset(
    {
        # RU contrast / addition
        "но",
        "однако",
        "только",
        "лучше",
        "вместо",
        "кроме",
        "плюс",
        "без",
        "еще",
        "раньше",
        "позже",
        "другое",
        "другой",
        "другая",
        "другую",
        "другим",
        "другому",
        "других",
        # RU order fields
        "время",
        "времени",
        "дата",
        "даты",
        "дату",
        "адрес",
        "адреса",
        "адресу",
        "телефон",
        "телефона",
        "доставка",
        "доставку",
        "доставки",
        "самовывоз",
        "самовывозом",
        "получатель",
        "получателя",
        # RU dates / times in words
        "завтра",
        "сегодня",
        "послезавтра",
        "утром",
        "вечером",
        "днем",
        "ночью",
        "утро",
        "вечер",
        "час",
        "часа",
        "часов",
        # TG (folded)
        "вакт",
        "соат",
        "руз",
        "фардо",
        "пагох",
        "имруз",
        "сурога",
        "дигар",
        "лекин",
        "аммо",
        "факат",
        "бехтар",
        "илова",
        "тагйир",
    }
)

_DETAIL_STEMS = (
    "торт",
    "пирожн",
    "десерт",
    "капкейк",
    "чизкейк",
    "эклер",
    "медовик",
    "бархат",
    "наполеон",
    "коробк",
    "штук",
    "килограмм",
    "ширин",
)


def _has_details(tokens: list[str]) -> bool:
    """03 §5: substantive details — numbers, dates/times, products, order fields.

    Change verbs are deliberately *not* details: a bare "поменяйте" must stay ``NO`` so the bot
    asks what exactly to change.
    """
    for token in tokens:
        if any(char.isdigit() for char in token):
            return True
        if token in _DETAIL_WORDS or token.startswith(_DETAIL_STEMS):
            return True
    return False


def classify_confirmation(text: str | None) -> ConfirmationDecision:
    """Classify a reply to the order summary (03 §5).  Never raises; unknown input → UNCERTAIN."""
    normalized = normalize_text(text)
    if not normalized:
        # Empty message, or one that was only emoji/punctuation ("👍", "✅") — 03 §5 asks
        # the bot to request a written "Да".
        return ConfirmationDecision.UNCERTAIN

    tokens = tajik_fold(normalized).split()
    found = scan_phrases(tokens, _MARKERS, _MAX_PHRASE_LEN)
    details = _has_details(tokens)

    # 1. Cancellation always resolves to the safe side (see module docstring).
    if "cancel" in found:
        return ConfirmationDecision.NO

    # 2. Negation / change verbs: with details the changes are extracted, without them the bot
    #    asks what to change (05 §5, step 3).
    if "negate" in found or "change" in found:
        return ConfirmationDecision.CHANGE if details else ConfirmationDecision.NO

    # 3. Doubt markers never confirm (03 §5, SPEC §13).  A hedged request that still carries
    #    details ("может быть в 19:00") is treated as a change, not as doubt about the summary.
    if "hedge" in found:
        if "agree" in found or not details:
            return ConfirmationDecision.UNCERTAIN
        return ConfirmationDecision.CHANGE

    # 4. Explicit agreement (03 §5): short, no hedge/negation/change markers.
    if "agree" in found:
        if details:
            return ConfirmationDecision.CHANGE
        if len(tokens) > MAX_YES_WORDS:
            return ConfirmationDecision.UNCERTAIN
        return ConfirmationDecision.YES

    # 5. No decision markers at all: weak acknowledgements ("ок", "ага") and small talk ask for
    #    a written "Да"; a substantive message goes back through understanding as a change.
    return ConfirmationDecision.CHANGE if details else ConfirmationDecision.UNCERTAIN


def _markers(text: str | None) -> tuple[list[str], dict[str, list[str]]]:
    normalized = normalize_text(text)
    tokens = tajik_fold(normalized).split() if normalized else []
    return tokens, scan_phrases(tokens, _MARKERS, _MAX_PHRASE_LEN)


def is_hesitation(text: str | None) -> bool:
    """A reply *to the confirmation question* that is not a decision: doubt ("наверное", "думаю да"),
    a weak acknowledgement ("ок", "👍"), an over-long agreement, or nothing readable.

    ``classify_confirmation`` returns ``UNCERTAIN`` for these *and* for messages about something else
    ("А как оплатить?"). Only a hesitation is answered with "напишите «Да»" right away; any other
    message goes through understanding — which can never confirm an order, so this is safe.
    """
    tokens, found = _markers(text)
    return not tokens or bool({"hedge", "ack", "agree"} & set(found))


def mentions_cancellation(text: str | None) -> bool:
    """True when the message contains a cancellation word ("отмена", "отмените", "бекор").

    At the order summary such a message is a request to cancel, not "what should be changed": the
    dialog passes it to understanding (``CANCEL_ORDER``) instead of answering ``ASK_WHAT_TO_CHANGE``.
    """
    _, found = _markers(text)
    return "cancel" in found


def classify_cancel_confirmation(text: str | None) -> ConfirmationDecision:
    """Answer to "Отменить заказ №N?" (05 §5, step 3, ``awaiting="cancel_confirmation"``).

    Here a cancellation word *agrees* with the question: "да, отмените" / "отмените" / "бекор кунед" are
    ``YES`` — while :func:`classify_confirmation` must read them as ``NO`` at an order summary. A
    cancellation word next to a negation or a doubt ("нет, отмена", "наверное отменить") is
    ``UNCERTAIN`` and the question is repeated. Everything else is classified as a normal confirmation.
    """
    tokens, found = _markers(text)
    if "cancel" in found:
        if "negate" in found or "hedge" in found or len(tokens) > MAX_YES_WORDS:
            return ConfirmationDecision.UNCERTAIN
        return ConfirmationDecision.YES
    return classify_confirmation(text)
