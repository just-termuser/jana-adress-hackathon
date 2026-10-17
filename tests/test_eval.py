"""Симулятор метрики жюри и обязательные регрессионные пороги."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from eval import evaluate, field_outcome, norm, score_record

from jana_extract.parser import FIELDS
from jana_extract.service import extract_batch

FIX = Path(__file__).parent / "fixtures"


def test_norm_lower_no_punct_no_extra_spaces():
    assert norm("  Аксай-3а, ") == "аксай3а"
    assert norm("Аксай-3а", punct="space") == "аксай 3а"
    assert norm("Казыбек   би") == "казыбек би"
    assert norm(None) is None
    assert norm(" , ") is None


def test_typos_cases_and_yo_are_not_forgiven():
    assert norm("Казыбек би") != norm("казибек би")
    assert norm("Абая") != norm("Абай")
    assert norm("Королёва") != norm("Королева")


def test_numbers_compared_as_strings():
    assert field_outcome({"house": "1"}, {"house": "1/2"}, "house") is False
    assert field_outcome({"house": "1/2"}, {"house": "1/2"}, "house") is True
    assert field_outcome({"house": "12А"}, {"house": "12а"}, "house") is True


def test_both_empty_field_is_excluded():
    assert field_outcome({"floor": None}, {"floor": None}, "floor") is None
    assert field_outcome({"floor": "3"}, {"floor": None}, "floor") is False
    assert field_outcome({"floor": None}, {"floor": "3"}, "floor") is False
    assert field_outcome({"floor": "3"}, {}, "floor") is None  # поле не размечено


def test_group_weights_renormalised_to_present_groups():
    gold = dict.fromkeys(FIELDS)
    gold.update(city="Алматы", house="5")
    pred = dict(gold)
    assert score_record(pred, gold) == 1.0
    pred["house"] = "6"  # 1 из 2 полей адреса; других групп в записи нет
    assert score_record(pred, gold) == pytest.approx(0.5)
    gold["apartment"] = pred["apartment"] = "7"  # детали верны: 0.6*0.5 + 0.25*1
    assert score_record(pred, gold) == pytest.approx((0.6 * 0.5 + 0.25) / 0.85)


def test_sample_output_all_11_fields_exact():
    """Эталон организаторов: все 11 полей должны совпадать в точности."""
    inp = json.loads((FIX / "sample_input.json").read_text(encoding="utf-8"))
    gold = json.loads((FIX / "sample_output.json").read_text(encoding="utf-8"))
    pred = extract_batch(inp)
    for p, g in zip(pred, gold, strict=True):
        for f in FIELDS:
            assert p[f] == g[f], (f, p[f], g[f])  # строгое равенство, без нормализации


def test_holdout_accuracy_floor():
    cases = json.loads((FIX / "holdout_cases.json").read_text(encoding="utf-8"))
    pred = extract_batch([{"id": i, "raw_address": c["raw"]} for i, c in enumerate(cases)])
    res = evaluate(pred, [c["expect"] for c in cases], raws=[c["raw"] for c in cases])
    assert res["score"] >= 0.988, res  # трещотка: достигнуто 0.9882


def test_synth_corpus_gold_is_consistent():
    """Эталон синтетики: 11 полей, текстовые поля — подстроки raw."""
    cases = json.loads((FIX / "synth_corpus.json").read_text(encoding="utf-8"))
    assert len(cases) >= 1000
    for c in cases:
        assert set(c["expect"]) == set(FIELDS)
        for f in set(FIELDS) - {"province", "district", "street_type"}:
            v = c["expect"][f]
            assert v is None or v in c["raw"], (f, v, c["raw"])


def test_synth_accuracy_floor():
    """Трещотка на синтетике (район — только где он есть в строке, как в ТЗ)."""
    cases = json.loads((FIX / "synth_corpus.json").read_text(encoding="utf-8"))
    pred = extract_batch([{"id": c["id"], "raw_address": c["raw"]} for c in cases])
    res = evaluate(pred, [c["expect"] for c in cases], raws=[c["raw"] for c in cases])
    assert res["score"] >= 0.9904, res  # трещотка: достигнуто 0.99045 (волна 1, класс 4)


def test_district_counted_only_where_present_in_text():
    gold = dict.fromkeys(FIELDS)
    gold.update(city="Алматы", house="5", district="Ауэзовский")
    pred = dict(gold, district=None)
    raw_without = "г. Алматы, ул. Абая 5"
    raw_with = "г. Алматы, Ауэзовский р-н, ул. Абая 5"
    assert evaluate([pred], [gold], raws=[raw_without])["score"] == 1.0
    assert evaluate([pred], [gold], raws=[raw_with])["score"] < 1.0
    assert evaluate([pred], [gold], raws=[raw_without], district_scope="all")["score"] < 1.0


def test_district_restoration_precision_floor():
    """Восстановленный район (для защиты): точность важнее полноты."""
    from eval import district_restoration

    cases = json.loads((FIX / "synth_corpus.json").read_text(encoding="utf-8"))
    pred = extract_batch([{"id": c["id"], "raw_address": c["raw"]} for c in cases])
    dr = district_restoration(pred, [c["expect"] for c in cases], [c["raw"] for c in cases])
    assert dr["precision"] >= 0.99, dr  # достигнуто 0.990
    assert dr["coverage"] >= 0.9176, dr  # достигнуто 0.91770


def test_synth_landmark_floor():
    """Ориентир вне весов ТЗ, но точность поля держим трещоткой (волна 1, класс 5)."""
    cases = json.loads((FIX / "synth_corpus.json").read_text(encoding="utf-8"))
    pred = extract_batch([{"id": c["id"], "raw_address": c["raw"]} for c in cases])
    res = evaluate(pred, [c["expect"] for c in cases], raws=[c["raw"] for c in cases])
    assert res["per_field"]["landmark"] >= 1.0, res["per_field_counts"]["landmark"]
