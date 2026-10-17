"""Порог района по улице/мкр: покрытие и точность при разных порогах и порядках.

    python tools/district_study.py

Берёт записи синтетики из городов респ. значения, где района нет в строке, и
для каждого порога считает покрытие (доля записей с выданным районом) и
точность (доля верных среди выданных) при двух порядках источников.

Оговорка: «истинный» район в синтетике выбран по тем же частотам OSM, что
использует парсер, поэтому кривая показывает поведение порога в предположении
«адреса распределены как здания в OSM», а не независимую проверку.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

from eval import district_in_text  # noqa: E402

from jana_extract.reference import get_reference  # noqa: E402
from jana_extract.service import extract_batch  # noqa: E402
from jana_extract.text import core  # noqa: E402

REPUBLICAN = ("Алматы", "Астана", "Шымкент")
THRESHOLDS = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9)


def main() -> int:
    ref = get_reference()
    cases = json.loads((ROOT / "tests/fixtures/synth_corpus.json").read_text(encoding="utf-8"))
    out = extract_batch([{"id": c["id"], "raw_address": c["raw"]} for c in cases])
    rows = []
    for c, o in zip(cases, out, strict=True):
        g = c["expect"]
        if g["province"] not in REPUBLICAN or not g["district"]:
            continue
        if district_in_text(c["raw"], g["district"]):
            continue
        reg = ref.region_by_name[g["province"]]
        cands = {}
        for src in ("living_area", "street_name"):
            if o[src]:
                hit = ref.area_or_street_district(reg, core(o[src]))
                if hit:
                    cands[src] = (ref.district_name(hit[0]), hit[1])
        rows.append((g["district"], cands))
    print(f"записей с восстанавливаемым районом: {len(rows)}")
    for order in (("street_name", "living_area"), ("living_area", "street_name")):
        print("порядок:", " → ".join(order))
        for t in THRESHOLDS:
            emitted = correct = 0
            for gold_d, cands in rows:
                pick = next((cands[s][0] for s in order if s in cands and cands[s][1] >= t), None)
                if pick:
                    emitted += 1
                    correct += pick == gold_d
            print(
                f"  порог {t:.2f}: покрытие {emitted / len(rows):.3f}, "
                f"точность {correct / max(emitted, 1):.3f} (выдано {emitted})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
