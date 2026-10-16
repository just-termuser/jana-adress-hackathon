import json
from pathlib import Path

import pytest

from jana_extract.parser import FIELDS, parse_address

FIX = Path(__file__).parent / "fixtures"
CASES = json.loads((FIX / "edge_cases.json").read_text(encoding="utf-8"))
SUBSTRING_FIELDS = set(FIELDS) - {"province", "district", "street_type"}


@pytest.mark.parametrize("case", CASES, ids=[c["raw"][:40] for c in CASES])
def test_edge_case(case):
    got = parse_address(case["raw"]).values
    diff = {k: (got[k], v) for k, v in case["expect"].items() if got[k] != v}
    assert not diff, f"got != expected: {diff}"


@pytest.mark.parametrize("case", CASES, ids=[c["raw"][:40] for c in CASES])
def test_text_fields_are_raw_substrings(case):
    raw = case["raw"]
    res = parse_address(raw)
    for f in SUBSTRING_FIELDS:
        v = res.values[f]
        if v is not None:
            assert v in raw, f"{f}={v!r} is not a substring of {raw!r}"
            assert v.strip() == v and v


def test_one_and_one_half_are_different_houses():
    assert parse_address("ул Абая 1").values["house"] == "1"
    assert parse_address("ул Абая 1/2").values["house"] == "1/2"


@pytest.mark.parametrize("raw", ["", "   ", ",,,", "​", "кв", "123", "????"])
def test_garbage_never_crashes(raw):
    res = parse_address(raw)
    assert set(res.values) == set(FIELDS)
    assert 0 <= res.confidence <= 1


def test_house_literal_glued_to_marker():
    v = parse_address("Нурсат микрорайон 21адом 9 подьезд 133 кв").values
    assert (v["street_name"], v["house"], v["entrance"], v["apartment"]) == (
        "Нурсат",
        "21а",
        "9",
        "133",
    )


def test_kazakh_half_without_comma_ends_at_marker():
    v = parse_address("город Астана/Астана қаласы Толе би 19/4 kv 204").values
    assert (v["city"], v["street_name"], v["house"], v["apartment"]) == (
        "Астана",
        "Толе би",
        "19/4",
        "204",
    )


def test_kazakh_street_half_keeps_house():
    v = parse_address("г. Алматы, ул. Абая/Абай көшесі 5, кв 3").values
    assert (v["street_name"], v["house"], v["apartment"]) == ("Абая", "5", "3")


def test_country_word_as_street_name():
    v = parse_address("Усть-Каменогорск, ул. Казахстан 59, кв. 101").values
    assert (v["street_type"], v["street_name"], v["house"]) == ("улица", "Казахстан", "59")
    assert parse_address("Республика Казахстан, г. Алматы, Абая 5").values["street_name"] == "Абая"
