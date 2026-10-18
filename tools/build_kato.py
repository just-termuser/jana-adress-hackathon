"""Сборка производного справочника КАТО и его сопоставление с OSM.

    python tools/build_kato.py --xlsx "data/raw/КАТО_18.09.2026.xlsx"

Источник: классификатор административно-территориальных объектов (КАТО),
Бюро национальной статистики РК, https://stat.gov.kz/ru/classifiers/statistical/21/.
Исходный xlsx не коммитится (data/raw/ в .gitignore); в Git — только
data/kato.json.gz с источником, датой и sha256 исходника в meta.

Файл читается стандартной библиотекой как набор XML внутри zip: из него берутся
только строки таблиц, ничего не исполняется; размеры частей ограничены.

Сопоставление с OSM (data/reference.json.gz) — по паре «область + название без
типа» (русское или казахское название). Результат на сущность: ссылка на объект
OSM или пометка «новый» (нет в OSM).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jana_extract.text import fold  # noqa: E402

SOURCE_URL = "https://stat.gov.kz/ru/classifiers/statistical/21/"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
MAX_PART_BYTES = 200 * 1024 * 1024  # защита от «zip-бомбы»
REPUBLICAN = {"71": "Астана", "75": "Алматы", "79": "Шымкент"}

# Префиксы НП в КАТО и ранг, с которым новый НП попадает в справочник
# (4 — город, 3 — посёлок/малый город, 2 — село, 1 — прочее).
SETTLEMENT_PREFIX = re.compile(
    r"^(г|п|пгт|с|ст|уч|рзд|кр|отг|крес|отд|жив|маш|оздоровит|а)\.\s*", re.I
)
PREFIX_RANK = {"г": 3, "п": 2, "пгт": 2, "с": 2, "а": 2, "ст": 1}
# Новые (нет в OSM) НП берём только с этими префиксами. Без префикса в КАТО —
# зимовки, точки, фермы, отделения («зимовка Магазин», «точка Мешит»): они
# перехватывали бы ориентиры и улицы и для адресов не нужны.
NEW_SETTLEMENT_PREFIXES = frozenset(PREFIX_RANK)
KK_SUFFIX = re.compile(r"\s+(қ|а|к|кенті|ауылы|уч|ст|бекеті|разъезі)\.?$", re.I)
DISTRICT_WORDS = re.compile(r"\b(район|р\.а\.|ауданы|аудан|Г\.А\.|Қ\.Ә\.)", re.I)


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- xlsx


def read_xlsx(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as z:
        for name in ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"):
            if z.getinfo(name).file_size > MAX_PART_BYTES:
                raise ValueError(f"{name}: слишком большой ({z.getinfo(name).file_size} байт)")
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        strings = ["".join(t.text or "" for t in si.iter(NS + "t")) for si in sst.iter(NS + "si")]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows: list[dict[str, str]] = []
    for row in sheet.iter(NS + "row"):
        d: dict[str, str] = {}
        for c in row.iter(NS + "c"):
            col = re.match(r"[A-Z]+", c.get("r", "")).group()
            v = c.find(NS + "v")
            if c.get("t") == "s" and v is not None:
                d[col] = strings[int(v.text)]
            elif c.get("t") == "inlineStr":
                d[col] = "".join(t.text or "" for t in c.iter(NS + "t"))
            elif v is not None:
                d[col] = v.text or ""
        rows.append(d)
    header = rows[0]
    return [{header[k]: (v or "").strip() for k, v in r.items() if k in header} for r in rows[1:]]


# --------------------------------------------------------------------------- names


def clean_district(name: str) -> str:
    """«Ауэзовский район» → «Ауэзовский», «район им.Казыбек би» → «им. Казыбек би»."""
    s = DISTRICT_WORDS.sub(" ", name)
    s = re.sub(r"\bим\.\s*", "им. ", s)
    return " ".join(s.split())


def clean_settlement(name: str) -> tuple[str, str]:
    m = SETTLEMENT_PREFIX.match(name)
    prefix = m.group(1).lower() if m else ""
    return (name[m.end() :] if m else name).strip(), prefix


def clean_kk(name: str) -> str:
    return KK_SUFFIX.sub("", DISTRICT_WORDS.sub(" ", name)).strip()


def key(name: str) -> str:
    # «им.Габита Мусрепова» и «имени Габита Мусрепова» — одно и то же.
    return fold(re.sub(r"\bим\.\s*", "имени ", name))


# --------------------------------------------------------------------------- build


def classify(rows: list[dict[str, str]]):
    regions, districts, settlements = [], [], []
    for r in rows:
        code = r["te"]
        if len(code) != 9 or not code.isdigit():
            continue
        ru, kk = r.get("rus_name", ""), r.get("kaz_name", "")
        ab = code[:2]
        if code[2:] == "0000000":
            regions.append({"code": code, "ru": ru, "kk": kk})
            continue
        is_district_name = bool(re.search(r"\bрайон\b", ru, re.I)) and "р.а." not in ru
        l2 = code[4:] == "00000"
        if ab in REPUBLICAN and code[6:] == "000" and is_district_name:
            districts.append(
                {"code": code, "ru": ru, "kk": kk, "city": REPUBLICAN[ab], "kind": "city"}
            )
        elif l2 and is_district_name:
            districts.append({"code": code, "ru": ru, "kk": kk, "city": None, "kind": "district"})
        elif not l2 and is_district_name and code[6:] != "000":
            # «район им.Казыбек би» внутри «Караганда Г.А.»
            districts.append({"code": code, "ru": ru, "kk": kk, "city": None, "kind": "city_of_ga"})
        elif not l2 and (code[6:] != "000" or ru.startswith("г.")):
            name, prefix = clean_settlement(ru)
            if name and not re.search(r"\b(с\.о|с\.а|п\.а|г\.а|р\.а)\b", ru, re.I):
                settlements.append({"code": code, "ru": name, "kk": clean_kk(kk), "prefix": prefix})
    # Город-администрация «Караганда Г.А.» — для районов городов областного значения.
    ga = {
        r["te"][:4]: re.sub(r"\s*Г\.А\..*$", "", r["rus_name"]).strip()
        for r in rows
        if r.get("rus_name", "").endswith("Г.А.") and r["te"][4:] == "00000"
    }
    for d in districts:
        if d["kind"] == "city_of_ga":
            d["city"] = ga.get(d["code"][:4])
    return regions, districts, settlements


def link(regions, districts, settlements, osm: dict) -> dict:
    osm_regions = osm["regions"]
    region_by_fold: dict[str, int] = {}
    for i, r in enumerate(osm_regions):
        for v in r["variants"]:
            region_by_fold[key(v)] = i
            region_by_fold[key(re.sub(r"\s*(облысы|область)$", "", v))] = i
    out_regions = []
    region_of_ab: dict[str, int] = {}
    for r in regions:
        cand = [key(r["kk"]), key(re.sub(r"^г\.", "", r["ru"])), key(r["ru"])]
        cand += [key(re.sub(r"\s*облысы$", "", r["kk"]))]
        osm_i = next((region_by_fold[c] for c in cand if c in region_by_fold), None)
        out_regions.append({**r, "osm": osm_i})
        if osm_i is not None:
            region_of_ab[r["code"][:2]] = osm_i

    osm_d_index: dict[tuple[int, str], int] = {}
    for i, d in enumerate(osm["districts"]):
        for v in d["variants"]:
            osm_d_index[(d["region"], key(clean_district(v)))] = i
    out_districts = []
    for d in districts:
        reg = region_of_ab.get(d["code"][:2])
        canon = clean_district(d["ru"])
        keys = {key(canon), key(clean_district(d["kk"]))}
        osm_i = next((osm_d_index[(reg, k)] for k in keys if (reg, k) in osm_d_index), None)
        out_districts.append({**d, "region": reg, "canonical": canon, "osm": osm_i})
    district_of_code = {
        d["code"][:4]: j for j, d in enumerate(out_districts) if d["kind"] == "district"
    }

    osm_s_index: dict[tuple[int, str], list[int]] = defaultdict(list)
    for i, s in enumerate(osm["settlements"]):
        for v in s["variants"]:
            for part in v.split(";"):
                k = (s["region"], key(part))
                if i not in osm_s_index[k]:
                    osm_s_index[k].append(i)
    kato_count = Counter((region_of_ab.get(s["code"][:2]), key(s["ru"])) for s in settlements)
    out_settlements = []
    for s in settlements:
        reg = region_of_ab.get(s["code"][:2])
        dj = district_of_code.get(s["code"][:4])
        osm_i = None
        for k in (key(s["ru"]), key(s["kk"])):
            cands = osm_s_index.get((reg, k), [])
            if not cands:
                continue
            osm_d = out_districts[dj]["osm"] if dj is not None else None
            same = [c for c in cands if osm["settlements"][c]["district"] == osm_d]
            if same:
                osm_i = same[0]
            elif len(cands) == 1 and kato_count[(reg, key(s["ru"]))] == 1:
                # Название уникально в области с обеих сторон, а районы разные —
                # настоящий конфликт источников (часто это районы реформы 2022 г.).
                osm_i = cands[0]
            # Иначе одноимённые сёла разных районов — это разные НП: не связываем.
            break
        if osm_i is None and s["prefix"] not in NEW_SETTLEMENT_PREFIXES:
            continue
        out_settlements.append(
            {
                **s,
                "region": reg,
                "district": dj,
                "osm": osm_i,
                "rank": PREFIX_RANK.get(s["prefix"], 1),
            }
        )
    return {"regions": out_regions, "districts": out_districts, "settlements": out_settlements}


def coverage(linked: dict, osm: dict) -> dict:
    d = linked["districts"]
    s = linked["settlements"]
    new_city_districts = defaultdict(list)
    for x in d:
        if x["osm"] is None and x["kind"] != "district":
            new_city_districts[x["city"] or "?"].append(x["canonical"])
    osm_linked = {x["osm"] for x in s if x["osm"] is not None}
    return {
        "regions": {
            "kato": len(linked["regions"]),
            "linked": sum(x["osm"] is not None for x in linked["regions"]),
        },
        "districts": {
            "kato": len(d),
            "linked": sum(x["osm"] is not None for x in d),
            "new": sum(x["osm"] is None for x in d),
            "new_names": sorted(
                f"{x['canonical']} ({x['city'] or 'район'})" for x in d if x["osm"] is None
            ),
        },
        "city_districts_new": {k: sorted(v) for k, v in new_city_districts.items()},
        "settlements": {
            "kato": len(s),
            "linked_to_osm": sum(x["osm"] is not None for x in s),
            "new": sum(x["osm"] is None for x in s),
            "osm_total": len(osm["settlements"]),
            "osm_with_kato": len(osm_linked),
            "new_by_prefix": dict(
                Counter(x["prefix"] or "—" for x in s if x["osm"] is None).most_common()
            ),
            "skipped_rule": "новые НП без префикса г./п./пгт/с./а./ст. не включаются",
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--osm", default=str(ROOT / "data" / "reference.json.gz"))
    ap.add_argument("--out", default=str(ROOT / "data" / "kato.json.gz"))
    args = ap.parse_args(argv)

    src = Path(args.xlsx)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    rows = read_xlsx(src)
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", src.name)
    edition = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
    regions, districts, settlements = classify(rows)
    with gzip.open(args.osm, "rt", encoding="utf-8") as fh:
        osm = json.load(fh)
    linked = link(regions, districts, settlements, osm)
    cov = coverage(linked, osm)
    out = {
        "meta": {
            "source": SOURCE_URL,
            "file": src.name,
            "edition": edition,
            "sha256": sha,
            "rows": len(rows),
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "attribution": "КАТО, Бюро национальной статистики АСПиР РК (stat.gov.kz)",
            "coverage": cov,
        },
        **linked,
    }
    payload = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.GzipFile(args.out, "wb", compresslevel=9, mtime=0) as fh:
        fh.write(payload)
    size_kb = Path(args.out).stat().st_size / 1e3
    log(f"{args.out}: {size_kb:.0f} КБ, строк КАТО {len(rows)}, sha256 {sha[:12]}…")
    log(json.dumps(cov, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
