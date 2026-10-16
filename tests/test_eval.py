"""Регрессионный порог точности на отложенном наборе (ручная разметка по ТЗ)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from eval import evaluate, norm

from jana_extract.service import extract_batch

FIX = Path(__file__).parent / "fixtures"


def test_jury_normalisation():
    assert norm("Аксай-3а,") == "аксай 3а"
    assert norm("Казыбек би") != norm("казибек би")  # опечатки не прощаются
    assert norm(None) is None


def test_holdout_accuracy_floor():
    cases = json.loads((FIX / "holdout_cases.json").read_text(encoding="utf-8"))
    pred = extract_batch([{"id": i, "raw_address": c["raw"]} for i, c in enumerate(cases)])
    res = evaluate(pred, [c["expect"] for c in cases])
    assert res["weighted"] >= 0.97, res
