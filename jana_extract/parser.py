"""Извлечение полей адреса без нормализации.

Конвейер (каждый шаг «съедает» токены, следующий видит только свободные):

1. страна и почтовый индекс — убираем как шум;
2. номера с маркерами: «кв 70», «239 кв», «11дом», «5 подьезд», «3 этаж»
   (направление маркера выбирается глобально по сумме очков);
3. административные единицы с маркерами: «г. Алматы», «Ақтөбе қаласы»,
   «Ауэзовский р-н», «Актюбинская обл.»;
4. улица / микрорайон / ЖК с маркером, префиксные и постфиксные;
5. ориентир: сегмент от слова-ориентира («магазин», «Qazpost», …);
6. административные единицы без маркеров по справочнику («Алматы, …»);
7. улица без маркера: слова перед номером («Назарбаева8», «Толе би 12»);
8. оставшиеся номера по позиции: дом, затем квартира;
9. справочные поля: province/district и проверка city;
10. confidence.

Все текстовые поля — срезы raw_address по границам токенов.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from . import markers as M
from .reference import Reference, Settlement, get_reference
from .text import core, fold
from .tokenizer import NUM, ORD, WORD, Token, Tokenized, tokenize

FIELDS = (
    "province",
    "city",
    "district",
    "living_area",
    "street_type",
    "street_name",
    "house",
    "apartment",
    "entrance",
    "floor",
    "landmark",
)

# Район по улице/мкр в городах респ. значения выдаётся при доле адресов в одном
# районе не ниже порога. 0.75 выбран по кривой «порог → точность/покрытие»
# (tools/district_study.py, reports/stage2.md): точность важнее полноты.
DISTRICT_SHARE_MIN = 0.75
# Одноимённые НП без различающего контекста: город угадан по крупности.
AMBIGUOUS_SETTLEMENT_PENALTY = 0.85
# Сегмент считается повтором уже найденного города при сходстве не ниже порога.
DUP_CITY_SIMILARITY = 85

_STREET_ONLY_STOP = frozenset({M.CITY, M.SETTLEMENT, M.REGION, M.DISTRICT, M.COUNTRY})


@dataclass
class Comp:
    kind: str  # city|settlement|region|district|street|area|zhk|landmark
    toks: list[Token]
    canon: str | None = None
    marked: bool = True
    alt: bool = False
    conf: float = 0.9

    @property
    def order(self) -> int:
        return self.toks[0].i

    def text(self, tk: Tokenized) -> str:
        return tk.span(self.toks[0], self.toks[-1])


@dataclass
class Result:
    values: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(FIELDS))
    conf: dict[str, float] = field(default_factory=dict)
    spans: dict[str, tuple[int, int]] = field(default_factory=dict)
    leftovers: list[str] = field(default_factory=list)
    confidence: float = 0.0


class Parser:
    def __init__(self, raw: str, ref: Reference) -> None:
        self.raw = raw or ""
        self.ref = ref
        self.tk = tokenize(self.raw)
        self.toks = self.tk.tokens
        self.comps: list[Comp] = []
        self.numbers: dict[str, tuple[Token, float]] = {}  # field -> (token, conf)
        self.region: int | None = None  # предварительная область для проверок по справочнику

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def free(t: Token | None) -> bool:
        return t is not None and t.used is None and t.kind != ORD

    def _next(self, t: Token, skip_ord: bool = True) -> Token | None:
        j = t.i + max(t.marker_len, 1)
        while j < len(self.toks):
            n = self.toks[j]
            if skip_ord and n.kind == ORD:
                j += 1
                continue
            return n if n.seg == t.seg else None
        return None

    def _prev(self, t: Token, skip_ord: bool = True) -> Token | None:
        j = t.i - 1
        while j >= 0:
            p = self.toks[j]
            if skip_ord and p.kind == ORD:
                j -= 1
                continue
            if p.marker == "part":
                j -= 1
                continue
            return p if p.seg == t.seg else None
        return None

    def _with_ord(self, t: Token) -> Token:
        """Номер вместе с порядковым окончанием («2-й») — для названий."""
        j = t.i + 1
        if j < len(self.toks) and self.toks[j].kind == ORD:
            return self.toks[j]
        return t

    def _use(self, toks: list[Token], how: str) -> None:
        for t in toks:
            t.used = how
            o = self._with_ord(t) if t.kind == NUM else t
            if o is not t:
                o.used = how

    @staticmethod
    def _is_house_anchor(t: Token | None) -> bool:
        """Номер дома, уже привязанный маркером, или сам маркер дома («д.», «дом»)."""
        return t is not None and (t.used == "house" or (t.marker == M.HOUSE and t.used == "marker"))

    def _is_plain_word(self, t: Token | None) -> bool:
        return (
            t is not None
            and t.kind == WORD
            and t.used is None
            and t.marker in (None,)
            and not t.alt
        )

    # ------------------------------------------------------------------ steps

    def step_noise(self) -> None:
        for t in self.toks:
            if t.marker == M.COUNTRY and self._country_is_street_name(t):
                t.marker = None  # «ул. Казахстан», «Казахстан көшесі»
            if t.marker == M.COUNTRY:
                t.used = "country"
                if t.marker_len > 1:
                    for x in self.toks[t.i + 1 : t.i + t.marker_len]:
                        x.used = "country"
            elif t.kind == NUM and len(t.text) == 6 and t.text.isdigit():
                t.used = "postcode"

    def _country_is_street_name(self, t: Token) -> bool:
        prev = self.toks[t.i - 1] if t.i > 0 else None
        nxt = self.toks[t.i + t.marker_len] if t.i + t.marker_len < len(self.toks) else None
        prev_street = prev is not None and prev.seg == t.seg and prev.marker in M.STREETISH
        next_street = (
            nxt is not None
            and nxt.seg == t.seg
            and nxt.marker in M.STREETISH
            and nxt.f in M.POSTFIX_LANG
        )
        return prev_street or next_street

    def step_number_markers(self) -> None:
        """Глобальная привязка маркеров номеров к соседним числам."""
        mk = [t for t in self.toks if t.marker in M.NUMBER_MARKERS and t.used is None]
        options: list[list[tuple[Token | None, float]]] = []
        for t in mk:
            opts: list[tuple[Token | None, float]] = [(None, 0.0)]
            nxt, prv = self._next(t), self._prev(t)
            postfix_lang = t.f in M.POSTFIX_LANG
            if nxt is not None and nxt.kind == NUM and self.free(nxt):
                s = 1.5 + (2.0 if nxt.glued_prev else 0.0) + (0.3 if t.dot_after else 0.0)
                if postfix_lang:
                    s -= 0.4
                opts.append((nxt, s))
            if prv is not None and prv.kind == NUM and self.free(prv):
                s = 1.3 + (2.0 if t.glued_prev or t.hyphen_prev else 0.0)
                if postfix_lang:
                    s += 0.4
                # «Нурсити 2 микрорайон»: число сразу после слова — часть названия.
                before = self._prev(prv)
                if before is not None and before.kind == WORD and before.marker is None:
                    s -= 0.5
                opts.append((prv, s))
            options.append(opts)
        if not mk:
            return
        best, best_score = None, -1.0
        # Маркеров в адресе единицы — полный перебор дешевле любой эвристики.
        if len(mk) > 8:
            choice_iter = [tuple(max(o, key=lambda x: x[1]) for o in options)]
        else:
            choice_iter = itertools.product(*options)
        for combo in choice_iter:
            used_ids = [c[0].i for c in combo if c[0] is not None]
            if len(used_ids) != len(set(used_ids)):
                continue
            score = sum(c[1] for c in combo)
            if score > best_score:
                best, best_score = combo, score
        assert best is not None
        field_of = {M.HOUSE: "house", M.APT: "apartment", M.ENTRANCE: "entrance", M.FLOOR: "floor"}
        for t, (num, _s) in zip(mk, best, strict=True):
            if num is None:
                # «д Ивановка», «к Абая»: однобуквенный маркер без числа — не маркер.
                if t.f in M.AMBIGUOUS_SHORT:
                    t.marker = None
                    if t.f in ("д", "с", "п", "ст"):
                        t.marker = M.SETTLEMENT if t.f != "д" else None
                else:
                    t.used = "marker"
                continue
            t.used = "marker"
            fld = field_of.get(t.marker)
            if fld is None:
                self._use([num], "corpus")
                continue
            if fld in self.numbers:
                self._use([num], "dup")
                continue
            self._use([num], fld)
            self.numbers[fld] = (num, 0.97)

    def _word_run(self, start: int, step: int, seg, limit: int = 4) -> list[Token]:
        run: list[Token] = []
        j = start
        while 0 <= j < len(self.toks) and len(run) < limit:
            t = self.toks[j]
            if t.seg != seg:
                break
            if t.marker == "part":
                j += step
                continue
            if t.kind != WORD or t.used is not None or t.marker is not None:
                break
            run.append(t)
            j += step
        return run if step > 0 else run[::-1]

    def step_admin_markers(self) -> None:
        for t in self.toks:
            if t.used is not None or t.marker not in M.ADMIN:
                continue
            short = t.f in M.AMBIGUOUS_SHORT or len(t.f) == 1
            after = self._word_run(t.i + t.marker_len, 1, t.seg, 3)
            before = self._word_run(t.i - 1, -1, t.seg, 3)
            if short and after and not self._looks_like_name(after[0]):
                after = []  # «рядом с мечетью» — не село
            if short and not after and before and t.marker == M.CITY:
                # «Алматы г»: постфиксное «г» — только для известного НП.
                found, _ = self.ref.find_settlement(self.tk.span(before[0], before[-1]), False)
                if not found:
                    before = before[-1:]
                    found, _ = self.ref.find_settlement(before[0].text, False)
                if found:
                    short = False
                    t.f = "қаласы"  # трактуем как постфиксный маркер
            postfix_first = (
                t.f in M.POSTFIX_LANG
                or t.marker in (M.REGION,)
                or (t.marker == M.DISTRICT and before)
            )
            if short:
                cands = [after] if after else []
            elif t.f in M.POSTFIX_LANG:
                # «облысы», «ауданы», «қаласы» стоят ПОСЛЕ названия; следующее за
                # ними слово — уже другой компонент («… облысы Казыгуртский район»).
                cands = [before]
            elif postfix_first:
                cands = [before, after]
            else:
                cands = [after, before]
            cands = [c for c in cands if c]
            if not cands:
                continue
            run = cands[0]
            prefix_dir = run is after
            name = self._best_admin_name(t.marker, run, prefix_dir)
            if not name:
                continue
            t.used = "marker"
            self._use(name, t.marker)
            self.comps.append(Comp(t.marker, name, marked=True, alt=t.alt or name[0].alt))

    def _looks_like_name(self, t: Token) -> bool:
        """Имя собственное с заглавной; если вся строка в нижнем регистре — любое."""
        return t.text[:1].isupper() or self.raw == self.raw.lower()

    def _best_admin_name(self, kind: str, run: list[Token], prefix_dir: bool) -> list[Token]:
        """Самое длинное подходящее по справочнику название из run.

        Для префиксного маркера («г. Алматы Абая 5») берём начало run, для
        постфиксного («Ауэзовский район») — конец."""
        n = len(run)
        for k in range(n, 0, -1):
            part = run[:k] if prefix_dir else run[n - k :]
            text = self.tk.span(part[0], part[-1])
            if kind in (M.CITY, M.SETTLEMENT):
                found, _ = self.ref.find_settlement(text, fuzzy=False)
                if found:
                    return part
            elif kind == M.REGION:
                if self.ref.find_region(text, fuzzy=False):
                    return part
            elif kind == M.DISTRICT and self.ref.find_district(text, fuzzy=False):
                return part
        # Нет в справочнике: при маркере доверяем ему, но берём осторожно.
        if n <= 2 and not self._followed_by_num(run[-1]):
            return run
        return run[:1] if prefix_dir else run[-1:]

    def _followed_by_num(self, t: Token) -> bool:
        n = self._next(t)
        return n is not None and n.kind == NUM

    def step_leading_city(self) -> None:
        """«Алматы Абая 5», «Актобе 11 мкр …»: город в начале строки без запятой."""
        if any(c.kind in (M.CITY, M.SETTLEMENT) for c in self.comps):
            return
        first = next(
            (t for t in self.toks if not t.alt and t.used not in ("country", "postcode")), None
        )
        if first is None or not self.free(first) or first.kind != WORD or first.marker is not None:
            return
        run = self._word_run(first.i, 1, first.seg, 3)
        for k in range(len(run), 0, -1):
            part = run[:k]
            after = self.toks[part[-1].i + 1] if part[-1].i + 1 < len(self.toks) else None
            if after is None or after.seg != first.seg:
                return  # весь сегмент — это обработает step_admin_unmarked
            if after.marker in M.ADMIN:
                continue
            found, _ = self.ref.find_settlement(self.tk.span(part[0], part[-1]), False)
            if found and found[0].rank >= 3:
                self._use(part, M.CITY)
                self.comps.append(Comp(M.CITY, part, marked=False, conf=0.8))
                return

    # -- улицы, микрорайоны, ЖК

    def _has_later_free_num(self, after: Token) -> bool:
        return any(self.free(t) and t.kind == NUM and not t.alt for t in self.toks[after.i + 1 :])

    def _prefix_name(self, m: Token) -> list[Token]:
        name: list[Token] = []
        j = m.i + m.marker_len
        while j < len(self.toks):
            t = self.toks[j]
            if t.seg != m.seg or not self.free(t):
                if t.kind == ORD and name:
                    j += 1
                    continue
                break
            if t.kind == WORD:
                if t.marker is not None and t.marker not in (M.CITY,):
                    # «ул. Город…» маловероятно; любой другой маркер — граница.
                    break
                name.append(t)
            elif t.kind == NUM:
                nxt = self._next(t)
                if (
                    not name
                    or t.hyphen_prev
                    or (nxt is not None and self._is_plain_word(nxt) and nxt.seg == m.seg)
                ):
                    name.append(t)
                    # число без слов: «мкр 5», «12 микрорайон» — дальше не идём,
                    # если за ним не следует слово названия («8 Марта»).
                    if len([x for x in name if x.kind == WORD]) == 0 and not (
                        nxt is not None and self._is_plain_word(nxt)
                    ):
                        j += 1
                        break
                else:
                    break
            j += 1
        return name

    def _postfix_name(self, m: Token) -> list[Token]:
        name: list[Token] = []
        j = m.i - 1
        while j >= 0:
            t = self.toks[j]
            if t.kind == ORD:
                j -= 1
                continue
            if t.seg != m.seg or not self.free(t) or (t.kind == WORD and t.marker is not None):
                break
            name.append(t)
            j -= 1
        name.reverse()
        # Номер дома перед постфиксным маркером («5 Абая улица») не бывает,
        # а вот «Нурсити 2 микрорайон» и «12 микрорайон» — бывают.
        return name[-4:]

    def step_street_markers(self) -> None:
        for m in self.toks:
            if m.used is not None or m.marker not in M.STREETISH:
                continue
            pre = self._prefix_name(m)
            post = self._postfix_name(m)
            pre_words = any(t.kind == WORD for t in pre)
            post_words = any(t.kind == WORD for t in post)
            pre_starts_num = bool(pre) and pre[0].kind == NUM and not pre[0].hyphen_prev
            if (m.f in M.POSTFIX_LANG or pre_starts_num) and post_words:
                # «Нурсат микрорайон 21 …»: число после маркера — дом, не название.
                name = post
            elif m.f in M.POSTFIX_LANG and post:
                name = post
            elif pre_words or (pre and not post_words):
                name = pre
            elif post:
                name = post
            else:
                name = pre
            if not name:
                if m.marker == M.ZHK:
                    m.used = "marker"
                continue
            if name is pre and name[-1].kind == WORD and m.marker in (M.AREA, M.STREET):
                self._maybe_extend_with_number(m, name)
            m.used = "marker"
            self._use(name, m.marker)
            self.comps.append(
                Comp(
                    m.marker, name, canon=m.canon, marked=True, alt=m.alt or name[0].alt, conf=0.95
                )
            )

    def _maybe_extend_with_number(self, m: Token, name: list[Token]) -> None:
        """«мкр Аксай 3а, 59» — 3а часть названия; «ул Абая 5» — 5 это дом."""
        nxt = self._next(name[-1])
        if nxt is None or nxt.kind != NUM or not self.free(nxt):
            return
        key = core(self.tk.span(name[0], nxt))
        house_elsewhere = "house" in self.numbers or self._has_later_free_num(nxt)
        if m.marker == M.AREA:
            known = self.ref.known_area(key, self.region) or self.ref.known_area(key)
            if (known and house_elsewhere) or (house_elsewhere and nxt.seg != self._seg_after(nxt)):
                name.append(nxt)
        elif self.ref.known_street(key, self.region) and house_elsewhere:
            name.append(nxt)

    def _seg_after(self, t: Token):
        n = self.toks[t.i + 1] if t.i + 1 < len(self.toks) else None
        return n.seg if n is not None else None

    # -- ориентиры

    def step_landmarks(self) -> None:
        for m in self.toks:
            if m.used is not None or m.marker != M.LANDMARK or m.alt:
                continue
            span = [m]
            j = m.i + m.marker_len
            if m.f == "ориентир" and j < len(self.toks) and self.toks[j].seg == m.seg:
                # «ориентир: рядом с мечетью» — само слово «ориентир» лишь подпись.
                m.used = "marker"
                span = [self.toks[j]]
                j += 1
            while j < len(self.toks):
                t = self.toks[j]
                if t.seg != m.seg or (t.used is not None and t.marker != "part"):
                    break
                span.append(t)
                j += 1
            if len(span) == 1:
                # Постфиксный ориентир: «Береке дүкені».
                back = self._word_run(m.i - 1, -1, m.seg, 3)
                span = back + span
            self._use(span, "landmark")
            self.comps.append(Comp(M.LANDMARK, span, marked=True, conf=0.8))
            break

    # -- административные единицы без маркеров

    def _segments(self) -> list[list[Token]]:
        segs: dict = {}
        for t in self.toks:
            segs.setdefault(t.seg, []).append(t)
        return list(segs.values())

    def step_admin_unmarked(self) -> None:
        segs = self._segments()
        have_city = any(c.kind in (M.CITY, M.SETTLEMENT) and not c.alt for c in self.comps)
        first_content_seen = False
        for si, seg in enumerate(segs):
            free = [t for t in seg if self.free(t)]
            if not free:
                continue
            words = [t for t in free if t.kind == WORD and t.marker is None]
            if seg[0].alt and all(t.alt for t in free):
                continue
            only_words = len(words) == len(free)
            next_seg = next((s for s in segs[si + 1 :] if any(self.free(t) for t in s)), None)
            next_starts_num = (
                next_seg is not None
                and next((t for t in next_seg if self.free(t)), None) is not None
                and next(t for t in next_seg if self.free(t)).kind == NUM
            )
            if only_words and words:
                text = self.tk.span(words[0], words[-1])
                if self._try_admin(
                    words,
                    text,
                    have_city,
                    allow_fuzzy=not first_content_seen,
                    street_like=next_starts_num and have_city,
                ):
                    have_city = have_city or any(
                        c.kind in (M.CITY, M.SETTLEMENT) for c in self.comps
                    )
                    continue
            elif not first_content_seen and not have_city and words and free[0] is words[0]:
                # «Алматы Абая 5» без запятых: город в начале строки.
                run = []
                for t in free:
                    if t.kind != WORD or t.marker is not None:
                        break
                    run.append(t)
                for k in range(min(len(run) - 1, 3), 0, -1):
                    part = run[:k]
                    found, _ = self.ref.find_settlement(self.tk.span(part[0], part[-1]), False)
                    if found and found[0].rank >= 3:
                        self._use(part, M.CITY)
                        self.comps.append(Comp(M.CITY, part, marked=False, conf=0.8))
                        have_city = True
                        break
            first_content_seen = True

    def _try_admin(self, words, text, have_city, allow_fuzzy, street_like) -> bool:
        reg = self.ref.find_region(text, fuzzy=False)
        # «Алматы» — и регион, и город: для городов респ. значения это city.
        if reg and not self.ref.is_republican_city(reg[0]):
            self._use(words, M.REGION)
            self.comps.append(Comp(M.REGION, words, marked=False, conf=0.85))
            return True
        f = fold(text)
        if f.endswith(("ски", "ский", "ская", "цки", "цкий")) or f.endswith("ий"):
            d = self.ref.find_district(text, fuzzy=False)
            if d:
                self._use(words, M.DISTRICT)
                self.comps.append(Comp(M.DISTRICT, words, marked=False, conf=0.8))
                return True
        if street_like:
            return False
        found, score = self.ref.find_settlement(text, fuzzy=allow_fuzzy)
        if found:
            if have_city:
                city = next(c for c in self.comps if c.kind in (M.CITY, M.SETTLEMENT))
                # Дубль — только повтор ЭТОГО города (в т.ч. с опечаткой). Слово,
                # похожее на какой-то другой НП («Попова», «Ушкара»), — кандидат в улицу.
                if fuzz.ratio(core(city.text(self.tk)), core(text)) >= DUP_CITY_SIMILARITY:
                    self._use(words, "dup")
                    return True
                return False
            self._use(words, M.CITY)
            self.comps.append(Comp(M.CITY, words, marked=False, conf=0.85 * score))
            return True
        return False

    # -- улица без маркера

    def step_street_unmarked(self) -> None:
        if any(c.kind == M.STREET and not c.alt for c in self.comps):
            return
        cands: list[tuple[int, list[Token]]] = []
        for t in self.toks:
            if not self._is_plain_word(t):
                continue
            prv = self.toks[t.i - 1] if t.i > 0 else None
            if prv is not None and prv.seg == t.seg and self._is_plain_word(prv):
                continue  # не начало серии слов
            run = [t]
            j = t.i + 1
            while j < len(self.toks) and len(run) < 4:
                n = self.toks[j]
                if n.seg != t.seg:
                    break
                if self._is_plain_word(n):
                    run.append(n)
                elif n.kind == NUM and self.free(n) and n.hyphen_prev:
                    # «Мамыр-4», «Жетысу-2»: число через дефис — часть названия,
                    # если дальше есть другой номер под дом.
                    if self._has_later_free_num(n):
                        run.append(n)
                    else:
                        break
                else:
                    break
                j += 1
            last = run[-1]
            nxt = self._next(last)
            if nxt is not None and (
                (nxt.kind == NUM and self.free(nxt)) or self._is_house_anchor(nxt)
            ):
                score = 3  # «Назарбаева 8», «Валиханва д. 50/2», «Ушкара 116дом»
            elif nxt is None:
                # слово-сегмент, следом сегмент с номером или с домом по маркеру
                k = last.i + 1
                first_after = self.toks[k] if k < len(self.toks) else None
                while k < len(self.toks) and not self.free(self.toks[k]):
                    k += 1
                after = self.toks[k] if k < len(self.toks) else None
                if after is not None and after.kind == NUM and after.seg != last.seg:
                    score = 2
                elif first_after is not None and self._is_house_anchor(first_after):
                    score = 2  # «Попова, д. 210», «Ушкара, 116дом»
                else:
                    score = 1
            else:
                score = 0
            if score:
                cands.append((score, run))
        if not cands:
            return
        cands.sort(key=lambda c: (-c[0], c[1][0].i))
        score, run = cands[0]
        if score == 1:
            # Только слова без номера: улица, если это не НП и не единственное, что есть.
            text = self.tk.span(run[0], run[-1])
            found, _ = self.ref.find_settlement(text, fuzzy=False)
            if found and not any(c.kind in (M.CITY, M.SETTLEMENT) for c in self.comps):
                self._use(run, M.CITY)
                self.comps.append(Comp(M.CITY, run, marked=False, conf=0.7))
                return
        key = core(self.tk.span(run[0], run[-1]))
        known = self.ref.known_street(key) or self.ref.known_area(key)
        self._use(run, M.STREET)
        conf = 0.85 if known else 0.7
        self.comps.append(Comp(M.STREET, run, canon=None, marked=False, conf=conf))

    # -- номера по позиции

    def step_numbers_positional(self) -> None:
        streetish = [c for c in self.comps if c.kind in (M.STREET, M.AREA) and not c.alt]
        anchor = min((c.toks[-1].i for c in streetish), default=-1)
        free_nums = [t for t in self.toks if self.free(t) and t.kind == NUM and not t.alt]
        after = [t for t in free_nums if t.i > anchor]
        before = [t for t in free_nums if t.i <= anchor]
        order = after + before
        for t in order:
            if "house" not in self.numbers:
                self._use([t], "house")
                self.numbers["house"] = (t, 0.85 if streetish else 0.6)
            elif "apartment" not in self.numbers and t.i > self.numbers["house"][0].i:
                self._use([t], "apartment")
                self.numbers["apartment"] = (t, 0.75)

    # ------------------------------------------------------------------ resolve

    def _pick(self, kind: str) -> Comp | None:
        cs = [c for c in self.comps if c.kind == kind]
        main = [c for c in cs if not c.alt]
        return (main or cs or [None])[0]

    def _resolve_region(self, c: Comp) -> tuple[int, float] | None:
        """«Алматы обл» — это Алматинская область, а не город Алматы."""
        text = c.text(self.tk)
        if c.marked:
            for suffix in (" облысы", " область"):
                hit = self.ref.find_region(text + suffix, fuzzy=False)
                if hit and not self.ref.is_republican_city(hit[0]):
                    return hit
        return self.ref.find_region(text)

    def _prelim_region(self) -> None:
        """Ранняя оценка области — чтобы проверять названия мкр по справочнику."""
        reg = self._pick(M.REGION)
        if reg:
            hit = self._resolve_region(reg)
            if hit:
                self.region = hit[0]
                return
        for c in self.comps:
            if c.kind in (M.CITY, M.SETTLEMENT):
                found, _ = self.ref.find_settlement(c.text(self.tk))
                best = self.ref.best_settlement(found)
                if best:
                    self.region = best.region
                    return

    def resolve(self) -> Result:
        res = Result()
        v, cf = res.values, res.conf

        # --- city
        city_c = self._pick(M.CITY) or self._pick(M.SETTLEMENT)
        cities = [c for c in self.comps if c.kind in (M.CITY, M.SETTLEMENT)]
        if city_c is None and cities:
            city_c = cities[0]
        reg_c = self._pick(M.REGION)
        dis_c = self._pick(M.DISTRICT)

        region_idx: int | None = None
        region_conf = 0.0
        if reg_c:
            hit = self._resolve_region(reg_c)
            if hit:
                region_idx, region_conf = hit[0], 0.95 * hit[1]

        district_idx: int | None = None
        district_conf = 0.0
        district_text: str | None = None
        if dis_c:
            hit = self.ref.find_district(dis_c.text(self.tk), region=region_idx)
            if hit is None:
                hit = self.ref.find_district(dis_c.text(self.tk))
                if hit and region_idx is not None:
                    hit = (hit[0], hit[1] * 0.7)  # район из другой области — сомнительно
            if hit:
                district_idx, district_conf = hit[0], 0.95 * hit[1]
            else:
                district_text = re.sub(r"(?i)\b(район|р-н|ауданы|аудан)\b", "", dis_c.text(self.tk))
                district_text = " ".join(district_text.split()) or None
                district_conf = 0.6

        settlement: Settlement | None = None
        settlement_unique = False
        city_conf = 0.0
        if city_c:
            v["city"] = city_c.text(self.tk)
            res.spans["city"] = (city_c.toks[0].start, city_c.toks[-1].end)
            found, score = self.ref.find_settlement(v["city"], fuzzy=True)
            dis_for_pick = district_idx
            settlement = self.ref.best_settlement(found, region_idx, dis_for_pick)
            if found and settlement is None and region_idx is None:
                # одноимённые НП без контекста: берём самый крупный, но с низким доверием
                settlement = found[0]
                score *= 0.6
            # Однозначно = после учёта области/района из строки остался ровно один
            # НП с таким названием. Перевес по рангу — догадка: район по ней не выводим.
            pool = [
                s
                for s in found
                if (region_idx is None or s.region == region_idx)
                and (district_idx is None or s.district == district_idx)
            ]
            settlement_unique = settlement is not None and len(pool) == 1 and pool[0] is settlement
            if settlement is not None and not settlement_unique:
                score *= AMBIGUOUS_SETTLEMENT_PENALTY
            if settlement is not None:
                city_conf = (0.97 if city_c.marked else 0.88) * score
            else:
                city_conf = 0.7 if city_c.marked else 0.5
            # город-курорт республиканского значения?
            reg_hit = self.ref.find_region(v["city"], fuzzy=False)
            if reg_hit and self.ref.is_republican_city(reg_hit[0]):
                region_idx = region_idx if region_idx is not None else reg_hit[0]
                region_conf = max(region_conf, 0.97)
                settlement = None
        if region_idx is None and settlement is not None:
            region_idx = settlement.region
            region_conf = 0.9 * city_conf
        if region_idx is None and district_idx is not None:
            region_idx = self.ref.districts[district_idx]["region"]
            region_conf = 0.8 * district_conf
        if region_idx is None and v["city"] is None:
            # «вместо города — район»: район города респ. значения
            pass

        # --- street / living area
        streets = [c for c in self.comps if c.kind == M.STREET]
        areas = [c for c in self.comps if c.kind == M.AREA]
        zhks = [c for c in self.comps if c.kind == M.ZHK]

        def main_first(cs):
            return [c for c in cs if not c.alt] + [c for c in cs if c.alt]

        streets, areas, zhks = main_first(streets), main_first(areas), main_first(zhks)
        street_c: Comp | None = None
        living_c: Comp | None = None
        if streets:
            street_c = streets[0]
            living_c = (zhks or areas or [None])[0]
        elif areas:
            if zhks:
                street_c, living_c = areas[0], zhks[0]
            elif len(areas) > 1:
                living_c, street_c = areas[0], areas[1]
            else:
                street_c = areas[0]
        elif zhks:
            living_c = zhks[0]
        if street_c is not None:
            v["street_name"] = street_c.text(self.tk)
            res.spans["street_name"] = (street_c.toks[0].start, street_c.toks[-1].end)
            v["street_type"] = street_c.canon
            cf["street_name"] = street_c.conf
            cf["street_type"] = 0.95 if street_c.canon else 0.6
        if living_c is not None:
            v["living_area"] = living_c.text(self.tk)
            res.spans["living_area"] = (living_c.toks[0].start, living_c.toks[-1].end)
            cf["living_area"] = living_c.conf

        # --- district inference
        if district_idx is None and district_text is None:
            if region_idx is not None and self.ref.is_republican_city(region_idx):
                # Сначала самый локальный объект (мкр / ЖК), затем улица.
                for c in (living_c, street_c):
                    if c is None:
                        continue
                    hit = self.ref.area_or_street_district(region_idx, core(c.text(self.tk)))
                    if hit and hit[1] >= DISTRICT_SHARE_MIN:
                        district_idx, district_conf = hit[0], 0.5 + 0.4 * hit[1]
                        break
            elif settlement is not None and settlement_unique and settlement.district is not None:
                d_name = self.ref.district_name(settlement.district) or ""
                # «Конаев» внутри «Конаев» — это горадминистрация, а не район.
                if core(d_name) != core(settlement.name):
                    district_idx = settlement.district
                    district_conf = 0.8 * max(city_conf, 0.5)
        if district_idx is not None:
            d = self.ref.districts[district_idx]
            if region_idx is None:
                region_idx, region_conf = d["region"], 0.8 * district_conf
            v["district"] = d["name"]
        elif district_text:
            v["district"] = district_text
        if region_idx is not None:
            v["province"] = self.ref.region_name(region_idx)

        cf["city"] = city_conf
        cf["province"] = region_conf
        cf["district"] = district_conf

        # --- numbers
        for fld, (tok, c) in self.numbers.items():
            v[fld] = tok.text
            res.spans[fld] = (tok.start, tok.end)
            cf[fld] = c

        # --- landmark
        lm = self._pick(M.LANDMARK)
        if lm:
            v["landmark"] = lm.text(self.tk)
            res.spans["landmark"] = (lm.toks[0].start, lm.toks[-1].end)
            cf["landmark"] = lm.conf

        res.leftovers = [
            t.text for t in self.toks if t.used is None and t.kind != ORD and not t.alt
        ]
        res.confidence = self._confidence(res)
        return res

    def _confidence(self, res: Result) -> float:
        v, cf = res.values, res.conf
        clean = not res.leftovers

        def fc(name: str, null_ok: float = 0.85) -> float:
            if v[name] is None:
                return null_ok if clean else null_ok - 0.25
            return cf.get(name, 0.7)

        address = [fc("city", 0.4), fc("street_name", 0.4), fc("house", 0.45)]
        details = [
            fc("apartment"),
            fc("entrance", 0.92),
            fc("floor", 0.92),
            fc("landmark", 0.92),
            fc("living_area", 0.9),
        ]
        refs = [fc("province", 0.3), fc("district", 0.6), fc("street_type", 0.6)]
        score = (
            0.60 * sum(address) / len(address)
            + 0.25 * sum(details) / len(details)
            + 0.15 * sum(refs) / len(refs)
        )
        score *= max(0.5, 1.0 - 0.08 * len(res.leftovers))
        return round(min(0.99, max(0.01, score)), 2)

    # ------------------------------------------------------------------ run

    def run(self) -> Result:
        self.step_noise()
        self.step_number_markers()
        self.step_admin_markers()
        self.step_leading_city()
        self._prelim_region()
        self.step_street_markers()
        self.step_landmarks()
        self.step_admin_unmarked()
        if self.region is None:
            self._prelim_region()
        self.step_street_unmarked()
        self.step_numbers_positional()
        return self.resolve()


def parse_address(raw: str, ref: Reference | None = None) -> Result:
    return Parser(raw, ref or get_reference()).run()


def empty_result() -> Result:
    return Result()
