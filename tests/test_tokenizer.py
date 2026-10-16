import pytest

from jana_extract import markers as M
from jana_extract.tokenizer import NUM, ORD, WORD, tokenize


def kinds(raw):
    return [(t.text, t.kind) for t in tokenize(raw).tokens]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("66квартал", [("66", NUM), ("квартал", WORD)]),
        ("Назарбаева8", [("Назарбаева", WORD), ("8", NUM)]),
        ("11дом", [("11", NUM), ("дом", WORD)]),
        ("4подьезд", [("4", NUM), ("подьезд", WORD)]),
        ("кв70", [("кв", WORD), ("70", NUM)]),
        ("12а", [("12а", NUM)]),
        ("1/2", [("1/2", NUM)]),
        ("33/1", [("33/1", NUM)]),
        ("2-й", [("2", NUM), ("й", ORD)]),
        ("Аль-Фараби", [("Аль-Фараби", WORD)]),
    ],
)
def test_glued_and_numbers(raw, expected):
    assert kinds(raw) == expected


def test_offsets_are_raw_substrings():
    raw = "г.Алматы,​мкр.Аксай-3а,д.59,кв.70"
    tk = tokenize(raw)
    for t in tk.tokens:
        assert raw[t.start : t.end] == t.text


def test_invisible_inside_word_keeps_offsets():
    raw = "Аба​я 5"
    tk = tokenize(raw)
    word = tk.tokens[0]
    assert word.kind == WORD
    assert raw[word.start : word.end] == "Аба​я"


def test_glued_flag():
    tk = tokenize("11дом 5 подьезд")
    dom = tk.tokens[1]
    assert dom.marker == M.HOUSE and dom.glued_prev
    assert not tk.tokens[3].glued_prev


def test_markers_ru_kk_lat():
    tk = tokenize("flat 191 kv 12 70 пәтер 3 қабат шағын ауданы")
    m = {t.text: t.marker for t in tk.tokens if t.marker}
    assert m["flat"] == M.APT
    assert m["kv"] == M.APT
    assert m["пәтер"] == M.APT
    assert m["қабат"] == M.FLOOR
    assert m["шағын"] == M.AREA


def test_bilingual_slash_marks_kazakh_side():
    tk = tokenize("город Актобе/Ақтөбе қаласы, ул Абая 5")
    alt = [t.text for t in tk.tokens if t.alt]
    assert alt == ["Ақтөбе", "қаласы"]


def test_bilingual_kazakh_left():
    tk = tokenize("Ақтөбе қаласы/город Актобе")
    alt = [t.text for t in tk.tokens if t.alt]
    assert alt == ["Ақтөбе", "қаласы"]


def test_fraction_is_not_bilingual():
    tk = tokenize("Абая 33/1")
    assert not any(t.alt for t in tk.tokens)
