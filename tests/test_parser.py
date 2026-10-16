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
