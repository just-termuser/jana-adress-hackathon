import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jana_extract.api import app
from jana_extract.service import OUTPUT_KEYS

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_sample_batch_matches_expected(client):
    inp = json.loads((FIX / "sample_input.json").read_text(encoding="utf-8"))
    exp = json.loads((FIX / "sample_output.json").read_text(encoding="utf-8"))
    out = client.post("/parse/batch", json=inp).json()
    assert len(out) == len(exp)
    for got, want in zip(out, exp, strict=True):
        assert list(got) == list(OUTPUT_KEYS)
        for k in OUTPUT_KEYS:
            if k != "confidence":
                assert got[k] == want[k], k
        assert 0 <= got["confidence"] <= 1


def test_batch_never_skips_records(client):
    payload = [
        {"id": 1, "raw_address": "г. Алматы, ул. Абая 5"},
        {"id": 2, "raw_address": ""},
        {"id": 3},
        {"id": 4, "raw_address": None},
        {"id": 5, "raw_address": 12345},
        "строка вместо объекта",
        {"id": 7, "raw_address": "​​"},
    ]
    out = client.post("/parse/batch", json=payload).json()
    assert len(out) == len(payload)
    assert [r["id"] for r in out] == [1, 2, 3, 4, 5, None, 7]
    assert out[0]["house"] == "5"
    assert out[3]["confidence"] == 0


def test_batch_wrapped_records_and_debug(client):
    out = client.post(
        "/parse/batch?debug=true", json={"records": [{"id": "a", "raw_address": "кв 70"}]}
    ).json()
    assert out[0]["apartment"] == "70"
    assert "_meta" in out[0]


def test_batch_rejects_non_list(client):
    assert client.post("/parse/batch", json={"foo": 1}).status_code == 422


def test_parse_one(client):
    r = client.post("/parse", json={"id": "x", "raw_address": "Назарбаева8"}).json()
    assert r["street_name"] == "Назарбаева" and r["house"] == "8"


def test_file_upload_json_and_csv(client):
    data = (FIX / "sample_input.json").read_bytes()
    out = client.post("/parse/file", files={"file": ("in.json", data)}).json()
    assert out[0]["street_name"] == "Аксай 3а"
    csv_in = 'id,raw_address\nr1,"Алматы, Толе би 12"\n'.encode()
    r = client.post("/parse/file?format=csv", files={"file": ("in.csv", csv_in)})
    assert r.status_code == 200
    assert "Толе би" in r.text


def test_reference_info(client):
    info = client.get("/reference/info").json()
    assert info["counts"]["regions"] == 20
