"""Text normalization shared by the deterministic AI components (05-ai.md §1).

03 §5 prescribes the normalization used before any keyword matching: lower-case, ``ё → е``,
strip emoji and punctuation, collapse whitespace.  The same pipeline is used by the handoff
detector (03 §6), the language heuristic and the product matcher.

``tajik_fold`` maps the six Tajik-specific Cyrillic letters to their Russian look-alikes
(``ҳ→х``, ``қ→к``, ``ғ→г``, ``ӣ→и``, ``ӯ→у``, ``ҷ→ч``) so that the two spellings customers
actually use — ``ҳа``/``ха``, ``тасдиқ мекунам``/``тасдик мекунам``, ``суроға``/``сурога`` —
are one and the same word for matching.  Folding is *not* used for language detection:
``detect_language`` needs the original letters (see ``language.py``).

Everything here is pure Python and allocation-light: a customer message is a few dozen
characters, so a straightforward character scan is both fastest and easiest to audit.
"""

from collections.abc import Iterable

__all__ = [
    "TAJIK_FOLD_MAP",
    "TAJIK_LETTERS",
    "build_phrase_index",
    "contains_tajik_letters",
    "latin_readings",
    "normalize_fold",
    "normalize_text",
    "phrase_key",
    "scan_phrases",
    "tajik_fold",
    "tokenize",
]

# 03 §5: "ё" is folded to "е" so that "всё верно" and "все верно" are the same phrase.
_YO_TABLE = str.maketrans({"ё": "е", "Ё": "Е"})

#: Tajik-specific Cyrillic letters → their Russian look-alikes (used for matching only).
TAJIK_FOLD_MAP: dict[str, str] = {
    "ҳ": "х",
    "қ": "к",
    "ғ": "г",
    "ӣ": "и",
    "ӯ": "у",
    "ҷ": "ч",
}

#: The same letters (both cases) — their presence proves the message is Tajik (05 §5, step 5).
TAJIK_LETTERS: frozenset[str] = frozenset(
    list(TAJIK_FOLD_MAP) + [letter.upper() for letter in TAJIK_FOLD_MAP]
)

_TAJIK_TABLE = str.maketrans(
    {
        **TAJIK_FOLD_MAP,
        **{source.upper(): target.upper() for source, target in TAJIK_FOLD_MAP.items()},
    }
)


def normalize_text(value: str | None) -> str:
    """03 §5 normalization: lower-case, ``ё→е``, drop emoji/punctuation, collapse spaces.

    Digits are kept (``"19:00"`` → ``"19 00"``): the confirmation classifier treats numbers as
    substantive details.  Letters of any alphabet are kept; everything that is neither
    alphanumeric nor whitespace (punctuation, emoji, variation selectors, ZWJ) becomes a space.
    """
    if not value:
        return ""
    text = str(value).lower().translate(_YO_TABLE)
    cleaned = "".join(char if (char.isalnum() or char.isspace()) else " " for char in text)
    return " ".join(cleaned.split())


def tajik_fold(value: str | None) -> str:
    """``ҳ→х``, ``қ→к``, ``ғ→г``, ``ӣ→и``, ``ӯ→у``, ``ҷ→ч`` (case preserved)."""
    if not value:
        return ""
    return str(value).translate(_TAJIK_TABLE)


def normalize_fold(value: str | None) -> str:
    """``normalize_text`` + ``tajik_fold`` — the form every keyword list in this package uses."""
    return tajik_fold(normalize_text(value))


# Tajik and Russian typed in Latin letters ("Garmakak mefisonidmi?", "Pryam garmakak huram", "Salom")
# — the digraphs first. "j" is "ҷ" in Tajik ("kujo" = "куҷо") and "ж" in Russian ("mojno"): both
# readings are produced, see ``latin_readings``.
_LATIN_DIGRAPHS = (
    ("shch", "щ"),
    ("sh", "ш"),
    ("ch", "ч"),
    ("zh", "ж"),
    ("kh", "х"),
    ("ts", "ц"),
    ("ya", "я"),
    ("yu", "ю"),
    ("yo", "е"),
    ("ye", "е"),
)
_LATIN_LETTERS = str.maketrans(
    {
        "a": "а", "b": "б", "c": "с", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и",
        "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р", "s": "с",
        "t": "т", "u": "у", "v": "в", "w": "в", "x": "х", "z": "з",
    }
)  # fmt: skip
_VOWELS = frozenset("aeiouаеиоуыэюя")


def _latin_word(word: str, j: str) -> str:
    for latin, cyrillic in _LATIN_DIGRAPHS:
        word = word.replace(latin, cyrillic)
    # "y": "й" after a vowel ("moy"), "ы" elsewhere ("byl")
    word = "".join(
        ("й" if index and word[index - 1] in _VOWELS else "ы") if char == "y" else char
        for index, char in enumerate(word)
    )
    return word.replace("j", j).translate(_LATIN_LETTERS)


#: English is not transliterated: "…commented on your post" is not «пост» (the Instagram notice in
#: the archive replay, 24.09.2026).
_ENGLISH_WORDS = frozenset(
    {
        # not "do", "on", "to", "in", "be": "do skolki", "chto to", "in chi", "be shakar" are Russian and Tajik
        "the", "this", "that", "you", "your", "of", "is", "are", "was", "will", "because", "until",
        "before", "after", "and", "it", "for", "with", "from", "by", "can", "does", "have", "has",
        "what", "when", "where", "how", "please", "hello", "thanks",
    }
)  # fmt: skip


def latin_readings(value: str | None) -> list[str]:
    """Cyrillic readings of a message typed in Latin letters, folded like ``normalize_fold`` — so the
    owner's Cyrillic keywords match "garmakak" as "гармакак". Empty when there is no Latin word, or
    the Latin words are English, or the text carries a link."""
    text = normalize_fold(value)
    if not any("a" <= char <= "z" for char in text):
        return []
    words = text.split()
    if "http" in text or "www" in words or sum(word in _ENGLISH_WORDS for word in words) >= 2:
        return []
    readings = []
    for j in ("ч", "ж"):
        words = [_latin_word(word, j) if word.isascii() and word.isalpha() else word for word in text.split()]
        readings.append(" ".join(words))
    return list(dict.fromkeys(readings))


def tokenize(value: str | None, *, fold: bool = False) -> list[str]:
    """Words of the normalized message (``fold=True`` also applies :func:`tajik_fold`).

    Token-wise matching is what keeps the classifiers safe from substrings: ``"не"`` never
    matches inside ``"нет проблем"``… and ``"да"`` never matches inside ``"надо"``/``"дату"``.
    """
    return (normalize_fold(value) if fold else normalize_text(value)).split()


def contains_tajik_letters(value: str | None) -> bool:
    """True if the text uses ``ӣ ӯ ҳ қ ғ ҷ`` — decisive evidence of Tajik (05 §5, step 5)."""
    if not value:
        return False
    return any(char in TAJIK_LETTERS for char in str(value))


def phrase_key(phrase: str) -> tuple[str, ...]:
    """Normalize a keyword-list entry into the token tuple used for n-gram matching."""
    return tuple(normalize_fold(phrase).split())


def build_phrase_index(categories: Iterable[tuple[str, Iterable[str]]]) -> tuple[dict[tuple[str, ...], str], int]:
    """``[(category, phrases)]`` → ``({token tuple: category}, longest phrase length)``.

    Later categories win on duplicates, so lists must not overlap; ``max_len`` drives the
    longest-match-first scan (``"не знаю"`` is a hedge even though ``"не"`` alone is a negation).
    """
    index: dict[tuple[str, ...], str] = {}
    longest = 0
    for category, phrases in categories:
        for phrase in phrases:
            key = phrase_key(phrase)
            if not key:
                continue
            index[key] = category
            longest = max(longest, len(key))
    return index, longest


def scan_phrases(
    tokens: list[str],
    index: dict[tuple[str, ...], str],
    max_len: int,
) -> dict[str, list[str]]:
    """Longest-match-first left-to-right scan → ``{category: [matched phrases]}``.

    Matched tokens are consumed, so ``"не знаю"`` (hedge) does not also report ``"не"``
    (negation) and ``"да все верно"`` is one agreement, not three.
    """
    found: dict[str, list[str]] = {}
    position = 0
    total = len(tokens)
    while position < total:
        for size in range(min(max_len, total - position), 0, -1):
            key = tuple(tokens[position : position + size])
            category = index.get(key)
            if category is not None:
                found.setdefault(category, []).append(" ".join(key))
                position += size
                break
        else:
            position += 1
    return found
