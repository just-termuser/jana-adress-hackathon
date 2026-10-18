"""Слияние производного КАТО (data/kato.json.gz) со справочником OSM при загрузке.

Приоритет источников:
- КАТО — официальные названия и коды областей, районов и НП, принадлежность
  НП району; районы городов областного значения (Актобе, Караганда, Тараз),
  которых нет в OSM;
- OSM — улицы, микрорайоны, ЖК и всё, что связано с геометрией (район по улице).

Выходной формат для сущностей, которые есть в OSM, не меняется (формат примера
ТЗ: «Абайская область», «Байконур»). Официальное название и код КАТО лежат в
`official` и отдаются в `_meta.official`. `kato_canonical=True` (переменная
JANA_KATO_CANONICAL=1) выводит формы КАТО везде («область Абай», «Байқоңыр»).
"""

from __future__ import annotations

import re

from .text import fold

_REGION_RU = re.compile(r"^г\.\s*")


def _add_variant(entry: dict, *names: str) -> None:
    seen = {fold(v) for v in entry["variants"]}
    for n in names:
        if n and fold(n) not in seen:
            entry["variants"].append(n)
            seen.add(fold(n))


def _district_variants(canonical: str, kk: str) -> list[str]:
    out = [canonical, kk]
    m = re.match(r"^им\.\s*(.+)$", canonical)
    if m:  # «им. Казыбек би» → «имени Казыбек би», «Казыбек би»
        out += [f"имени {m.group(1)}", m.group(1)]
    return [x for x in out if x]


def merge_kato(data: dict, kato: dict, kato_canonical: bool = False) -> dict:
    stats = {"district_conflicts": 0, "districts_added": 0, "settlements_added": 0}
    conflicts: list[dict] = []
    regions, districts, settlements = data["regions"], data["districts"], data["settlements"]

    for r in kato["regions"]:
        if r["osm"] is None:
            continue
        entry = regions[r["osm"]]
        official = _REGION_RU.sub("", r["ru"])
        entry["official"] = {"kato": r["code"], "name": official}
        _add_variant(entry, r["ru"], official, r["kk"])
        if kato_canonical:
            entry["name"] = official

    # районы: КАТО-индекс → индекс в объединённом списке
    d_map: dict[int, int] = {}
    for j, d in enumerate(kato["districts"]):
        if d["region"] is None:
            continue
        kk = re.sub(r"\s*(ауданы|аудан|атын\.)\s*", " ", d["kk"]).strip()
        if d["osm"] is not None:
            entry = districts[d["osm"]]
            if kato_canonical:
                entry["name"] = d["canonical"]
        else:
            entry = {
                "name": d["canonical"],
                "region": d["region"],
                "city": d["city"],
                "variants": [],
            }
            districts.append(entry)
            stats["districts_added"] += 1
        entry["official"] = {"kato": d["code"], "name": d["ru"]}
        if d["city"] and not entry.get("city"):
            entry["city"] = d["city"]
        _add_variant(entry, *_district_variants(d["canonical"], kk))
        d_map[j] = d["osm"] if d["osm"] is not None else len(districts) - 1

    for s in kato["settlements"]:
        if s["region"] is None:
            continue
        district = d_map.get(s["district"]) if s["district"] is not None else None
        if s["osm"] is not None:
            entry = settlements[s["osm"]]
            entry["official"] = {"kato": s["code"], "name": s["ru"]}
            _add_variant(entry, s["ru"], s["kk"])
            # Принадлежность району — по КАТО (официально). Города областного
            # значения (внутри «Г.А.») района не имеют: district у них остаётся null.
            if district is not None and entry.get("district") != district:
                if entry.get("district") is not None:
                    stats["district_conflicts"] += 1
                    conflicts.append(
                        {
                            "settlement": entry["name"],
                            "region": regions[entry["region"]]["name"],
                            "osm": districts[entry["district"]]["name"],
                            "kato": districts[district]["name"],
                            "kato_code": s["code"],
                        }
                    )
                entry["district"] = district
        else:
            settlements.append(
                {
                    "name": s["ru"],
                    "kind": "town" if s["rank"] >= 3 else "village",
                    "rank": s["rank"],
                    "pop": 0,
                    "region": s["region"],
                    "district": district,
                    "variants": [v for v in (s["ru"], s["kk"]) if v],
                    "official": {"kato": s["code"], "name": s["ru"]},
                    "source": "kato",
                }
            )
            stats["settlements_added"] += 1

    data["meta"] = dict(data["meta"])
    data["meta"]["kato"] = {
        **{k: v for k, v in kato["meta"].items() if k != "coverage"},
        "merge": stats,
    }
    data["meta"]["kato_coverage"] = kato["meta"]["coverage"]
    data["meta"]["kato_conflicts"] = conflicts
    return data
