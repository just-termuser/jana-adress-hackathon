"""Волна 3: классы 6, 2 (остаток), 8 (без маркера), 10. Тесты написаны ДО правки."""

import pytest

from jana_extract.parser import parse_address
from jana_extract.service import extract_record

NOT_YET = pytest.mark.xfail(strict=True, reason="волна 3: ещё не исправлено")


def todo(*args):
    return pytest.param(*args, marks=NOT_YET)


def fields(raw, *names):
    v = parse_address(raw).values
    return tuple(v[n] for n in names)


# ------------------------------------------- класс 10: дубль города


@pytest.mark.parametrize(
    ("raw", "street_type", "street", "living", "house"),
    [
        ("г. Шымкент, Шымкент, мкр 28, 218, 1 этаж", "микрорайон", "28", None, "218"),
        ("Актюбинская обл., Эмба, г. Эмба, 26 мкр, 14", "микрорайон", "26", None, "14"),
        (
            "город Кушмурун / Құсмұрын қаласы,Кушмурун,Айтей мкр,75",
            "микрорайон",
            "Айтей",
            None,
            "75",
        ),
        (
            "Жангизтобе г. Жангизтобе Уалихонов даңғылы дом 81/9 кв. 39",
            "проспект",
            "Уалихонов",
            None,
            "81/9",
        ),
        ("г. Астана Астана Сарын 192 кв 250", None, "Сарын", None, "192"),
    ],
)
def test_duplicated_city_is_not_a_street(raw, street_type, street, living, house):
    assert fields(raw, "street_type", "street_name", "living_area", "house") == (
        street_type,
        street,
        living,
        house,
    )


def test_street_named_like_city_stays_street():
    assert fields("Астана, ул. Астана 5", "city", "street_name") == ("Астана", "Астана")


# ------------------------------------------- класс 6: одноимённые НП и районы


@pytest.mark.parametrize(
    ("raw", "province"),
    [
        todo("абайский район,мкр финансист,д. 87/2,apt 76", "Шымкент"),
        todo("абайский район,едил каган131", "Шымкент"),
        todo("Есиль р-н, микрорайон Чубары, пр. Кима 72, кв 142", "Астана"),
        todo("г.Аксу, Касымхана211", "Акмолинская область"),
        todo("Актау, мкр. Токабай, ул. Златоустская, д. 154, кв. 286", "Карагандинская область"),
    ],
)
def test_namesake_resolved_by_street_or_microdistrict(raw, province):
    assert parse_address(raw).values["province"] == province


@NOT_YET
def test_district_namesake_resolved_keeps_reference_name():
    assert fields("Есиль р-н, микрорайон Чубары, пр. Кима 72", "district") == ("Есиль",)


@NOT_YET
def test_region_from_district_comes_before_city_search():
    raw = "Ауэзовский р-н, Аксай 3 микрорайон 8дом 1 подьезд 14 кв"
    assert fields(raw, "province", "city", "street_name", "house") == (
        "Алматы",
        None,
        "Аксай 3",
        "8",
    )


@NOT_YET
def test_unresolvable_namesake_is_null_and_explained():
    rec = extract_record(
        {"id": 1, "raw_address": "Абайский район, ул. Корганбай Омешулы, д. 126"}, debug=True
    )
    assert rec["province"] is None
    assert rec["district"] == "Абайский"  # район есть в строке, имя одинаковое у всех кандидатов
    cands = rec["_meta"]["ambiguity"]["province"]
    assert {"Шымкент", "Карагандинская область", "Абайская область"} <= set(cands)


@NOT_YET
def test_dominant_namesake_is_kept_but_flagged():
    rec = extract_record({"id": 1, "raw_address": "Аксу, ул. Абая 5"}, debug=True)
    assert rec["province"] == "Павлодарская область"  # город Аксу против сёл Аксу
    assert "Акмолинская область" in rec["_meta"]["ambiguity"]["province"]
    plain = extract_record({"id": 2, "raw_address": "Павлодар, ул. Абая 5"})
    assert rec["confidence"] < plain["confidence"]


# ------------------------------------------- класс 2 (остаток): без запятых


@pytest.mark.parametrize(
    ("raw", "city", "street", "house"),
    [
        todo("проспект Ваганная 69 Алга", "Алга", "Ваганная", "69"),
        todo("Шымкент г Жертумсык микрорайон 51дом 1 подъезд 243 кв", "Шымкент", "Жертумсык", "51"),
        todo("Казахстан,Темиртау ул.,35вдом,278,Шымкент", "Шымкент", "Темиртау", "35в"),
        todo("Каркаралинская, д.34а, кв 123, Шар", "Шар", "Каркаралинская", "34а"),
        todo("Байконур р-н Шортанды ул. 192 108 пәтер 8подьезд 9 этаж", None, "Шортанды", "192"),
    ],
)
def test_no_commas_rest(raw, city, street, house):
    assert fields(raw, "city", "street_name", "house") == (city, street, house)


def test_number_then_word_street_still_works():
    assert fields("Атырау, ул. 8 Марта 12", "street_name", "house") == ("8 Марта", "12")


# ------------------------------------------- класс 8: опечатка в городе без маркера


@pytest.mark.parametrize(
    ("raw", "city", "province", "street"),
    [
        todo(
            "Каратоу Атырау 3 микрорайон 6дом 9 подъезд 45 кв",
            "Каратоу",
            "Жамбылская область",
            "Атырау 3",
        ),
        todo("Алмоты Аккайын д. 158 kv 138", "Алмоты", "Алматы", "Аккайын"),
        todo("Шимкент Тауке хана 45", "Шимкент", "Шымкент", "Тауке хана"),
    ],
)
def test_city_typo_without_marker(raw, city, province, street):
    assert fields(raw, "city", "province", "street_name") == (city, province, street)


@pytest.mark.parametrize(
    ("raw", "street"),
    [("Сатпаева 5, кв 3", "Сатпаева"), ("Жансугурова 12, кв 4", "Жансугурова")],
)
def test_fuzzy_city_does_not_intercept_known_street(raw, street):
    assert fields(raw, "city", "street_name") == (None, street)
