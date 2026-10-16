"""Токенизатор со смещениями в исходной строке.

Каждый токен знает свои границы `start:end` в raw_address. Любое текстовое
поле ответа — это `raw[first.start:last.end]`, поэтому подстрочный инвариант
выполняется по построению.

Слитное написание («66квартал», «Назарбаева8», «11дом», «кв70») разбирается
естественно: буквы и цифры — разные классы символов. Исключение — литера
дома («12а», «3А»): одна буква сразу после цифр, за которой не идёт другая
буква, остаётся частью номера.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .markers import MULTI, SINGLE
from .text import INVISIBLE, fold, has_kazakh_letters

WORD = "word"
NUM = "num"
ORD = "ord"  # порядковое окончание: «-й», «-ші» в «2-й», «4-ші»

_L = r"a-zA-Zа-яА-ЯёЁәғқңөұүһіӘҒҚҢӨҰҮҺІ"
# Литера дома: одна буква (не й/ь/ъ/ы), следом не буква.
_LIT = r"[a-zA-Zа-иА-Ик-щК-ЩэюяЭЮЯәғқңөұүһіӘҒҚҢӨҰҮҺІ]"
_TOKEN = re.compile(
    rf"""
    (?P<abbr>ж/м|ж\.м\.|п/о|с/о|а/о|ж/д)(?![{_L}])
  | (?P<num>\d+(?:-?{_LIT}(?![{_L}]))?(?:\s?/\s?\d+(?:-?{_LIT}(?![{_L}]))?)*)
  | (?P<word>[{_L}]+(?:['’`-][{_L}]+)*)
  | (?P<sep>[,;()\[\]{{}}\n\r\t|]+)
  | (?P<slash>[/\\])
  | (?P<other>\S)
    """,
    re.VERBOSE,
)
_ORD_RE = re.compile(r"^(?:ый|ий|ой|ая|яя|ое|ее|ые|го|й|я|е|шы|ші|ши|сы|сі|ы|і|th|st|nd|rd)$", re.I)


@dataclass(slots=True)
class Token:
    i: int
    start: int
    end: int
    text: str
    kind: str
    seg: int
    f: str = ""
    marker: str | None = None  # категория маркера
    canon: str | None = None  # справочная форма street_type
    marker_len: int = 1  # сколько токенов занимает многословный маркер
    alt: bool = False  # вторая (казахская) половина «рус/қаз»
    glued_prev: bool = False  # нет пробела между этим и предыдущим токеном
    hyphen_prev: bool = False  # перед токеном дефис («Самал-2»)
    dot_after: bool = False
    used: str | None = None  # чем токен «съеден» парсером

    @property
    def is_num(self) -> bool:
        return self.kind == NUM

    @property
    def is_word(self) -> bool:
        return self.kind == WORD


@dataclass(slots=True)
class Tokenized:
    raw: str
    tokens: list[Token] = field(default_factory=list)
    nseg: int = 0

    def span(self, a: Token, b: Token) -> str:
        return self.raw[a.start : b.end]


def _clean_view(raw: str) -> tuple[str, list[int]]:
    """Строка без невидимых символов + карта индексов обратно в raw."""
    chars, idx = [], []
    for i, ch in enumerate(raw):
        if ch in INVISIBLE:
            continue
        cat = unicodedata.category(ch)
        if cat == "Zs" or ch in "   ":
            ch = " "
        elif ch in "‐‑‒–—−":
            ch = "-"
        chars.append(ch)
        idx.append(i)
    idx.append(len(raw))
    return "".join(chars), idx


def tokenize(raw: str) -> Tokenized:
    text, imap = _clean_view(raw or "")
    out = Tokenized(raw=raw or "")
    seg = 0
    prev_end_clean = -1
    pending_hyphen = False
    slash_positions: list[int] = []  # индекс токена, перед которым был «/» между словами
    for m in _TOKEN.finditer(text):
        kind = m.lastgroup
        s, e = m.start(), m.end()
        if kind == "sep":
            seg += 1
            pending_hyphen = False
            prev_end_clean = -1
            continue
        if kind == "slash":
            slash_positions.append(len(out.tokens))
            pending_hyphen = False
            prev_end_clean = -1
            continue
        if kind == "other":
            ch = m.group()
            if ch == "-":
                pending_hyphen = True
            elif ch == "." and out.tokens:
                out.tokens[-1].dot_after = True
            # Прочая пунктуация (кавычки, №, #) разрывает «слитность».
            if ch not in "-":
                prev_end_clean = -1
            continue
        tok_kind = {"num": NUM, "word": WORD, "abbr": WORD}[kind]
        tok_text = m.group()
        if tok_kind == WORD and out.tokens and out.tokens[-1].is_num and _ORD_RE.match(tok_text):
            prev = out.tokens[-1]
            if prev.seg == seg and (prev_end_clean == s or pending_hyphen):
                tok_kind = ORD
        tok = Token(
            i=len(out.tokens),
            start=imap[s],
            end=imap[e - 1] + 1,
            text=raw[imap[s] : imap[e - 1] + 1],
            kind=tok_kind,
            seg=seg,
            f=fold(tok_text),
            glued_prev=prev_end_clean == s,
            hyphen_prev=pending_hyphen,
        )
        out.tokens.append(tok)
        prev_end_clean = e
        pending_hyphen = False
    out.nseg = seg + 1
    _mark_markers(out.tokens)
    _mark_bilingual(out.tokens, slash_positions)
    return out


def _mark_markers(tokens: list[Token]) -> None:
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.kind != WORD:
            i += 1
            continue
        matched = False
        for words, cat, canon in MULTI:
            n = len(words)
            window = tokens[i : i + n]
            if (
                len(window) == n
                and all(w.kind == WORD and w.seg == t.seg for w in window)
                and tuple(w.f for w in window) == words
            ):
                t.marker, t.canon, t.marker_len = cat, canon, n
                for w in window[1:]:
                    w.marker, w.used = "part", "marker"
                i += n
                matched = True
                break
        if matched:
            continue
        hit = SINGLE.get(t.f)
        if hit:
            t.marker, t.canon = hit
        i += 1


_KK_SIDE_WORDS = frozenset(
    fold(w) for w in "қаласы облысы ауданы көшесі ауылы даңғылы шағын ықшам кенті үй пәтер".split()
)


def _is_kazakh_side(tokens: list[Token]) -> bool:
    return any(has_kazakh_letters(t.text) or t.f in _KK_SIDE_WORDS for t in tokens)


def _is_latin_side(tokens: list[Token]) -> bool:
    words = [t for t in tokens if t.is_word]
    return bool(words) and all(re.fullmatch(r"[A-Za-z'’-]+", t.text) for t in words)


def _mark_bilingual(tokens: list[Token], slashes: list[int]) -> None:
    """«город Актобе/Ақтөбе қаласы»: казахскую половину помечаем alt=True.

    Слэш между числами («33/1») сюда не попадает — он внутри числового токена.
    Половина — от начала сегмента до «/» и от «/» до конца сегмента.
    """
    for pos in slashes:
        if pos == 0 or pos >= len(tokens):
            continue
        left_last, right_first = tokens[pos - 1], tokens[pos]
        if left_last.seg != right_first.seg:
            continue
        seg = left_last.seg
        left = [t for t in tokens[:pos] if t.seg == seg]
        right = [t for t in tokens[pos:] if t.seg == seg]
        if not any(t.is_word for t in left) or not any(t.is_word for t in right):
            continue
        lk, rk = _is_kazakh_side(left), _is_kazakh_side(right)
        if rk and not lk:
            side = right
        elif lk and not rk:
            side = left
        elif _is_latin_side(right) and not _is_latin_side(left):
            side = right
        elif _is_latin_side(left) and not _is_latin_side(right):
            side = left
        else:
            continue
        for t in side:
            t.alt = True
        # Половины слэша — разные логические сегменты.
        for t in right:
            t.seg = seg + 0.5  # type: ignore[assignment]
