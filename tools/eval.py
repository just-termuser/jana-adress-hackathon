"""Симулятор метрики жюри.

    python tools/eval.py tests/fixtures/sample_input.json tests/fixtures/sample_output.json
    python tools/eval.py --cases tests/fixtures/holdout_cases.json --show-errors
    python tools/eval.py --cases data/synth/synth_corpus.json

Правила (из ТЗ):
- каждое поле сравнивается отдельно, посимвольно, как строка («1/2» ≠ «1»);
- нижний регистр, без пунктуации, лишние пробелы схлопнуты;
- падежи, опечатки, ё/е НЕ прощаются;
- поле, которое пусто и в эталоне, и у нас, из подсчёта исключается;
- веса групп: область/город/улица/дом — 60%, квартира/подъезд/этаж — 25%,
  район/жилой массив — 15%; группа участвует, только если в записи есть хотя бы
  одно её поле (веса перенормируются).

Открытые вопросы к правилам, вынесенные в параметры:
- «убрана пунктуация»: символ удаляется (`--punct delete`, по умолчанию) или
  заменяется пробелом (`--punct space`). Разница видна на «Аксай-3а» / «1/2».
- street_type и landmark в группах ТЗ не названы: по умолчанию они считаются
  отдельно и в итог не входят; `--alt-groups` включает их (street_type — в адрес,
  landmark — в детали), чтобы увидеть чувствительность итога.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jana_extract.parser import FIELDS  # noqa: E402

OFFICIAL_GROUPS: dict[str, tuple[float, list[str]]] = {
    "address": (0.60, ["province", "city", "street_name", "house"]),
    "details": (0.25, ["apartment", "entrance", "floor"]),
    "reference": (0.15, ["district", "living_area"]),
}
ALT_GROUPS: dict[str, tuple[float, list[str]]] = {
    "address": (0.60, ["province", "city", "street_type", "street_name", "house"]),
    "details": (0.25, ["apartment", "entrance", "floor", "landmark"]),
    "reference": (0.15, ["district", "living_area"]),
}

_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)


def norm(v, punct: str = "delete") -> str | None:
    """Нормализация жюри: lower, без пунктуации, без лишних пробелов. Больше ничего."""
    if v is None:
        return None
    s = str(v).lower()
    s = _PUNCT.sub("" if punct == "delete" else " ", s)
    s = " ".join(s.split())
    return s or None


def field_outcome(pred, gold, field: str, punct: str = "delete") -> bool | None:
    """True/False — поле засчитано/нет; None — поле не участвует в подсчёте."""
    if field not in gold:  # частичная разметка: поле не размечено
        return None
    g, p = norm(gold[field], punct), norm(pred.get(field), punct)
    if g is None and p is None:
        return None
    return g == p


def score_record(pred: dict, gold: dict, groups=OFFICIAL_GROUPS, punct="delete") -> float | None:
    total_w, acc = 0.0, 0.0
    for w, fields in groups.values():
        outs = [o for f in fields if (o := field_outcome(pred, gold, f, punct)) is not None]
        if outs:
            total_w += w
            acc += w * sum(outs) / len(outs)
    return acc / total_w if total_w else None


def evaluate(pred: list[dict], gold: list[dict], groups=OFFICIAL_GROUPS, punct="delete") -> dict:
    per = {f: [0, 0] for f in FIELDS}
    rec_scores = []
    for p, g in zip(pred, gold, strict=True):
        for f in FIELDS:
            o = field_outcome(p, g, f, punct)
            if o is not None:
                per[f][1] += 1
                per[f][0] += o
        s = score_record(p, g, groups, punct)
        if s is not None:
            rec_scores.append(s)
    return {
        "n": len(pred),
        "per_field": {f: (ok / n if n else None) for f, (ok, n) in per.items()},
        "per_field_counts": {f: tuple(v) for f, v in per.items()},
        "score": sum(rec_scores) / len(rec_scores) if rec_scores else None,
        "exact_records": sum(1 for s in rec_scores if s == 1.0) / len(rec_scores) if rec_scores else None,
    }


def load_cases(args) -> tuple[list[dict], list[dict]]:
    if args.cases:
        cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
        inp = [{"id": c.get("id", i), "raw_address": c["raw"]} for i, c in enumerate(cases)]
        return inp, [c["expect"] for c in cases]
    inp = json.loads(Path(args.input).read_text(encoding="utf-8"))
    gold = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    return inp, gold


def report(res: dict, title: str) -> None:
    print(title)
    for f in FIELDS:
        a = res["per_field"][f]
        ok, n = res["per_field_counts"][f]
        print(f"  {f:12s} {'—' if a is None else f'{a:.3f}'}  ({ok}/{n})")
    print(f"  итог (среднее по записям): {res['score']:.4f}; записей без ошибок: {res['exact_records']:.3f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Симулятор метрики жюри")
    ap.add_argument("input", nargs="?")
    ap.add_argument("expected", nargs="?")
    ap.add_argument("--cases", "--edge", dest="cases", help="файл [{raw, expect:{...}}]")
    ap.add_argument("--punct", choices=["delete", "space"], default="delete")
    ap.add_argument("--alt-groups", action="store_true")
    ap.add_argument("--show-errors", action="store_true")
    args = ap.parse_args(argv)

    from jana_extract.service import extract_batch

    inp, gold = load_cases(args)
    pred = extract_batch(inp)
    groups = ALT_GROUPS if args.alt_groups else OFFICIAL_GROUPS
    print(f"записей: {len(pred)}; punct={args.punct}; группы={'alt' if args.alt_groups else 'ТЗ'}")
    report(evaluate(pred, gold, groups, args.punct), "")
    other = evaluate(pred, gold, OFFICIAL_GROUPS if args.alt_groups else ALT_GROUPS, args.punct)
    other_p = evaluate(pred, gold, groups, "space" if args.punct == "delete" else "delete")
    print(f"  чувствительность: другие группы → {other['score']:.4f}; другая пунктуация → {other_p['score']:.4f}")
    if args.show_errors:
        for r, p, g in zip(inp, pred, gold, strict=True):
            bad = {f: (p.get(f), g[f]) for f in FIELDS if field_outcome(p, g, f, args.punct) is False}
            if bad:
                print(" ", r["raw_address"], bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
