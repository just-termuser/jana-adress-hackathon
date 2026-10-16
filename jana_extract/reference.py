"""Справочник в памяти: регионы, районы, НП, улицы и микрорайоны.

Файл `data/reference.json.gz` собирается `tools/build_reference.py` из OSM.
Все ключи индексов — `fold()`/`core()` от названий; сами названия районов и
регионов возвращаются в справочном формате («Ауэзовский», «Актюбинская область»).
"""

from __future__ import annotations

import gzip
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

from .text import core, fold

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "reference.json.gz"

# Ключи уже прошли fold(): мягкий знак убран, поэтому «област», а не «область».
_REGION_WORDS = re.compile(r"\b(област|облысы|обл)\b")
_DISTRICT_WORDS = re.compile(r"\b(район|ауданы|аудан|р н)\b")


@dataclass(frozen=True, slots=True)
class Settlement:
    idx: int
    name: str
    kind: str
    rank: int
    pop: int
    region: int
    district: int | None


class Reference:
    def __init__(self, data: dict) -> None:
        self.meta = data["meta"]
        self.regions = data["regions"]
        self.districts = data["districts"]
        self.settlements = [
            Settlement(i, s["name"], s["kind"], s["rank"], s["pop"], s["region"], s["district"])
            for i, s in enumerate(data["settlements"])
        ]
        self.street_names: dict[str, int] = data["street_names"]
        self.area_names: dict[str, int] = data["area_names"]
        self.city_streets: dict[str, dict[str, dict[str, int]]] = data["city_streets"]
        self.city_areas: dict[str, dict[str, dict[str, int]]] = data["city_areas"]
        self.region_streets = {int(k): frozenset(v) for k, v in data["region_streets"].items()}
        self.region_areas = {int(k): frozenset(v) for k, v in data["region_areas"].items()}

        self.region_index: dict[str, int] = {}
        for i, r in enumerate(self.regions):
            for v in r["variants"]:
                f = fold(v)
                self.region_index.setdefault(f, i)
                stem = " ".join(_REGION_WORDS.sub(" ", f).split())
                # «Актюбинская» без слова «область» — тоже регион. А вот
                # «Алматы» как стем «Алматы облысы» — нет: это город.
                if stem and stem != f and r["kind"] == "oblast" and stem.endswith(("ая", "ой")):
                    self.region_index.setdefault(stem, i)
                if r["kind"] == "city":
                    self.region_index.setdefault(f, i)
        self.region_by_name = {r["name"]: i for i, r in enumerate(self.regions)}

        self.district_index: dict[str, list[int]] = defaultdict(list)
        for i, d in enumerate(self.districts):
            keys = {fold(v) for v in d["variants"]}
            for k in list(keys):
                keys.add(" ".join(_DISTRICT_WORDS.sub(" ", k).split()))
            for k in keys:
                if k:
                    self.district_index[k].append(i)

        self.settlement_index: dict[str, list[int]] = defaultdict(list)
        for i, s in enumerate(data["settlements"]):
            keys = set()
            for v in s["variants"]:
                for part in v.split(";"):
                    keys.add(fold(part))
                    keys.add(core(part))
            for k in keys:
                if k and i not in self.settlement_index[k]:
                    self.settlement_index[k].append(i)
        self._settlement_keys = [k for k in self.settlement_index if len(k) >= 4]
        self._district_keys = [k for k in self.district_index if len(k) >= 4]
        self._region_keys = [k for k in self.region_index if len(k) >= 4]

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> Reference:
        p = Path(path or os.environ.get("JANA_REFERENCE") or DEFAULT_PATH)
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _fuzzy(key: str, choices: list[str], cutoff: float) -> tuple[str, float] | None:
        if len(key) < 4 or not choices:
            return None
        hit = process.extractOne(key, choices, scorer=fuzz.ratio, score_cutoff=cutoff)
        if hit is None:
            return None
        return hit[0], hit[1] / 100.0

    def region_name(self, idx: int | None) -> str | None:
        return None if idx is None or idx < 0 else self.regions[idx]["name"]

    def district_name(self, idx: int | None) -> str | None:
        return None if idx is None or idx < 0 else self.districts[idx]["name"]

    def is_republican_city(self, region_idx: int | None) -> bool:
        return (
            region_idx is not None
            and region_idx >= 0
            and self.regions[region_idx]["kind"] == "city"
        )

    # ------------------------------------------------------------------ lookups

    def find_region(self, text: str, fuzzy: bool = True) -> tuple[int, float] | None:
        f = fold(text)
        if not f:
            return None
        if f in self.region_index:
            return self.region_index[f], 1.0
        stem = " ".join(_REGION_WORDS.sub(" ", f).split())
        if stem in self.region_index:
            return self.region_index[stem], 0.95
        if fuzzy:
            hit = self._fuzzy(stem or f, self._region_keys, 85)
            if hit:
                return self.region_index[hit[0]], hit[1] * 0.9
        return None

    def find_district(
        self, text: str, region: int | None = None, fuzzy: bool = True
    ) -> tuple[int, float] | None:
        f = " ".join(_DISTRICT_WORDS.sub(" ", fold(text)).split())
        if not f:
            return None
        cands, score = self.district_index.get(f), 1.0
        if not cands and fuzzy:
            hit = self._fuzzy(f, self._district_keys, 85)
            if hit:
                cands, score = self.district_index[hit[0]], hit[1] * 0.9
        if not cands:
            return None
        if region is not None and region >= 0:
            same = [c for c in cands if self.districts[c]["region"] == region]
            if same:
                return same[0], score
            return None
        # Без контекста неоднозначный район (Абайский есть в нескольких местах)
        # всё равно возвращаем — имя района одинаковое, но доверие ниже.
        return cands[0], score if len(cands) == 1 else score * 0.85

    def find_settlement(self, text: str, fuzzy: bool = True) -> tuple[list[Settlement], float]:
        f = fold(text)
        if not f:
            return [], 0.0
        idx = self.settlement_index.get(f) or self.settlement_index.get(core(text))
        score = 1.0
        if not idx and fuzzy and len(f) >= 5:
            hit = self._fuzzy(f, self._settlement_keys, 88)
            if hit:
                idx, score = self.settlement_index[hit[0]], hit[1] * 0.9
        if not idx:
            return [], 0.0
        found = sorted((self.settlements[i] for i in idx), key=lambda s: (-s.rank, -s.pop))
        return found, score

    def best_settlement(
        self, cands: list[Settlement], region: int | None = None, district: int | None = None
    ) -> Settlement | None:
        """Выбор НП с учётом контекста. Без контекста — самый крупный,
        если он явно крупнее остальных (город против одноимённых аулов)."""
        if not cands:
            return None
        pool = cands
        if region is not None and region >= 0:
            pool = [c for c in pool if c.region == region] or []
        if district is not None and district >= 0 and pool:
            pool = [c for c in pool if c.district == district] or pool
        if not pool:
            return None
        if len(pool) == 1 or pool[0].rank > pool[1].rank:
            return pool[0]
        return None

    def area_or_street_district(self, city_region: int, key: str) -> tuple[int, float] | None:
        """Район города респ. значения по названию улицы или микрорайона."""
        city = self.region_name(city_region)
        best: dict[str, int] = {}
        for table in (self.city_areas, self.city_streets):
            counts = table.get(city or "", {}).get(key)
            if counts:
                for d, c in counts.items():
                    best[d] = best.get(d, 0) + c
        if not best:
            return None
        total = sum(best.values())
        d, c = max(best.items(), key=lambda kv: kv[1])
        return int(d), c / total

    def known_street(self, key: str, region: int | None = None) -> bool:
        if region is not None and region >= 0:
            return key in self.region_streets.get(region, ())
        return key in self.street_names

    def known_area(self, key: str, region: int | None = None) -> bool:
        if region is not None and region >= 0:
            return key in self.region_areas.get(region, ())
        return key in self.area_names


@lru_cache(maxsize=1)
def get_reference() -> Reference:
    return Reference.load()
