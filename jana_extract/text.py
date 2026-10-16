"""Нормализация строк ТОЛЬКО для сравнения со справочником.

Результат `fold()` никогда не попадает в ответ: в ответ идут срезы исходной
строки. Здесь мы сознательно «прощаем» регистр, ё/е, казахские буквы,
мягкий/твёрдый знак, дефисы и порядковые окончания, чтобы найти запись
справочника. Сам ответ остаётся ровно таким, как в raw_address.
"""

from __future__ import annotations

import re
import unicodedata

# Казахские буквы → ближайшие русские. Нужна только для поиска:
# «Ақтөбе» и «Актобе», «Әуезов» и «Ауэзов» попадают в один ключ.
_KK_FOLD = str.maketrans(
    {
        "ә": "а",
        "ғ": "г",
        "қ": "к",
        "ң": "н",
        "ө": "о",
        "ұ": "у",
        "ү": "у",
        "һ": "х",
        "і": "и",
        "ё": "е",
        "э": "е",
        "ъ": "",
        "ь": "",
        # Латинские двойники кириллических букв (частая «опечатка» раскладки).
        "a": "а",
        "c": "с",
        "e": "е",
        "o": "о",
        "p": "р",
        "x": "х",
        "y": "у",
        "k": "к",
        "m": "м",
        "t": "т",
        "b": "в",
        "h": "н",
    }
)

# Символы, которые не видны глазу и не должны влиять ни на токенизацию,
# ни на сравнение: zero-width, soft hyphen, BOM, bidi-метки.
INVISIBLE = frozenset("​‌‍‎‏⁠⁡⁢⁣⁤﻿­᠎‪‫‬‭‮⁦⁧⁨⁩")

_ORDINAL = re.compile(
    r"(\d+)\s*-?\s*(?:ый|ий|ой|ая|яя|ое|ее|ые|го|му|ым|ом|й|я|е|шы|ші|ши|сы|си|ы|и|th|st|nd|rd)(?![а-яa-z])"
)
_NON_WORD = re.compile(r"[^0-9a-zа-я]+")
_DIGIT_SPACE_LETTER = re.compile(r"(\d)\s+([а-я])(?![а-я])")
_LETTER_DIGIT = re.compile(r"([а-я])(\d)")


def strip_invisible(s: str) -> str:
    return "".join(ch for ch in s if ch not in INVISIBLE)


def _is_latin_word(s: str) -> bool:
    return any("a" <= ch <= "z" for ch in s) and not any("а" <= ch <= "я" for ch in s)


def fold(s: str | None) -> str:
    """Ключ для сравнения: нижний регистр, без пунктуации и диакритики."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", strip_invisible(s)).lower()
    # Чисто латинские слова («flat», «Almaty») не транслитерируем посимвольно:
    # двойники a/c/e/o/... превращают их в кашу. Сворачиваем только кириллицу.
    parts = []
    for word in s.split():
        parts.append(word if _is_latin_word(word) else word.translate(_KK_FOLD))
    s = " ".join(parts)
    s = _NON_WORD.sub(" ", s)
    s = _ORDINAL.sub(r"\1", s)
    s = _DIGIT_SPACE_LETTER.sub(r"\1\2", s)
    s = _LETTER_DIGIT.sub(r"\1 \2", s)
    return " ".join(s.split())


# Слова-типы, которые убираются из названия при построении ключа справочника.
# «улица Абая», «Абая улица», «ул. Абая» → «абая».
TYPE_WORDS = frozenset(
    fold(w)
    for w in (
        "улица ул у көшесі көш кошеси street str st "
        "проспект пр просп пр-т даңғылы даңғ avenue ave "
        "переулок пер тұйық тупик бульвар б-р бул шоссе тракт трасса "
        "набережная наб площадь пл проезд аллея линия "
        "микрорайон мкр мкрн м-н шағын ықшам аудан ауданы шагын "
        "жилой массив ж/м жм жилмассив тұрғын алабы "
        "квартал кв-л жк комплекс "
        "район р-н город г қаласы село с аул ауылы ауыл поселок пос п кенті кент "
        "посёлок станция ст сельский округ"
    ).split()
)


def core(name: str | None) -> str:
    """Ключ названия без слов-типов: «микрорайон Аксай-3А» → «аксай 3а»."""
    words = [w for w in fold(name).split() if w not in TYPE_WORDS]
    return " ".join(words)


KAZAKH_LETTERS = frozenset("әғқңөұүһіӘҒҚҢӨҰҮҺІ")


def has_kazakh_letters(s: str) -> bool:
    return any(ch in KAZAKH_LETTERS for ch in s)
