"""Синтетический корпус для диагностики: реальные названия из справочника +
«грязные» паттерны из ТЗ. Эталон строится вместе со строкой, поэтому он точен
по построению (в рамках наших правил трактовки полей).

    python tools/synth.py --n 2000 --out tests/fixtures/synth_corpus.json

Это НЕ замена скрытого набора жюри: распределение паттернов задано нами.
Корпус нужен, чтобы находить классы ошибок и мерить эффект правок.

Формат записи: {"id", "raw", "expect": {11 полей}, "tags": [...]}.
Названия улиц и мкр в справочнике хранятся fold-ключами (без «ь», ё→е), здесь
им возвращается заглавная буква; «ь» не восстанавливается.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jana_extract.markers import SINGLE  # noqa: E402
from jana_extract.parser import FIELDS  # noqa: E402
from jana_extract.reference import Reference  # noqa: E402
from jana_extract.text import core, fold, has_kazakh_letters  # noqa: E402

REPUBLICAN = ("Алматы", "Астана", "Шымкент")
INVIS = ["​", " ", "­", "﻿"]
LANDMARKS = [
    "магазин Береке",
    "магазин береке",
    "Qazpost Солнечный",
    "напротив ТЦ Мега",
    "возле школы 45",
    "рядом с аптекой",
    "остановка Рахат",
    "напротив рынка",
    "кафе Достар",
    "за мечетью",
    "ТРЦ Керуен",
    "возле Magnum",
]
LANDMARK_LABELS = ["", "", "", "ориентир: "]
TYPO_SUBST = {"а": "о", "о": "а", "ы": "и", "и": "ы", "е": "и", "у": "ю", "з": "с", "с": "з"}


class Ref:
    def __init__(self, path: Path) -> None:
        r = json.load(gzip.open(path, "rt", encoding="utf-8"))
        for i, s in enumerate(r["settlements"]):
            s["_idx"] = i
        self.jref = Reference.load(path)
        self.regions = r["regions"]
        self.districts = r["districts"]
        bad = re.compile(r"администрац|әкімдіг|\d", re.I)
        # «Село имени …», «Посёлок …»: тип внутри названия — эталон city неоднозначен.
        typed = re.compile(r"^(село|посёлок|поселок|аул|станция|город)\b", re.I)
        bad_name = lambda n: bool(bad.search(n) or typed.search(n))  # noqa: E731
        self.cities = [s for s in r["settlements"] if s["rank"] >= 3 and not bad_name(s["name"])]
        self.villages = [
            s
            for s in r["settlements"]
            if s["rank"] <= 2
            and s["district"] is not None
            and not bad_name(s["name"])
            and len(s["name"]) >= 4
        ]
        self.region_streets = {
            int(k): [x for x in v if self._good_name(x)] for k, v in r["region_streets"].items()
        }
        self.city_streets = r["city_streets"]
        self.city_areas = r["city_areas"]
        self.area_names = [k for k, c in r["area_names"].items() if c >= 3 and self._good_area(k)]
        # Мкр/ЖК своей области: в реальных адресах мкр относится к своему городу.
        self.region_areas = {
            int(k): [x for x in v if self._good_area(x)] for k, v in r["region_areas"].items()
        }

    @staticmethod
    def _good_name(k: str) -> bool:
        words = k.split()
        if not 1 <= len(words) <= 3 or len(k) < 4:
            return False
        if not re.fullmatch(r"[а-я ]+", k):
            return False
        return not any(w in SINGLE for w in words)

    @staticmethod
    def _good_area(k: str) -> bool:
        words = k.split()
        return (
            1 <= len(words) <= 3
            and re.fullmatch(r"[а-я]+( [а-я]+)?( \d+[а-я]?)?", k) is not None
            and not any(w in SINGLE for w in words)
        )

    def district_name(self, idx):
        return None if idx is None else self.districts[idx]["name"]

    def counts(self, city: str, key: str) -> dict[str, int] | None:
        merged: dict[str, int] = {}
        for table in (self.city_areas, self.city_streets):
            for d, c in table.get(city, {}).get(key, {}).items():
                merged[d] = merged.get(d, 0) + c
        return merged or None

    def unambiguous_district(self, settlement: dict, region_ctx: int | None) -> str | None:
        """Район НП, если «НП → район» однозначно по справочнику и контексту строки.

        Неоднозначно: больше одного одноимённого НП, не различимых по области из
        строки; НП — город областного значения (вне районов); «район» совпадает
        с названием самого города (горадминистрация, как «Конаев»)."""
        if settlement.get("district") is None:
            return None
        found, _ = self.jref.find_settlement(settlement["name"], fuzzy=False)
        pool = [s for s in found if region_ctx is None or s.region == region_ctx]
        # Однозначно = ровно один НП с таким названием после учёта контекста строки.
        # Перевес по рангу («самая крупная Алексеевка») однозначностью не считается.
        if len(pool) != 1 or pool[0].idx != settlement["_idx"]:
            return None
        name = self.districts[settlement["district"]]["name"]
        if fold(name) == fold(settlement["name"]):
            return None
        return name


def title(k: str) -> str:
    return " ".join(w if w[:1].isdigit() else w[:1].upper() + w[1:] for w in k.split())


def typo(rnd: random.Random, s: str) -> str:
    idx = [i for i, ch in enumerate(s) if ch.lower() in TYPO_SUBST and 0 < i < len(s) - 1]
    if not idx:
        return s
    i = rnd.choice(idx)
    ch = TYPO_SUBST[s[i].lower()]
    return s[:i] + (ch.upper() if s[i].isupper() else ch) + s[i + 1 :]


def kk_variant(settlement: dict) -> str | None:
    for v in settlement["variants"]:
        if has_kazakh_letters(v) and fold(v) == fold(settlement["name"]):
            return v
    for v in settlement["variants"]:
        if has_kazakh_letters(v) and len(v.split()) == 1:
            return v
    return None


def gen_record(rnd: random.Random, ref: Ref, i: int) -> dict:
    gold: dict = dict.fromkeys(FIELDS)
    tags: list[str] = []
    parts: list[str] = []  # части адреса в порядке следования
    pre: list[str] = []

    # ---------------------------------------------------------------- местность
    mkr_prefix = False
    kind = rnd.choices(["rep", "city", "village"], [0.40, 0.40, 0.20])[0]
    if kind == "rep":
        rname = rnd.choice(REPUBLICAN)
        reg_idx = next(i for i, r in enumerate(ref.regions) if r["name"] == rname)
        settlement = next(s for s in ref.cities if s["name"] == rname)
    elif kind == "city":
        settlement = rnd.choice(
            [s for s in ref.cities if ref.regions[s["region"]]["kind"] == "oblast"]
        )
        reg_idx = settlement["region"]
    else:
        settlement = rnd.choice(ref.villages)
        reg_idx = settlement["region"]
    region = ref.regions[reg_idx]
    gold["province"] = region["name"]
    city = settlement["name"]

    if rnd.random() < 0.05:
        pre.append(rnd.choice(["050000", "100000", "Республика Казахстан", "Казахстан", "РК"]))
        tags.append("noise_prefix")

    if kind == "village" or (kind == "city" and rnd.random() < 0.25):
        stem = region["name"].replace(" область", "")
        kk = next((v for v in region["variants"] if v.endswith("облысы")), None)
        form = rnd.choice([f"{stem} обл.", f"{stem} область", f"{stem} обл"] + ([kk] if kk else []))
        parts.append(form)
        tags.append("region_in_text")

    district_idx = settlement.get("district") if kind == "village" else None
    if kind == "village" and district_idx is not None and rnd.random() < 0.7:
        d = ref.districts[district_idx]
        kk = next((v for v in d["variants"] if has_kazakh_letters(v)), None)
        form = rnd.choice(
            [f"{d['name']} р-н", f"{d['name']} район"] + ([f"{kk} ауданы"] if kk else [])
        )
        parts.append(form)
        tags.append("district_in_text")
    district_from_text = "district_in_text" in tags
    if district_from_text:
        gold["district"] = ref.district_name(district_idx)

    # город
    city_text = city
    if kind != "village" and len(city) >= 5 and rnd.random() < 0.05:
        city_text = typo(rnd, city)
        tags.append("typo_city")
    kk_city = kk_variant(settlement)
    city_omitted = False
    district_instead = kind == "rep" and rnd.random() < 0.05
    if district_instead:
        city_omitted = True
        tags.append("district_instead_of_city")
    elif kind == "village":
        form = rnd.choice(
            ["с. {}", "село {}", "п. {}", "пос. {}", "{}"] + (["{} ауылы"] if kk_city else [])
        )
        if form == "{} ауылы":
            city_text = kk_city
            tags.append("kazakh_city")
        parts.append(form.format(city_text))
        tags.append("village")
    else:
        r = rnd.random()
        if r < 0.20 and kk_city and city_text == city:
            sep = rnd.choice(["/", " / "])
            parts.append(f"город {city_text}{sep}{kk_city} қаласы")
            tags.append("bilingual_city")
        elif r < 0.27 and kk_city and city_text == city:
            city_text = kk_city
            parts.append(f"{kk_city} қаласы")
            tags.append("kazakh_city")
        elif r < 0.45:
            parts.append(f"г. {city_text}")
        elif r < 0.55:
            parts.append(f"г.{city_text}")
        elif r < 0.62:
            parts.append(f"город {city_text}")
        elif r < 0.66:
            parts.append(f"{city_text} г")
        else:
            parts.append(city_text)
        if rnd.random() < 0.04:
            parts.append(rnd.choice([f"г. {city_text}", city_text]))
            tags.append("city_duplicated")
    gold["city"] = None if city_omitted else city_text
    if kind != "rep" and not district_from_text:
        # Конвенция 1: район НП из справочника, если соответствие однозначно
        # с учётом того, что есть в строке (область). Иначе null.
        gold["district"] = ref.unambiguous_district(
            settlement, reg_idx if "region_in_text" in tags else None
        )

    # ---------------------------------------------------------------- улица
    pool = ref.region_streets.get(reg_idx) or ref.region_streets[next(iter(ref.region_streets))]
    city_streets = ref.city_streets.get(region["name"]) if kind == "rep" else None
    city_areas = ref.city_areas.get(region["name"]) if kind == "rep" else None

    def pick_street():
        if city_streets and rnd.random() < 0.8:
            cand = [k for k in rnd.sample(sorted(city_streets), 30) if Ref._good_name(k)]
            if cand:
                k = cand[0]
                return k, city_streets[k]
        return rnd.choice(pool), None

    def pick_area():
        if city_areas and rnd.random() < 0.8:
            cand = [k for k in rnd.sample(sorted(city_areas), 40) if Ref._good_area(k)]
            if cand:
                return cand[0], city_areas[cand[0]]
        own = ref.region_areas.get(reg_idx)
        return (rnd.choice(own) if own else rnd.choice(ref.area_names)), None

    def majority(counts):
        """«Истинный» район конкретного адреса: выбирается пропорционально числу
        адресов улицы/мкр в каждом районе. Так точность вывода района по
        большинству честно падает на улицах, которые делят несколько районов."""
        if not counts:
            return None
        ds = sorted(counts)
        d = rnd.choices(ds, [counts[k] for k in ds])[0]
        return ref.districts[int(d)]["name"]

    street_key, _street_counts = pick_street()
    use_street_key = False
    street_name = title(street_key)
    if rnd.random() < 0.05:
        street_name = typo(rnd, street_name)
        tags.append("typo_street")

    house = rnd.choices(
        [
            str(rnd.randint(1, 250)),
            f"{rnd.randint(1, 120)}{rnd.choice('абвгАБ')}",
            f"{rnd.randint(1, 99)}/{rnd.randint(1, 9)}",
            "1/2",
        ],
        [0.6, 0.18, 0.18, 0.04],
    )[0]
    if "/" in house:
        tags.append("house_fraction")
    elif house[-1].isalpha():
        tags.append("house_litera")

    style = rnd.choices(
        [
            "typed",
            "typed_post",
            "kk_typed",
            "plain",
            "glued",
            "mkr_named",
            "mkr_number",
            "kvartal_glued",
            "mkr_plus_street",
            "zhk_plus_street",
            "mkr_postfix_glued",
        ],
        [0.26, 0.05, 0.06, 0.12, 0.06, 0.12, 0.08, 0.03, 0.10, 0.05, 0.07],
    )[0]
    tags.append(f"street:{style}")
    house_done = False
    area_key: str | None = None  # самый локальный объект: мкр / ЖМ / ЖК / квартал
    merged_into_mkr = False
    if style == "typed":
        t, form = rnd.choice(
            [
                ("улица", "ул. {}"),
                ("улица", "ул.{}"),
                ("улица", "улица {}"),
                ("улица", "ул {}"),
                ("проспект", "пр. {}"),
                ("проспект", "проспект {}"),
                ("проспект", "пр-т {}"),
                ("переулок", "пер. {}"),
            ]
        )
        parts.append(form.format(street_name))
        gold.update(street_type=t, street_name=street_name)
        use_street_key = True
    elif style == "typed_post":
        t, form = rnd.choice(
            [("улица", "{} ул."), ("улица", "{} улица"), ("проспект", "{} проспект")]
        )
        parts.append(form.format(street_name))
        gold.update(street_type=t, street_name=street_name)
        use_street_key = True
    elif style == "kk_typed":
        t, form = rnd.choice([("улица", "{} көшесі"), ("проспект", "{} даңғылы")])
        parts.append(form.format(street_name))
        gold.update(street_type=t, street_name=street_name)
        use_street_key = True
    elif style == "plain":
        parts.append(street_name)
        gold.update(street_name=street_name)
        use_street_key = True
    elif style == "glued":
        parts.append(f"{street_name}{house}")
        gold.update(street_name=street_name, house=house)
        use_street_key = True
        house_done = True
        tags.append("glued_street_house")
    elif style == "mkr_named":
        ak, _acounts = pick_area()
        an = title(ak)
        form = rnd.choice(["мкр {}", "мкр. {}", "микрорайон {}", "мкрн {}", "{} мкр"])
        mkr_prefix = not form.startswith("{}")
        parts.append(form.format(an))
        gold.update(street_type="микрорайон", street_name=an)
        area_key = ak
    elif style == "mkr_number":
        n = str(rnd.randint(1, 32))
        form = rnd.choice(["{} мкр", "мкр {}", "мкр. {}", "{} микрорайон", "{}-й микрорайон"])
        text = form.format(n)
        parts.append(text)
        gold.update(street_type="микрорайон", street_name=n)
        area_key = n
        tags.append("mkr_number_name")
    elif style == "kvartal_glued":
        n = str(rnd.randint(1, 90))
        parts.append(f"{n}квартал")
        gold.update(street_type="квартал", street_name=n)
        area_key = n
        tags.append("glued_kvartal")
    elif style == "mkr_plus_street":
        ak, _acounts = pick_area()
        an = title(ak)
        parts.append(rnd.choice(["мкр {}", "мкр. {}", "микрорайон {}"]).format(an))
        t, form = rnd.choice([("улица", "ул. {}"), ("улица", "улица {}"), ("проспект", "пр. {}")])
        parts.append(form.format(street_name))
        gold.update(living_area=an, street_type=t, street_name=street_name)
        area_key, use_street_key = ak, True
    elif style == "zhk_plus_street":
        own = ref.region_areas.get(reg_idx)
        zk = title(rnd.choice(own) if own else rnd.choice(ref.area_names))
        parts.append(rnd.choice(["ЖК {}", 'ЖК "{}"', "жк {}"]).format(zk))
        t, form = rnd.choice([("улица", "ул. {}"), ("проспект", "пр. {}"), (None, "{}")])
        parts.append(form.format(street_name))
        gold.update(living_area=zk, street_type=t, street_name=street_name)
        area_key, use_street_key = core(zk), True
        tags.append("zhk")
    elif style == "mkr_postfix_glued":
        ak, _acounts = pick_area()
        an = title(ak)
        ent = str(rnd.randint(1, 9))
        apt = str(rnd.randint(1, 300))
        h = str(rnd.randint(1, 60))
        parts.append(f"{an} микрорайон {h}дом {ent} {rnd.choice(['подьезд', 'подъезд'])} {apt} кв")
        gold.update(street_type="микрорайон", street_name=an, house=h, entrance=ent, apartment=apt)
        area_key = ak
        house_done = True
        tags.append("spec_nursity_like")

    # ---------------------------------------------------------------- номера
    if not house_done:
        form = rnd.choices(
            ["{}", "д. {}", "дом {}", "д.{}", "{}дом", "үй {}"], [0.5, 0.2, 0.1, 0.1, 0.06, 0.04]
        )[0]
        if form == "{}дом":
            tags.append("glued_house_marker")
        if (
            form == "{}"
            and parts
            and rnd.random() < 0.5
            and style not in ("mkr_number", "kvartal_glued")
        ):
            parts[-1] = f"{parts[-1]} {house}"  # «ул. Абая 5» в одном сегменте
            tags.append("house_in_street_segment")
            merged_into_mkr = style == "mkr_named" and mkr_prefix
        else:
            parts.append(form.format(house))
        gold["house"] = house
        if gold["apartment"] is None and rnd.random() < 0.7:
            apt = str(rnd.randint(1, 350))
            form = rnd.choices(
                [
                    "кв {}",
                    "кв. {}",
                    "кв{}",
                    "{} кв",
                    "flat {}",
                    "kv {}",
                    "apt {}",
                    "{} пәтер",
                    "{}",
                ],
                [0.3, 0.2, 0.08, 0.08, 0.06, 0.08, 0.04, 0.06, 0.1],
            )[0]
            if form in ("flat {}", "kv {}", "apt {}"):
                tags.append("latin_apartment")
            if form == "{}":
                tags.append("apartment_unmarked")
            parts.append(form.format(apt))
            gold["apartment"] = apt
            if merged_into_mkr and form == "{}":
                # Конвенция 4 («мкр Аксай 3а, 59», как в эталоне организаторов):
                # число после маркера мкр — часть названия, следующее число — дом.
                gold["street_name"] = f"{gold['street_name']} {house}"
                gold["house"], gold["apartment"] = apt, None
                tags.append("mkr_number_then_house")
        if gold["entrance"] is None and rnd.random() < 0.15:
            e = str(rnd.randint(1, 12))
            form = rnd.choice(["{}подьезд", "подъезд {}", "{} подъезд", "{} подьезд", "под. {}"])
            parts.append(form.format(e))
            gold["entrance"] = e
            tags.append("entrance")
        if rnd.random() < 0.12:
            fl = str(rnd.randint(1, 20))
            parts.append(rnd.choice(["{} этаж", "этаж {}", "эт. {}", "{} қабат"]).format(fl))
            gold["floor"] = fl
            tags.append("floor")

    if rnd.random() < 0.08:
        lm = rnd.choice(LANDMARKS)
        parts.append(rnd.choice(LANDMARK_LABELS) + lm)
        gold["landmark"] = lm
        tags.append("landmark")

    if kind == "rep" and not district_from_text:
        # Истинный район адреса — из распределения самого локального объекта:
        # сначала мкр/ЖМ/ЖК, затем улица (та же объединённая таблица, что в справочнике).
        for key in (area_key, street_key if use_street_key else None):
            counts = ref.counts(region["name"], key) if key else None
            if counts:
                gold["district"] = majority(counts)
                break
    if district_instead:
        d_name = gold["district"] or rnd.choice(
            [d["name"] for d in ref.districts if d["city"] == region["name"]]
        )
        gold["district"] = d_name
        parts.insert(0, rnd.choice([f"{d_name} район", f"{d_name} р-н"]))

    # ---------------------------------------------------------------- сборка строки
    if rnd.random() < 0.05 and not city_omitted and kind != "village":
        # город в конце: «мкр Орбита-1, д.21, кв.5, Алматы»
        city_part = next((p for p in parts if city_text in p), None)
        if city_part and "/" not in city_part:
            parts.remove(city_part)
            parts.append(city_part)
            tags.append("city_last")
    parts = pre + parts
    sep = ", "
    if rnd.random() < 0.10:
        sep = " "
        tags.append("no_commas")
    elif rnd.random() < 0.15:
        sep = ","
    raw, joints = "", []
    for k, part in enumerate(parts):
        if k:
            raw += sep
            joints.append(len(raw))
        raw += part
    if rnd.random() < 0.05:
        raw = raw.lower()
        gold = {
            k: (
                v.lower()
                if isinstance(v, str) and k not in ("province", "district", "street_type")
                else v
            )
            for k, v in gold.items()
        }
        tags.append("lowercase")
    if rnd.random() < 0.06:
        # невидимый символ — только на границе частей, чтобы эталон оставался подстрокой
        p = rnd.choice(joints or [0])
        raw = raw[:p] + rnd.choice(INVIS) + raw[p:]
        tags.append("invisible_char")

    for f in FIELDS:
        if f in ("province", "district", "street_type"):
            continue
        v = gold[f]
        assert v is None or v in raw, (f, v, raw)
    return {"id": f"synth_{i:05d}", "raw": raw, "expect": gold, "tags": sorted(set(tags))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--out", default=str(ROOT / "tests" / "fixtures" / "synth_corpus.json"))
    args = ap.parse_args(argv)
    ref = Ref(ROOT / "data" / "reference.json.gz")
    rnd = random.Random(args.seed)
    recs = [gen_record(rnd, ref, i) for i in range(args.n)]
    Path(args.out).write_text(json.dumps(recs, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(recs)} записей → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
