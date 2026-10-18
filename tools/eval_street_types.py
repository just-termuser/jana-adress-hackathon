"""Независимая проверка вывода типа улицы на отложенных адресах OSM.

    python tools/eval_street_types.py

tests/fixtures/street_type_holdout.json — адреса `addr:street` с явно записанным
типом, которые НЕ входили в счётчики таблицы типов (см. build_street_types.py).
Тип отрезается (ключ названия без типа — `core()`), предсказывается по
справочнику с областью адреса и сравнивается с записанным.

Покрытие — доля адресов, где тип выдан; точность — доля верных среди выданных.
Для сравнения — наивная база «всегда улица» (точность при покрытии 100%).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jana_extract.reference import get_reference  # noqa: E402
from jana_extract.text import core  # noqa: E402


def evaluate(cases: list[dict]) -> dict:
    ref = get_reference()
    emitted = correct = ambiguous = no_data = 0
    by_source: Counter = Counter()
    by_source_ok: Counter = Counter()
    confusion: Counter = Counter()
    for c in cases:
        info = ref.infer_street_type(core(c["raw"]), c["region"])
        if info is None:
            no_data += 1
            continue
        if info["value"] is None:
            ambiguous += 1
            continue
        level = (
            "город"
            if info["source"].endswith(("Алматы", "Астана", "Шымкент"))
            else ("страна" if info["source"].endswith("Казахстан") else "область")
        )
        emitted += 1
        by_source[level] += 1
        ok = info["value"] == c["type"]
        correct += ok
        by_source_ok[level] += ok
        if not ok:
            confusion[(c["type"], info["value"])] += 1
    n = len(cases)
    baseline = sum(c["type"] == "улица" for c in cases) / n
    return {
        "n": n,
        "coverage": emitted / n,
        "precision": correct / emitted if emitted else None,
        "ambiguous": ambiguous / n,
        "no_data": no_data / n,
        "by_source": {k: (by_source[k], by_source_ok[k] / by_source[k]) for k in by_source},
        "confusion": confusion.most_common(8),
        "baseline_always_ulitsa": baseline,
        "true_types": Counter(c["type"] for c in cases).most_common(),
    }


def main() -> int:
    cases = json.loads(
        (ROOT / "tests/fixtures/street_type_holdout.json").read_text(encoding="utf-8")
    )
    r = evaluate(cases)
    print(f"отложенных адресов: {r['n']}")
    print(f"  покрытие {r['coverage']:.3f}, точность {r['precision']:.4f}")
    print(f"  неоднозначно (null + кандидаты) {r['ambiguous']:.3f}, нет данных {r['no_data']:.3f}")
    for k, (cnt, acc) in sorted(r["by_source"].items()):
        print(f"  уровень «{k}»: {cnt} адресов, точность {acc:.4f}")
    print(f"  база «всегда улица»: точность {r['baseline_always_ulitsa']:.4f} при покрытии 1.0")
    print(f"  истинные типы: {r['true_types']}")
    print(f"  частые ошибки (было → выдали): {r['confusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
