"""Волна 4: тип улицы из справочника, когда его нет в строке. Тесты — ДО реализации."""

import pytest

from jana_extract.parser import parse_address
from jana_extract.service import extract_record

NOT_YET = pytest.mark.xfail(strict=True, reason="волна 4: вывод типа улицы ещё не реализован")


def todo(*args):
    return pytest.param(*args, marks=NOT_YET)


@pytest.mark.parametrize(
    ("raw", "street", "street_type"),
    [
        todo("г. Алматы, жуалы 19", "жуалы", "улица"),
        todo("г. Алматы, Назарбаева 8, кв 3", "Назарбаева", "проспект"),
        todo("г. Астана, Республики 1", "Республики", "проспект"),
        # один и тот же ключ — разный тип в разных городах
        todo("г. Астана, Кабанбай батыра 53", "Кабанбай батыра", "проспект"),
        todo("г. Алматы, Кабанбай батыра 53", "Кабанбай батыра", "улица"),
    ],
)
def test_street_type_inferred_from_reference(raw, street, street_type):
    v = parse_address(raw).values
    assert (v["street_name"], v["street_type"]) == (street, street_type)


def test_written_type_wins():
    assert parse_address("г. Алматы, ул. Назарбаева 8").values["street_type"] == "улица"


@NOT_YET
def test_ambiguous_type_is_null_with_candidates():
    rec = extract_record({"id": 1, "raw_address": "г. Алматы, Абая 10"}, debug=True)
    assert rec["street_type"] is None  # проспект 22 / улица 3: доля 0.88 < 0.9
    cands = rec["_meta"]["ambiguity"]["street_type"]
    assert any(c.startswith("проспект") for c in cands)


def test_too_few_occurrences_is_null():
    assert parse_address("Кулманова 133").values["street_type"] is None  # 1 вхождение < 3


@NOT_YET
def test_inferred_type_explained_in_debug():
    rec = extract_record({"id": 1, "raw_address": "г. Алматы, жуалы 19"}, debug=True)
    inf = rec["_meta"]["inferred"]["street_type"]
    assert inf["value"] == "улица" and inf["source"].startswith("reference:")
    assert inf["share"] >= 0.9 and inf["n"] >= 3


@NOT_YET
def test_flag_disables_inference():
    v = parse_address("г. Алматы, жуалы 19", infer_street_type=False).values
    assert v["street_type"] is None and v["street_name"] == "жуалы"
