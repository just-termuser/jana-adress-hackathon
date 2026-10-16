"""Оценка по метрике жюри: посимвольное сравнение в lower case без пунктуации.

    python tools/eval.py tests/fixtures/sample_input.json tests/fixtures/sample_output.json
    python tools/eval.py --edge tests/fixtures/edge_cases.json

Группы весов взяты из условия: адрес 60%, детали 25%, справочные 15%.
Разбиение полей по группам — наше предположение (в ТЗ его нет):
адрес = city, street_name, house; детали = apartment, entrance, floor,
living_area, landmark; справочные = province, district, street_type.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jana_extract.parser import FIELDS
from jana_extract.service import extract_batch

GROUPS = {
    "address": (0.60, ["city", "street_name", "house"]),
    "details": (0.25, ["apartment", "entrance", "floor", "living_area", "landmark"]),
    "reference": (0.15, ["province", "district", "street_type"]),
}
_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)


def norm(v) -> str | None:
    if v is None:
        return None
    s = _PUNCT.sub(" ", str(v).lower())
    s = " ".join(s.split())
    return s or None


def evaluate(pred: list[dict], gold: list[dict], fields=FIELDS) -> dict:
    per = {f: [0, 0] for f in fields}
    for p, g in zip(pred, gold, strict=True):
        for f in fields:
            if f not in g:
                continue
            per[f][1] += 1
            per[f][0] += norm(p.get(f)) == norm(g[f])
    acc = {f: (ok / n if n else None) for f, (ok, n) in per.items()}
    score = 0.0
    wsum = 0.0
    for w, fs in GROUPS.values():
        vals = [acc[f] for f in fs if acc.get(f) is not None]
        if vals:
            score += w * sum(vals) / len(vals)
            wsum += w
    return {"per_field": acc, "weighted": score / wsum if wsum else None, "n": len(pred)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?")
    ap.add_argument("expected", nargs="?")
    ap.add_argument("--edge", help="файл вида [{raw, expect:{...}}]")
    ap.add_argument("--show-errors", action="store_true")
    args = ap.parse_args(argv)
    if args.edge:
        cases = json.loads(Path(args.edge).read_text(encoding="utf-8"))
        inp = [{"id": i, "raw_address": c["raw"]} for i, c in enumerate(cases)]
        gold = [c["expect"] for c in cases]
    else:
        inp = json.loads(Path(args.input).read_text(encoding="utf-8"))
        gold = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    pred = extract_batch(inp)
    res = evaluate(pred, gold)
    print(f"записей: {res['n']}")
    for f, a in res["per_field"].items():
        print(f"  {f:12s} {'—' if a is None else f'{a:.3f}'}")
    print(f"взвешенная точность: {res['weighted']:.3f}")
    if args.show_errors:
        for r, p, g in zip(inp, pred, gold, strict=True):
            bad = {f: (p.get(f), g[f]) for f in FIELDS if f in g and norm(p.get(f)) != norm(g[f])}
            if bad:
                print(r["raw_address"], bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
