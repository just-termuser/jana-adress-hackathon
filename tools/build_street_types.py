"""Таблица типов улиц из OSM: с каким типом записано каждое название.

    python tools/build_street_types.py --pbf ../osm/kazakhstan-261015.osm.pbf

Из названий дорог (`highway=*` + `name*`) и адресов (`addr:street`) берётся
тип, если он записан словом («улица Абая», «Абай даңғылы», «микрорайон Асар»),
и ключ названия без типа (`core()`, тот же, что у парсера). Счётчики — по
городам республиканского значения, по областям и по стране.

Независимая проверка: 10% адресов `addr:street` с явным типом (детерминированно
по id объекта) в счётчики НЕ попадают и сохраняются в
tests/fixtures/street_type_holdout.json. На них тип отрезается, предсказывается
по таблице и сравнивается с записанным (tools/eval_street_types.py).
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import osmium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from build_reference import REPUBLICAN_CITIES, Locator, load_admin_areas  # noqa: E402

from jana_extract import markers as M  # noqa: E402
from jana_extract.text import core, fold  # noqa: E402

HOLDOUT_SHARE = 10  # каждый 10-й адрес с явным типом — в проверку
HOLDOUT_MAX = 5000


def name_type(raw: str) -> str | None:
    """Канонический тип улицы, записанный в названии, или None."""
    words = fold(raw).split()
    for i in range(len(words)):
        for multi, cat, canon in M.MULTI:
            if tuple(words[i : i + len(multi)]) == multi and cat in (M.STREET, M.AREA) and canon:
                return canon
    for w in words:
        hit = M.SINGLE.get(w)
        if hit and hit[0] in (M.STREET, M.AREA) and hit[1]:
            return hit[1]
    return None


class Collector(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[tuple[str, float, float, bool, int]] = []  # raw, lon, lat, holdout?, w

    @staticmethod
    def _point(w):
        xs = [n.location.lon for n in w.nodes if n.location.valid()]
        ys = [n.location.lat for n in w.nodes if n.location.valid()]
        return (sum(xs) / len(xs), sum(ys) / len(ys)) if xs else None

    def _add(self, obj, point, raw: str, is_addr: bool) -> None:
        holdout = is_addr and name_type(raw) is not None and obj.id % HOLDOUT_SHARE == 0
        self.items.append((raw, point[0], point[1], holdout, 1))

    def node(self, n):
        street = n.tags.get("addr:street")
        if street and "addr:housenumber" in n.tags and n.location.valid():
            self._add(n, (n.location.lon, n.location.lat), street, True)

    def way(self, w):
        t = w.tags
        if "highway" in t or "addr:street" in t:
            point = self._point(w)
            if point is None:
                return
            if "highway" in t:
                for k in ("name", "name:ru", "name:kk"):
                    if t.get(k):
                        self._add(w, point, t[k], False)
            if t.get("addr:street") and "addr:housenumber" in t:
                self._add(w, point, t["addr:street"], True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pbf", required=True)
    ap.add_argument("--out", default=str(ROOT / "data" / "street_types.json.gz"))
    ap.add_argument(
        "--holdout", default=str(ROOT / "tests" / "fixtures" / "street_type_holdout.json")
    )
    args = ap.parse_args(argv)
    t0 = time.time()
    regions, _districts = load_admin_areas(args.pbf)
    regions.sort(key=lambda r: r["iso"])  # тот же порядок областей, что в справочнике
    names = [r["name"] for r in regions]
    loc = Locator(regions, [])
    col = Collector()
    col.apply_file(args.pbf, locations=True)
    print(f"объектов с названием улицы: {len(col.items)}", flush=True)
    reg_idx, _ = loc.locate([x[1] for x in col.items], [x[2] for x in col.items])

    by_region: dict[int, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    by_city: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    national: dict[str, Counter] = defaultdict(Counter)
    holdout = []
    seen_ways: set[tuple] = set()
    for (raw, lon, lat, is_holdout, _w), reg in zip(col.items, reg_idx, strict=True):
        if reg < 0:
            continue
        t = name_type(raw)
        key = core(raw)
        if not key:
            continue
        if is_holdout:
            holdout.append({"raw": raw, "region": int(reg), "type": t})
            continue
        if t is None:
            continue
        # участки одной дороги считаем один раз на ~1 км
        dedup = (raw, round(lon, 2), round(lat, 2))
        if dedup in seen_ways:
            continue
        seen_ways.add(dedup)
        by_region[int(reg)][key][t] += 1
        national[key][t] += 1
        if names[reg] in REPUBLICAN_CITIES:
            by_city[names[reg]][key][t] += 1

    def pack(table):
        return {k: dict(v) for k, v in table.items() if sum(v.values()) >= 1}

    out = {
        "meta": {
            "source": Path(args.pbf).name,
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "build_seconds": round(time.time() - t0, 1),
            "holdout_rule": (
                f"addr:street с явным типом, id % {HOLDOUT_SHARE} == 0, в счётчики не входят"
            ),
            "counts": {
                "national_keys": len(national),
                "region_keys": sum(len(v) for v in by_region.values()),
                "city_keys": {c: len(v) for c, v in by_city.items()},
                "holdout_total": len(holdout),
            },
        },
        "national": pack(national),
        "by_region": {str(r): pack(v) for r, v in by_region.items()},
        "by_city": {c: pack(v) for c, v in by_city.items()},
    }
    payload = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.GzipFile(args.out, "wb", compresslevel=9, mtime=0) as fh:
        fh.write(payload)
    rnd = random.Random(20261018)
    sample = rnd.sample(holdout, min(HOLDOUT_MAX, len(holdout)))
    Path(args.holdout).write_text(
        json.dumps(sample, ensure_ascii=False, indent=0), encoding="utf-8"
    )
    size = Path(args.out).stat().st_size / 1e3
    print(f"{args.out}: {size:.0f} КБ; {json.dumps(out['meta']['counts'], ensure_ascii=False)}")
    print(f"проверочная выборка: {len(sample)} из {len(holdout)} → {args.holdout}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
