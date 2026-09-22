"""Deterministic "call a human" detectors (03-business-rules.md §6, 05-ai.md §1).

Two of them, both at step 2 of 05 §5: :func:`detect_operator_request` — the customer asks for a
human; :func:`detect_our_fault` — the customer reports that we got something wrong, and the owner's
rule is that such a dialog goes to the manager immediately (see that function's own notes).

``DialogService`` calls :func:`detect_operator_request` before anything else (05 §5, step 2):
a match switches the conversation to ``HUMAN_HANDOFF``.  The LLM can request a handoff too
(``Intent.OPERATOR_REQUEST``), so this detector only has to catch the *explicit* wording —
a missed request is picked up by the LLM, while a false positive silently kills the bot for a
customer who merely ordered "торт на 10 человек".  The rules below are therefore conservative.

Rules (03 §6 keywords, made precise):

1. **Role words** — ``оператор``, ``менеджер``/``менеҷер``, ``администратор``, ``админ`` (any
   inflection) are a request on their own, because nobody writes them by accident.
2. **Person words** — ``человек``/``одам`` are ordinary words ("торт на 10 человек", "барои 10
   одам"), so they need a request context: a request verb nearby ("позовите", "хочу
   поговорить", "нужен"), the word "живой"/"зинда", or the preposition "с"/"со"/"бо"
   immediately before ("с человеком", "бо одам").
3. **Blockers** — a possessive right before a role word means the customer is talking *about*
   somebody, not asking for one: "мой менеджер заказывал в прошлый раз" → ``False``.  An
   explicit request verb in the same window overrides the blocker ("позовите вашего
   менеджера" → ``True``).  A negated need ("не надо оператора", "не нужен менеджер") is never
   a request.
"""

from app.ai.text_normalize import build_phrase_index, scan_phrases, tokenize

__all__ = ["detect_operator_request", "detect_our_fault"]

#: How many preceding tokens are inspected for context/blockers.
_CONTEXT_WINDOW = 3

# 1. Role words (03 §6). Prefix matching covers the inflections customers use:
#    оператора/оператору/оператором, менеджера/менеджеру/менеджером, менеҷер → менечер (folded).
_ROLE_PREFIXES = ("оператор", "менеджер", "менечер", "администратор")
#: "админ" is listed explicitly: a prefix would also swallow "административный".
_ADMIN_FORMS = frozenset({"админ", "админа", "админу", "админом", "админе", "админы", "админов", "админку"})

# 2. Person words (03 §6: "человек", "одам") — only with a request context.
_PERSON_PREFIXES = ("человек", "человеч", "одам", "сотрудник")
_ALIVE_PREFIXES = ("жив", "зинда", "настоящ", "реальн")
_WITH_PREPOSITIONS = frozenset({"с", "со", "бо"})
#: "торт на 10 человек", "для 5 человек", "барои 10 одам" — a head count, not a request.
_QUANTITY_PREPOSITIONS = frozenset({"на", "для", "до", "около", "примерно", "барои"})

# 3. Request verbs / need words. Prefix matching keeps the list short.
_REQUEST_PREFIXES = (
    "позов",
    "позва",
    "зовит",
    "вызов",
    "вызв",
    "соедин",
    "свяж",
    "связат",
    "переключ",
    "перевед",
    "переда",
    "передай",
    "пригласи",
    "поговор",
    "пообщат",
    "общат",
    "ответит",
    "напиш",
    "даъват",
)
_NEED_WORDS = frozenset(
    {
        "хочу",
        "хочется",
        "хотел",
        "хотела",
        "хотелось",
        "нужен",
        "нужна",
        "нужно",
        "нужны",
        "надо",
        "можно",
        "прошу",
        "пусть",
        "срочно",
        "дайте",
        "дай",
        "лозим",  # tg: "нужен"
        "зарур",  # tg: "необходим"
        "мехохам",  # tg: "хочу"
    }
)

# Possessives / demonstratives: "мой менеджер", "ваш оператор", "этот администратор".
_POSSESSIVES = frozenset(
    {
        "мой",
        "моя",
        "мое",
        "мои",
        "моего",
        "моему",
        "моим",
        "моей",
        "моих",
        "ваш",
        "ваша",
        "ваше",
        "ваши",
        "вашего",
        "вашему",
        "вашим",
        "вашей",
        "ваших",
        "наш",
        "наша",
        "наше",
        "наши",
        "нашего",
        "нашему",
        "нашим",
        "нашей",
        "его",
        "ее",
        "их",
        "этот",
        "эта",
        "это",
        "эти",
        "этого",
        "тот",
        "та",
        "те",
        "свой",
        "своего",
        "своим",
        "своей",
    }
)


def _is_role_token(token: str) -> bool:
    return token.startswith(_ROLE_PREFIXES) or token in _ADMIN_FORMS


def _is_person_token(token: str) -> bool:
    return token.startswith(_PERSON_PREFIXES)


def _has_request_verb(window: list[str]) -> bool:
    return any(token.startswith(_REQUEST_PREFIXES) or token in _NEED_WORDS for token in window)


def _has_negated_need(window: list[str]) -> bool:
    """"не надо оператора", "не нужен менеджер", "не хочу с человеком" → not a request."""
    if window and window[-1] == "не":
        return True
    return any(
        token == "не" and (window[index + 1] in _NEED_WORDS or window[index + 1].startswith(_REQUEST_PREFIXES))
        for index, token in enumerate(window[:-1])
    )


def _is_blocked(window: list[str]) -> bool:
    if _has_negated_need(window):
        return True
    if _has_request_verb(window):
        # "позовите вашего менеджера" — an explicit verb beats the possessive rule.
        return False
    return bool(window) and window[-1] in _POSSESSIVES


def _is_quantity_context(tokens: list[str], index: int, window: list[str]) -> bool:
    """"на 10 человек", "для 5 человек", "торт человек на 15" — a count, not a request."""
    if window:
        previous = window[-1]
        if any(char.isdigit() for char in previous) or previous in _QUANTITY_PREPOSITIONS:
            return True
    return any(any(char.isdigit() for char in token) for token in tokens[index + 1 : index + 3])


def _has_person_context(tokens: list[str], index: int, window: list[str]) -> bool:
    """A person word only counts inside an explicit request (see rule 2)."""
    if _is_quantity_context(tokens, index, window):
        return False
    if _has_request_verb(window):
        return True
    if any(token.startswith(_ALIVE_PREFIXES) for token in window):
        return True
    if window and window[-1] in _WITH_PREPOSITIONS:
        return True
    # "человек живой", "одами зинда" — the qualifier may follow the noun.
    return any(token.startswith(_ALIVE_PREFIXES) for token in tokens[index + 1 : index + 3])


def detect_operator_request(text: str | None) -> bool:
    """True when the customer explicitly asks for a human (03 §6).  Never raises."""
    tokens = tokenize(text, fold=True)
    for index, token in enumerate(tokens):
        role = _is_role_token(token)
        person = _is_person_token(token)
        if not (role or person):
            continue
        window = tokens[max(0, index - _CONTEXT_WINDOW) : index]
        if _is_blocked(window):
            continue
        if role or _has_person_context(tokens, index, window):
            return True
    return False


# --------------------------------------------------------------------------- our own mistake (03 §6)
#
# The owner's rule (22.09.2026): when something went wrong on the bakery's side, the bot does not
# explain, apologize or negotiate — the manager takes the dialog over at once.  The model can reach
# the same place with ``Intent.COMPLAINT``, and this detector is the deterministic half: without it a
# "вы не тот вкус привезли" can still be answered from the FAQ because a keyword happened to match.
#
# The direction of a mistake is the opposite of the operator detector: handing a working dialog to a
# human costs a little, leaving a real complaint to the bot costs a customer.  Still, the wordings
# below are about something that *has already happened* — a question about the future ("а если не
# привезёте вовремя?") is not a complaint and stays with the bot.

#: A complaint on their own, in any inflection the customers use.
_FAULT_PREFIXES = (
    "перепутал",
    "напутал",
    "опозда",
    "опаздыва",
    "испорт",
    "испорч",
    "черств",
    "засох",
    "подгорел",
    "несвеж",
    "протух",
    "плесен",
    "невкусн",
    "жалоб",
    "жалу",
    "жалов",
    "претензи",
    "хамств",
    "нахамил",
    "нагрубил",
    "ужасн",
    "отвратительн",
    "безобрази",
    "недолож",
    "недовес",
    "обсчитал",
)
#: Exact forms where a prefix would also catch an ordinary request ("задержите до вечера") or a
#: question about trust the FAQ answers ("надеюсь, не обманете").
_FAULT_TOKENS = frozenset(
    {
        "задержали",
        "задержка",
        "задерживается",
        "обманули",
        "обманул",
        "обманула",
        "кинули",
        "воняет",
        "сгорели",
        "сгорел",
        # Khujand Tajik: "не привезли", "не дошло", "не отправили", "испорчено"
        "наовардед",
        "наоварданд",
        "наовард",
        "нарасид",
        "нарасиданд",
        "нафиристодед",
        "надодед",
        "вайроншуда",
        "бемазза",
    }
)
#: Past-tense verbs that turn into a complaint only after "не": "не привезли", "не ответили".
_UNDONE_PAST = frozenset(
    {
        "привезли",
        "привез",
        "привезла",
        "привозили",
        "доставили",
        "доставил",
        "приехал",
        "приехали",
        "приехала",
        "пришел",
        "пришла",
        "пришли",
        "прислали",
        "прислал",
        "получил",
        "получила",
        "получили",
        "дождался",
        "дождалась",
        "ответили",
        "ответил",
        "ответила",
        "отправили",
        "отправил",
        "позвонил",
        "позвонили",
        "передали",
        "положили",
        "оформили",
        "сделали",
    }
)
_FAULT_PHRASES = (
    "не тот",
    "не те",
    "не такие",
    "не хватает",
    "не хватило",
    "не понравилось",
    "не понравились",
    "не понравилась",
    "плохое качество",
    "плохого качества",
    "не свежие",
    "не свежий",
    "до сих пор нет",
    "до сих пор не",
    "так и не",
    "верните деньги",
    "вернуть деньги",
    "верните предоплату",
    "возврат денег",
    "деньги назад",
    "испортили настроение",
    # Khujand Tajik
    "дер кардед",
    "дер карданд",
    "дер омад",
    "дер овардед",
    "дер оварданд",
    "хунук буд",
    "бад буд",
    "нагз набуд",
    "вайрон шуд",
    "вайрон буд",
    "фиреб додед",
    "фиреб кардед",
    "пулро баргардонед",
)
_FAULT_INDEX, _FAULT_MAX_LEN = build_phrase_index([("fault", _FAULT_PHRASES)])
#: "не" may stand one word away from the verb: "мне так и не ответили", "заказ мой не привезли".
_NEGATION_WINDOW = 2


def detect_our_fault(text: str | None) -> bool:
    """True when the customer reports that something went wrong on our side (03 §6).  Never raises."""
    tokens = tokenize(text, fold=True)
    if scan_phrases(tokens, _FAULT_INDEX, _FAULT_MAX_LEN):
        return True
    for index, token in enumerate(tokens):
        if token.startswith(_FAULT_PREFIXES) or token in _FAULT_TOKENS:
            return True
        if token in _UNDONE_PAST and "не" in tokens[max(0, index - _NEGATION_WINDOW) : index]:
            return True
    return False
