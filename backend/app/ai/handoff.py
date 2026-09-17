"""Deterministic "call a human" detector (03-business-rules.md §6, 05-ai.md §1).

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

from app.ai.text_normalize import tokenize

__all__ = ["detect_operator_request"]

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
