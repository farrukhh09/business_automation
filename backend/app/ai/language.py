"""Russian / Tajik heuristic (05-ai.md §5, step 5; 01-overview §4: ``Language`` = "ru" | "tg").

``DialogService`` needs a language for every reply, including replies produced without the LLM
(templates, guard fallbacks), so detection must work on its own::

    detect_language(text, llm_language=result.language, customer_language=customer.language)

Order of evidence:

1. **Tajik-specific letters** ``ӣ ӯ ҳ қ ғ ҷ`` — decisive, exactly as 05 §5 prescribes (the
   ``Responder`` switches this off with ``decisive_letters=False``: "стоит 200 сомонӣ" is not Tajik).
2. **Word scoring** — many customers type Tajik on a Russian keyboard ("салом, ман як торт
   мехохам"), which step 1 cannot see, and colloquial Tajik drops vowels ("мекнам",
   "мегирм", "навистм"). Distinctly Tajik words score 2, short/ambiguous function words 1, and a
   Tajik verb shape (``ме…ам/ем/ед/ид/анд``, ``на-ме…``) scores 2 even for a word not in any list.
   Northern speech also glues enclitics to the word — the question ``-ми`` ("хастми",
   "мефурушидми"), the possessive ``-аш`` ("тайераш"), ``-ям`` = "тоже" ("шумоям") and the
   locative ``-ба``/``-ва`` ("пагава", "остановкава") — so a token whose stem is a known word
   scores through :func:`_clitic_evidence` instead of having to be listed in every glued form.
   Russian greetings/verbs score 2, Russian function words 1, the letters ``ы щ ц`` (absent from
   the Tajik alphabet) add 1 once. Latin transliteration of either language is recognised by a
   short list of frequent words. Product names and other Russian *business* words ("заказ",
   "доставка", "самовывоз", "торт") are not evidence: Tajik speakers use them as is.
3. **Stickiness** — the winner must lead by :data:`SWITCH_MARGIN`; a weak or mixed message keeps
   the language the dialog already has (the LLM's own ``language`` field first, then the customer's
   stored language). People do not switch language mid-conversation because of one loanword.
"""

import re
from collections.abc import Iterable
from typing import Literal

from app.ai.text_normalize import contains_tajik_letters, normalize_text, tajik_fold

__all__ = ["DEFAULT_LANGUAGE", "SWITCH_MARGIN", "detect_language", "score_languages"]

DEFAULT_LANGUAGE: Literal["ru"] = "ru"
_LANGUAGES = ("ru", "tg")

#: How much one language must lead by before the dialog switches to it.
SWITCH_MARGIN = 2

# Letters that exist in Russian but not in the Tajik Cyrillic alphabet.
_RUSSIAN_ONLY_LETTERS = frozenset("ыщц")

# Distinctly Tajik words (weight 2) — greetings, verbs and nouns that never occur in Russian.
# Listed Tajik-folded: "мехоҳам" → "мехохам", "пагоҳ" → "пагох", "рӯз" → "руз".
_TAJIK_STRONG = frozenset(
    {
        "салом",
        "ассалом",
        "ассалому",
        "алейкум",
        "алекм",
        "рахмат",
        "ташаккур",
        "сипос",
        "мехохам",
        "мехохем",
        "мехохед",
        "мехом",
        "мехоем",
        "мекнам",
        "мекунам",
        "мекунем",
        "мекунед",
        "кунед",
        "мегирам",
        "мегирем",
        "мегирм",
        "гирам",
        "гирем",
        "бигирем",
        "мегиред",
        "навиштам",
        "навистам",
        "навистм",
        "нависед",
        "гуфтам",
        "лозим",
        "даркор",
        "зарур",
        "барои",
        "фардо",
        "пагох",
        "пагохи",
        "имруз",
        "имшаб",
        "бегох",
        "бегохи",
        "пешин",
        "субх",
        "чанд",
        "чан",
        "чандто",
        "нарх",
        "нархаш",
        "арзон",
        "кимат",
        "хаст",
        "хает",
        "нест",
        "шумо",
        "дорад",
        "надорад",
        "доред",
        "дорем",
        "дуруст",
        "мешавад",
        "мешад",
        "меша",
        "мебошад",
        "бошад",
        "бошед",
        "супориш",
        "фармоиш",
        "фармоишро",
        "расонидан",
        "расонед",
        "оваред",
        "биёред",
        "биеред",
        "сурога",
        "сурогаи",
        "телефонам",
        "телефонатон",
        "номам",
        "номатон",
        "бале",
        "мумкин",
        "хуб",
        "хубаст",
        "майлаш",
        "маъкул",
        "ширини",
        "торти",
        "тортро",
        "соат",
        "соати",
        "вакт",
        "вакташ",
        "кучо",
        "чи",
        "чихел",
        "кай",
        "хамин",
        "хамон",
        "ята",
        "якта",
        "дута",
        "сета",
        "чорта",
        "дона",
        "донаги",
        "хона",
        "хонаи",
        "куча",
        "кучаи",
        "хиебон",
        "хиебони",
        "нохия",
        "нохияи",
        "агар",
        "хайр",
        "саломат",
        "фахмидам",
        "нафахмидам",
        "намедонам",
        # Khujand (northern) colloquial forms: "манба" = ба ман, "кати" = бо, "боша" = бошад,
        # "мешава"/"меша" = мешавад, "хозир" = now, "тайёр" = ready, "нагз" = good, "-ми" = a question
        "манба",
        "боша",
        "нагз",
        "хозир",
        "тайёр",
        "тайер",
        "чанта",
        "пул",
        "сум",
        "зуд",
        "истед",
        "гап",
        "хамту",
        "аммо",
        "лекин",
        "факат",
        "хеле",
        "бисёр",
        "бисер",
        "калон",
        "хурд",
        "ака",
        "апа",
        "мешава",
        "мегира",
        "мекуна",
        "мебиёра",
        "мебиера",
        "меоран",
        "меоред",
        "мебиёред",
        "мебиеред",
        "мегирен",
        "мехохен",
        "мекунен",
        "нависен",
        "расонен",
        "расонем",
        "мерасонем",
        "гузаронед",
        "фиристед",
        "фиристам",
        "навиштем",
        "кадом",
        "кадомаш",
        "кадомашро",
        "чиро",
        "руз",
        "рузи",
        # Words taken from this bakery's own Instagram history (21.09.2026): the forms its customers
        # type most often and the heuristic could not score before.
        "баного",  # "ҳозир"/"в наличии": "баного хаст ми?"
        "чандпул",
        "чанпул",
        "нархотон",
        "худам",  # "худам мегирам" = I will pick it up myself
        "худатон",
        "худашам",
        "пага",  # tomorrow (пагоҳ)
        "басфардо",
        "пасфардо",
        "кунид",
        "кунам",
        "шавад",
        "шуд",
        "мешуд",
        "буд",
        "рафта",
        "омада",
        "гирифта",
        "када",
        "таер",
        "метонам",
        "метонем",
        "метонед",
        "метонид",
        "намешад",
        "намуд",
        "намудаш",
        "намудхо",
        "фирсонед",
        "фирсонид",
        "фисонид",
        "мефирсонам",
        "партоид",
        "мепартом",
        "парофтам",
        "гузашт",
        "гузаронд",
        "гузарондам",
        "хонаги",
        "бамаза",
        "макул",
        "якбор",
        "пулаша",
        "пули",
        "асалом",
        "асалому",
        "саломалекум",
        "мебурорем",
        "буроварда",
        "мегирет",
        "мегирид",
        "фахмо",
        "фахмидос",
        "мебахшид",
    }
)

# Short Tajik function words (weight 1): a few of them ("дар", "то", "да", "ай") also exist in Russian.
_TAJIK_WEAK = frozenset(
    {
        "ман",
        "аст",
        "дар",
        "бо",
        "ва",
        "ки",
        "ин",
        "он",
        "аз",
        "ба",
        "то",
        "як",
        "ду",
        "се",
        "хам",
        "мо",
        "ту",
        "ай",
        "да",
        "е",
        "боз",
        "дигар",
        "ха",
        "вай",
        "онхо",
        "ми",  # the northern question particle written apart: "мешава ми?"
        "мана",
        "ку",
        "кати",  # "with" in Khujand ("шоколад кати"); weak, because "Кати" is also a Russian name
        # Tajik-accented spellings of Russian loanwords (21.09.2026, from the bakery's history):
        # evidence of a Tajik speaker, but too close to the Russian word to weigh more.
        "хай",  # ҳай = ҳаст ("доставка хай")
        "каропка",
        "коропка",
        "каробка",
        "асарти",
        "даставка",
        "дастаравон",
        "та",  # "2 та" = two pieces
        "тои",
    }
)

# Russian greetings / verbs / question words (weight 2).
_RUSSIAN_STRONG = frozenset(
    {
        "здравствуйте",
        "здравствуй",
        "привет",
        "добрый",
        "доброе",
        "спасибо",
        "пожалуйста",
        "подскажите",
        "скажите",
        "хочу",
        "хотела",
        "хотел",
        "хотим",
        "можно",
        "сколько",
        "стоит",
        "стоят",
        "цена",
        "цены",
        "когда",
        "где",
        "какие",
        "какой",
        "какая",
        "заказать",
        "закажу",
        "нужен",
        "нужна",
        "нужно",
        "нужны",
        "будет",
        "есть",
        "дайте",
        "завтра",
        "сегодня",
        "послезавтра",
        "вечером",
        "утром",
        "извините",
        "хорошо",
        "ладно",
        "понял",
        "поняла",
        "готовите",
        "делаете",
        "привезите",
        "привезете",
        "заберу",
        "заберем",
        "свежие",
        "свежий",
        "вкусные",
        "вкусный",
    }
)

# Russian function words and business loanwords Tajik speakers use as well (weight 1).
_RUSSIAN_WEAK = frozenset(
    {
        "и",
        "в",
        "на",
        "с",
        "у",
        "к",
        "по",
        "за",
        "из",
        "для",
        "что",
        "как",
        "это",
        "я",
        "мне",
        "меня",
        "мы",
        "вы",
        "вас",
        "вам",
        "они",
        "он",
        "она",
        "нет",
        "же",
        "бы",
        "ли",
        "тоже",
        "очень",
        "еще",
        "там",
        "тут",
        "доставка",
        "доставку",
        "доставки",
        "самовывоз",
        "самовывозом",
        "адрес",
        "заказ",
        "заказа",
        "телефон",
    }
)

# Latin transliteration — a short list of the most frequent words of each language.
_TAJIK_LATIN = frozenset(
    {
        "salom",
        "assalom",
        "assalomu",
        "rahmat",
        "tashakkur",
        "mexoham",
        "mekhoham",
        "mexoxam",
        "mehoham",
        "lozim",
        "fardo",
        "pagoh",
        "chand",
        "narx",
        "hast",
        "nest",
        "shumo",
        "dorad",
        "mumkin",
        "xub",
        "khub",
        "bale",
        "kucho",
        "kay",
        "soat",
        "soati",
        "mekunam",
        "megiram",
        # Latin spellings seen in this bakery's own chats (21.09.2026).
        "asalom",
        "asalomu",
        "aleykum",
        "banogo",
        "taier",
        "nagz",
        "nagzmi",
        "hastmi",
        "nestmi",
        "mefisonid",
        "mefirsoned",
        "garmakak",
        "maylash",
        "majlash",
        "donash",
        "chandpul",
        "chanpul",
    }
)
_RUSSIAN_LATIN = frozenset(
    {
        "privet",
        "zdravstvuyte",
        "zdravstvuite",
        "hochu",
        "xochu",
        "skolko",
        "stoit",
        "spasibo",
        "mozhno",
        "mojno",
        "zavtra",
        "segodnya",
        "zakazat",
        "pozhaluysta",
        "pojaluista",
        "cena",
        "nuzhen",
        "nujen",
        # Latin spellings seen in this bakery's own chats (21.09.2026).
        "horosho",
        "horoso",
        "seychas",
        "seichas",
        "dobroe",
        "utro",
        "vecher",
        "adres",
        "nalichii",
    }
)

# Tajik verb shapes: present "ме-…-ам/ем/ед/анд" ("мехохам", "мегирам", "мешавад"), negated
# "на-ме-…", the colloquial forms with a dropped vowel ("мекнам", "мегирм"), the northern "-ед" →
# "-ен"/"-ид"/"-итон" ("мегирен", "мефурушид", "мекунитон") and "-ад" → "-од"/"-уд" ("мебурод"),
# the "-даги" participle of Khujand speech ("мекадаги" = would like to) and the question particle
# "-ми" glued to a verb ("мешавадми"). Russian words that happen to fit ("медовиком",
# "менеджером", "местам") are excluded explicitly.
_TAJIK_VERB_RE = re.compile(
    r"^(?:на)?ме[а-я]{2,}(?:(?:ам|ем|ед|ен|ид|итон|анд|[аоу]д|[бвгдзклмнпрстфхчш]м)(?:ми)?|[тд]аги)$"
)
# "-таги"/"-даги" on any stem ("хостаги", "гирифтаги", "фурухтаги"): a Russian word needs at least
# three letters before it to fit ("ватаги"), and this keeps those out.
_TAJIK_PARTICIPLE_RE = re.compile(r"^[а-я]{3,}[тд]аги$")
_NOT_TAJIK_VERBS = frozenset(
    {
        "медовиком",
        "медовикам",
        "менеджером",
        "менеджерам",
        "местам",
        "местом",
        "месяцем",
        "месяцам",
        "мешком",
        "мечтам",
        "медом",
        "мелом",
        "меню",
    }
)
# Tajik suffixes on ordinary nouns: object marker "-ро" ("тортро"), plural "-ҳо" → folded "-хо".
_TAJIK_SUFFIXES = ("ро", "хо")
_NOT_TAJIK_SUFFIXED = frozenset(
    {"метро", "ведро", "утро", "бюро", "ядро", "перо", "добро", "серебро"}
    | {"плохо", "тихо", "сухо", "глухо", "эхо", "ухо"}
)
_MIN_SUFFIXED_LENGTH = 5

_STRONG_WEIGHT = 2
_WEAK_WEIGHT = 1
# An enclitic outweighs one business loanword on the other side: "Доставка мекунитонми?" is a Tajik
# question, and "доставка" alone must not hold it at a draw.
_CLITIC_WEIGHT = 3

# Enclitics of northern speech, glued to the word instead of standing apart. "-ми" turns anything
# into a question ("хастми", "мефурушидми"); the others are the possessive "-аш" ("тайераш" = его
# готовность), "-ям" = "тоже" ("шумоям"), and the locative "-ба"/"-ва" ("пагава" = завтра,
# "остановкава" = на остановку) — which customers glue to Russian nouns just as readily.
_QUESTION_CLITIC = "ми"
_TAJIK_CLITICS = ("аш", "ям", "ба", "ва", "ша")
#: Below this the stem is too short to tell a clitic from an ordinary ending ("вами", "наша").
_MIN_CLITIC_HOST = 5


def _is_tajik_verb(token: str) -> bool:
    return bool(_TAJIK_VERB_RE.match(token)) and token not in _NOT_TAJIK_VERBS


def _clitic_evidence(token: str) -> int:
    """Evidence from an enclitic glued to a known stem ("хастми", "тайераш", "остановкава")."""
    if len(token) < _MIN_CLITIC_HOST:
        return 0
    if token.endswith(_QUESTION_CLITIC):
        # "-ми" only after a Tajik word: Russian instrumental plurals end the same way ("ценами").
        stem = token[: -len(_QUESTION_CLITIC)]
        if stem in _TAJIK_STRONG or _is_tajik_verb(stem):
            return _CLITIC_WEIGHT
        if stem in _TAJIK_WEAK:
            return _STRONG_WEIGHT
    for clitic in _TAJIK_CLITICS:
        if not token.endswith(clitic):
            continue
        stem = token[: -len(clitic)]
        if stem in _TAJIK_STRONG or _is_tajik_verb(stem):
            return _CLITIC_WEIGHT
        if stem in _TAJIK_WEAK:
            return _STRONG_WEIGHT
        if stem in _RUSSIAN_STRONG or stem in _RUSSIAN_WEAK:
            # A Russian noun wearing a Tajik ending is the speaker's language showing through.
            return _STRONG_WEIGHT
    return 0


def _tajik_shape(token: str) -> int:
    """Morphological evidence of Tajik for a word that is in no list."""
    clitic = _clitic_evidence(token)
    if clitic:
        return clitic
    if _is_tajik_verb(token) or _TAJIK_PARTICIPLE_RE.match(token):
        return _STRONG_WEIGHT
    if (
        len(token) >= _MIN_SUFFIXED_LENGTH
        and token.endswith(_TAJIK_SUFFIXES)
        and token not in _NOT_TAJIK_SUFFIXED
        and not any(char.isdigit() for char in token)
    ):
        return _WEAK_WEIGHT
    return 0


def score_languages(tokens: list[str]) -> tuple[int, int]:
    """``(tajik, russian)`` evidence for already normalized + Tajik-folded tokens."""
    tajik = russian = 0
    unknown: list[str] = []
    for token in tokens:
        if token in _TAJIK_STRONG or token in _TAJIK_LATIN:
            tajik += _STRONG_WEIGHT
        elif token in _RUSSIAN_STRONG or token in _RUSSIAN_LATIN:
            russian += _STRONG_WEIGHT
        elif token in _TAJIK_WEAK:
            tajik += _WEAK_WEIGHT
        elif token in _RUSSIAN_WEAK:
            russian += _WEAK_WEIGHT
        else:
            tajik += _tajik_shape(token)
            unknown.append(token)
    # "ы щ ц" hint at Russian spelling — one weak point, and only for words that are not known
    # loanwords: "самовывоз" typed by a Tajik speaker is a loanword, "Красный бархат" a product name;
    # neither switches the dialog to Russian on its own.
    if any(letter in token for token in unknown for letter in _RUSSIAN_ONLY_LETTERS):
        russian += _WEAK_WEIGHT
    return tajik, russian


def _coerce(language: object) -> Literal["ru", "tg"] | None:
    """Accept ``Language`` members, plain strings and ``None``; ignore anything else."""
    if language is None:
        return None
    value = str(language).strip().lower()
    return value if value in _LANGUAGES else None  # type: ignore[return-value]


def _without_phrases(tokens: list[str], phrases: Iterable[str]) -> list[str]:
    """Drop every occurrence of the given phrases (whole tokens), longest phrases first."""
    folded = {tuple(tajik_fold(normalize_text(phrase)).split()) for phrase in phrases}
    for phrase in sorted((p for p in folded if p), key=len, reverse=True):
        size = len(phrase)
        kept: list[str] = []
        index = 0
        while index < len(tokens):
            if tuple(tokens[index : index + size]) == phrase:
                index += size
                continue
            kept.append(tokens[index])
            index += 1
        tokens = kept
    return tokens


def detect_language(
    text: str | None,
    llm_language: object = None,
    customer_language: object = DEFAULT_LANGUAGE,
    ignore_phrases: Iterable[str] = (),
    *,
    decisive_letters: bool = True,
) -> Literal["ru", "tg"]:
    """Return ``"ru"`` or ``"tg"`` for a customer message (05 §5, step 5).  Never raises.

    ``ignore_phrases`` — catalog names, aliases and other texts that are not evidence of the
    message language ("Наполеон ва Красный бархат" is Tajik even though the names are Russian).
    ``decisive_letters=False`` scores Tajik letters like any other evidence (one extra point per
    word) instead of deciding at once — for checking a generated reply, where "200 сомонӣ" inside a
    Russian sentence must not pass as Tajik.
    """
    fallback = _coerce(llm_language) or _coerce(customer_language) or DEFAULT_LANGUAGE

    if decisive_letters and contains_tajik_letters(text):
        # 05 §5: ӣ ӯ ҳ қ ғ ҷ are decisive — no Russian word uses them.
        return "tg"

    normalized = normalize_text(text)
    if not normalized:
        return fallback

    raw_tokens = normalized.split()
    tokens = _without_phrases([tajik_fold(token) for token in raw_tokens], ignore_phrases)
    tajik, russian = score_languages(tokens)
    if not decisive_letters:
        kept = set(tokens)
        tajik += sum(1 for token in raw_tokens if contains_tajik_letters(token) and tajik_fold(token) in kept)

    if tajik - russian >= SWITCH_MARGIN:
        return "tg"
    if russian - tajik >= SWITCH_MARGIN:
        return "ru"
    # Weak or mixed evidence — a short message, a product name, a loanword: keep the dialog's language
    # (the LLM hint first, then the customer profile).
    return fallback
