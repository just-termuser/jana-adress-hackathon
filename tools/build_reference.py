"""Сборка справочника Jana Extract из OSM PBF Казахстана.

Запуск (нужны osmium и shapely из requirements-build.txt):

    python tools/build_reference.py --pbf ../osm/kazakhstan-261015.osm.pbf \
        --out data/reference.json.gz

Полигоны используются только здесь, при сборке: каждая точка (НП, улица,
адрес) привязывается к области (admin_level=4) и району (admin_level=6).
В рантайм уходят плоские таблицы без геометрии.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import osmium
import shapely
from shapely import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jana_extract.text import core, fold

REPUBLICAN_CITIES = ("Алматы", "Астана", "Шымкент")
SETTLEMENT_PLACES = {"city": 4, "town": 3, "village": 2, "hamlet": 1}
AREA_PLACES = {"suburb", "quarter", "neighbourhood", "city_block"}
MKR_HINT = re.compile(
    r"микрорайон|мкр|шағын аудан|ықшам аудан|жилой массив|ж/м|жилмассив|массив|квартал|жк\b",
    re.I,
)

# Исторические и разговорные названия регионов, которых нет в OSM как name:*.
REGION_ALIASES = {
    "Туркестанская область": ["Южно-Казахстанская область", "ЮКО", "Оңтүстік Қазақстан облысы"],
    "Восточно-Казахстанская область": ["ВКО"],
    "Северо-Казахстанская область": ["СКО"],
    "Западно-Казахстанская область": ["ЗКО"],
    "Костанайская область": ["Кустанайская область"],
    "Астана": ["Нур-Султан", "Нурсултан", "Nur-Sultan", "Astana", "Акмола город"],
    "Алматы": ["Almaty", "Алма-Ата"],
    "Шымкент": ["Shymkent", "Чимкент"],
}
# Переименованные районы Астаны: в адресах встречаются старые формы.
DISTRICT_ALIASES = {
    ("Астана", "Алматы"): ["Алматинский"],
    ("Астана", "Сарыарка"): ["Сарыаркинский"],
    ("Астана", "Есиль"): ["Есильский"],
    ("Астана", "Байконур"): ["Байконурский"],
    ("Астана", "Нура"): ["Нуринский"],
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def district_core(name: str) -> str:
    """«Ауэзовский район» → «Ауэзовский», «район Есиль» → «Есиль»,
    «Әуезов ауданы» → «Әуезов». Регистр и написание сохраняются."""
    out = re.sub(r"\b(район|ауданы|аудан|р-н)\b", "", name, flags=re.I)
    return " ".join(out.split())


# --------------------------------------------------------------------------- pass 1


def load_admin_areas(pbf: str):
    log("pass 1: административные границы (admin_level 4 и 6)")
    wkb = osmium.geom.WKBFactory()
    regions, districts = [], []
    fp = osmium.FileProcessor(pbf).with_areas(
        osmium.filter.TagFilter(("boundary", "administrative"))
    )
    for obj in fp:
        if not obj.is_area() or obj.from_way():
            continue
        t = obj.tags
        level = t.get("admin_level")
        if level not in ("4", "6"):
            continue
        try:
            geom = shapely.from_wkb(wkb.create_multipolygon(obj))
        except Exception:
            continue
        item = {
            "name": t.get("name:ru") or t.get("name"),
            "kk": t.get("name:kk"),
            "raw": t.get("name"),
            "en": t.get("name:en"),
            "iso": t.get("ISO3166-2", ""),
            "geom": geom,
        }
        if level == "4":
            if item["iso"].startswith("KZ-"):
                regions.append(item)
        else:
            districts.append(item)
    log(f"  регионов KZ: {len(regions)}, кандидатов в районы: {len(districts)}")
    return regions, districts


# --------------------------------------------------------------------------- pass 2


class Collector:
    def __init__(self) -> None:
        self.places: list[dict] = []
        self.streets: Counter = Counter()  # (raw_name, lon3, lat3, src) -> count
        self.areas: Counter = Counter()  # (raw_name, lon3, lat3) -> count
        self.addr_city: Counter = Counter()  # (raw_city, lon2, lat2) -> count

    @staticmethod
    def _way_point(w):
        xs, ys = [], []
        for n in w.nodes:
            if n.location.valid():
                xs.append(n.location.lon)
                ys.append(n.location.lat)
        if not xs:
            return None
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def handle(self, obj, point) -> None:
        t = obj.tags
        lon, lat = point
        k3 = (round(lon, 3), round(lat, 3))
        place = t.get("place")
        if place in SETTLEMENT_PLACES and obj.is_node():
            self.places.append(
                {
                    "kind": place,
                    "names": [
                        t.get(k)
                        for k in (
                            "name:ru",
                            "name",
                            "name:kk",
                            "name:en",
                            "alt_name",
                            "old_name",
                            "official_name",
                        )
                        if t.get(k)
                    ],
                    "name": t.get("name:ru") or t.get("name"),
                    "pop": _int(t.get("population")),
                    "lon": lon,
                    "lat": lat,
                }
            )
        if place in AREA_PLACES or (t.get("landuse") == "residential" and "name" in t):
            for k in ("name", "name:ru", "name:kk"):
                if t.get(k):
                    self.areas[(t[k], *k3)] += 1
        if "highway" in t and "name" in t and not obj.is_node():
            for k in ("name", "name:ru", "name:kk", "old_name", "alt_name"):
                if t.get(k):
                    self.streets[(t[k], *k3)] += 3
        if "addr:housenumber" in t:
            street = t.get("addr:street")
            if street:
                target = self.areas if MKR_HINT.search(street) else self.streets
                target[(street, *k3)] += 1
            for k in ("addr:place", "addr:quarter", "addr:suburb", "addr:subdistrict"):
                if t.get(k):
                    self.areas[(t[k], *k3)] += 1
            if t.get("addr:city"):
                self.addr_city[(t["addr:city"], round(lon, 2), round(lat, 2))] += 1


def _int(v):
    try:
        return int(str(v).replace(" ", ""))
    except (TypeError, ValueError):
        return 0


def collect_points(pbf: str) -> Collector:
    log("pass 2: НП, улицы, адреса, микрорайоны")
    col = Collector()
    fp = osmium.FileProcessor(pbf).with_locations()
    n = 0
    for obj in fp:
        if obj.is_relation():
            continue
        t = obj.tags
        if not (
            "place" in t
            or "addr:housenumber" in t
            or ("highway" in t and "name" in t)
            or (t.get("landuse") == "residential" and "name" in t)
        ):
            continue
        if obj.is_node():
            if not obj.location.valid():
                continue
            point = (obj.location.lon, obj.location.lat)
        else:
            point = Collector._way_point(obj)
            if point is None:
                continue
        col.handle(obj, point)
        n += 1
    log(
        f"  объектов: {n}, НП: {len(col.places)}, "
        f"улиц-ключей: {len(col.streets)}, мкр-ключей: {len(col.areas)}"
    )
    return col


# --------------------------------------------------------------------------- locate


class Locator:
    """Вектором находит область и район для массива точек."""

    def __init__(self, regions, districts):
        self.regions = regions
        self.region_tree = STRtree([r["geom"] for r in regions])
        self.districts = districts
        self.district_tree = STRtree([d["geom"] for d in districts]) if districts else None

    def _lookup(self, tree, xs, ys):
        out = np.full(len(xs), -1, dtype=np.int64)
        if tree is None or not len(xs):
            return out
        pts = shapely.points(np.asarray(xs), np.asarray(ys))
        inp, idx = tree.query(pts, predicate="within")
        out[inp] = idx
        return out

    def locate(self, xs, ys):
        return self._lookup(self.region_tree, xs, ys), self._lookup(self.district_tree, xs, ys)


# --------------------------------------------------------------------------- build


def build(pbf: str) -> dict:
    t0 = time.time()
    regions_raw, districts_raw = load_admin_areas(pbf)
    regions_raw.sort(key=lambda r: r["iso"])

    # Районы: только те, что лежат внутри KZ. Город-администрации «Г.А.»/«Қ.Ә.»
    # — это города областного значения, а не районы; их берём как НП.
    kz = shapely.union_all([r["geom"] for r in regions_raw])
    shapely.prepare(kz)
    districts_kz = []
    for d in districts_raw:
        rp = d["geom"].representative_point()
        if kz.contains(rp):
            districts_kz.append(d)
    region_tree = STRtree([r["geom"] for r in regions_raw])
    for d in districts_kz:
        rp = d["geom"].representative_point()
        hit = region_tree.query(rp, predicate="within")
        d["region"] = int(hit[0]) if len(hit) else -1

    is_city_admin = re.compile(
        r"\b(Г\.\s?А\.|Қ\.\s?Ә\.|городская администрация|қалалық әкімдігі)", re.I
    )
    true_districts = [
        d for d in districts_kz if not is_city_admin.search(f"{d['raw']} {d['name']}")
    ]
    city_admins = [d for d in districts_kz if is_city_admin.search(f"{d['raw']} {d['name']}")]
    log(f"  районов: {len(true_districts)}, городов-администраций: {len(city_admins)}")

    loc = Locator(regions_raw, true_districts)
    col = collect_points(pbf)

    # -- регионы
    regions = []
    for r in regions_raw:
        name = r["name"]
        kind = "city" if name in REPUBLICAN_CITIES else "oblast"
        variants = {name, r["kk"], r["raw"], r["en"], *REGION_ALIASES.get(name, [])}
        regions.append({"name": name, "kind": kind, "variants": sorted(v for v in variants if v)})

    # -- районы
    districts = []
    for d in true_districts:
        reg = d["region"]
        city = regions[reg]["name"] if reg >= 0 and regions[reg]["kind"] == "city" else None
        name = district_core(d["name"])
        variants = {name, district_core(d["kk"] or ""), district_core(d["raw"] or "")}
        variants |= set(DISTRICT_ALIASES.get((city, name), []))
        districts.append(
            {
                "name": name,
                "region": reg,
                "city": city,
                "variants": sorted(v for v in variants if v),
            }
        )

    # -- населённые пункты
    xs = [p["lon"] for p in col.places]
    ys = [p["lat"] for p in col.places]
    p_reg, p_dis = loc.locate(xs, ys)
    settlements = []
    for p, reg, dis in zip(col.places, p_reg, p_dis, strict=True):
        if reg < 0 or not p["name"]:
            continue
        settlements.append(
            {
                "name": p["name"],
                "kind": p["kind"],
                "rank": SETTLEMENT_PLACES[p["kind"]],
                "pop": p["pop"],
                "region": int(reg),
                # Для городов республиканского значения район НП не определяем:
                # он зависит от улицы, а не от точки центра города.
                "district": int(dis) if dis >= 0 and regions[reg]["kind"] != "city" else None,
                "variants": sorted({v for v in p["names"] if v}),
            }
        )
    # Города областного значения из городских администраций (например, «Аксу Г.А.»).
    known = {fold(s["name"]) for s in settlements}
    for d in city_admins:
        name = re.sub(r"\s*(Г\.\s?А\.|Қ\.\s?Ә\.).*$", "", d["name"]).strip()
        if fold(name) in known or d["region"] < 0:
            continue
        settlements.append(
            {
                "name": name,
                "kind": "town",
                "rank": 3,
                "pop": 0,
                "region": d["region"],
                "district": None,
                "variants": [name],
            }
        )
    # Варианты написания города из addr:city (там часто казахская форма).
    s_by_fold = defaultdict(list)
    for i, s in enumerate(settlements):
        s_by_fold[core(s["name"])].append(i)
    ac_items = list(col.addr_city.items())
    a_reg, _ = loc.locate([k[1] for k, _ in ac_items], [k[2] for k, _ in ac_items])
    extra = Counter()
    for ((raw, _x, _y), cnt), reg in zip(ac_items, a_reg, strict=True):
        if reg >= 0:
            extra[(raw, int(reg))] += cnt
    for (raw, reg), cnt in extra.items():
        key = core(raw)
        cands = [i for i in s_by_fold.get(key, []) if settlements[i]["region"] == reg]
        if cands or cnt < 20:
            continue
        # Часто addr:city — казахская форма того же НП: ищем по fold-ключу без типа.
        for i, s in enumerate(settlements):
            if s["region"] == reg and any(core(v) == key for v in s["variants"]):
                cands.append(i)
                break
        for i in cands:
            if raw not in settlements[i]["variants"]:
                settlements[i]["variants"].append(raw)

    # -- улицы и микрорайоны: глобальные частоты и районы в городах респ. значения
    def aggregate(counter: Counter):
        items = list(counter.items())
        r_idx, d_idx = loc.locate([k[1] for k, _ in items], [k[2] for k, _ in items])
        glob = Counter()
        by_city: dict[str, dict[str, Counter]] = {
            c: defaultdict(Counter) for c in REPUBLICAN_CITIES
        }
        by_region: dict[int, Counter] = defaultdict(Counter)
        for ((raw, _x, _y), cnt), reg, dis in zip(items, r_idx, d_idx, strict=True):
            if reg < 0:
                continue
            key = core(raw)
            if not key or len(key) > 60:
                continue
            glob[key] += cnt
            by_region[int(reg)][key] += cnt
            city = regions[reg]["name"]
            if city in by_city and dis >= 0:
                by_city[city][key][int(dis)] += cnt
        return glob, by_city, by_region

    s_glob, s_city, s_region = aggregate(col.streets)
    a_glob, a_city, a_region = aggregate(col.areas)

    def pack_city(by_city):
        out = {}
        for city, names in by_city.items():
            out[city] = {k: {str(d): c for d, c in v.most_common(4)} for k, v in names.items()}
        return out

    ref = {
        "meta": {
            "source": Path(pbf).name,
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "build_seconds": round(time.time() - t0, 1),
        },
        "regions": regions,
        "districts": districts,
        "settlements": settlements,
        "street_names": {k: c for k, c in s_glob.items() if c >= 2 or len(k) >= 4},
        "area_names": dict(a_glob),
        "region_streets": {
            str(r): sorted(k for k, c in v.items() if c >= 2) for r, v in s_region.items()
        },
        "region_areas": {str(r): sorted(v) for r, v in a_region.items()},
        "city_streets": pack_city(s_city),
        "city_areas": pack_city(a_city),
    }
    ref["meta"]["counts"] = {
        "regions": len(regions),
        "districts": len(districts),
        "settlements": len(settlements),
        "street_names": len(ref["street_names"]),
        "area_names": len(ref["area_names"]),
        "city_streets": {c: len(v) for c, v in ref["city_streets"].items()},
        "city_areas": {c: len(v) for c, v in ref["city_areas"].items()},
    }
    return ref


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pbf", required=True)
    ap.add_argument(
        "--out", default=str(Path(__file__).resolve().parents[1] / "data" / "reference.json.gz")
    )
    args = ap.parse_args(argv)
    ref = build(args.pbf)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ref, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # mtime=0 — одинаковые данные дают байт-в-байт одинаковый файл.
    with gzip.GzipFile(out, "wb", compresslevel=9, mtime=0) as fh:
        fh.write(payload)
    log(
        f"готово: {out} ({out.stat().st_size / 1e6:.1f} МБ), "
        f"{json.dumps(ref['meta']['counts'], ensure_ascii=False)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
