"""Волна 4, остаток: ЖК с номером и сёла КАТО без маркера при известной области.

Тесты написаны ДО правки.
"""

import pytest

from jana_extract.parser import parse_address

NOT_YET = pytest.mark.xfail(strict=True, reason="волна 4: ещё не исправлено")


def todo(*args):
    return pytest.param(*args, marks=NOT_YET)


def fields(raw, *names):
    v = parse_address(raw).values
    return tuple(v[n] for n in names)


# ------------------------------------------- ЖК с номером


@pytest.mark.parametrize(
    ("raw", "living", "street", "house"),
    [
        todo(
            'г. Алматы, ЖК "Калкаман 2", Брянская, д. 221, kv 62', "Калкаман 2", "Брянская", "221"
        ),
        todo('г.Шымкент,ЖК "Ынтымак 2",ул. Мукуми 59,кв. 41', "Ынтымак 2", "Мукуми", "59"),
        todo("Алматы,ЖК Жетысу 2,ул. Ужгород,37/2,кв. 88", "Жетысу 2", "Ужгород", "37/2"),
        todo("Боралдай, жк Ветеран 2, Егемен 184, kv 99", "Ветеран 2", "Егемен", "184"),
    ],
)
def test_zhk_with_number(raw, living, street, house):
    assert fields(raw, "living_area", "street_name", "house") == (living, street, house)


@pytest.mark.parametrize(
    ("raw", "living", "house"),
    [
        ("ЖК Нурлы Тау, д 5, кв 4", "Нурлы Тау", "5"),  # номер с маркером — дом
        ("г. Алматы, ЖК Хайвил, ул. Ахмедьярова 1, кв 10", "Хайвил", "1"),
    ],
)
def test_zhk_without_number_unchanged(raw, living, house):
    assert fields(raw, "living_area", "house") == (living, house)


# ------------------------------------------- сёла КАТО без маркера при известной области


@pytest.mark.parametrize(
    ("raw", "city", "street"),
    [
        ("Абайская область, Кулжабас, мкр 15, дом 24, этаж 12", "Кулжабас", "15"),
        ("Алматы облысы, Темирлик, 6 микрорайон, 155, 119 кв", "Темирлик", "6"),
        ("Атырауская обл., Жылыойский р-н, Жаманшабай, мкр 9, д.1/2", "Жаманшабай", "9"),
    ],
)
def test_kato_village_without_marker_when_region_known(raw, city, street):
    assert fields(raw, "city", "street_name") == (city, street)


def test_kato_village_without_region_is_not_a_city():
    """Без области в строке село из КАТО без маркера город не перехватывает."""
    assert fields("Кулжабас, ул. Абая 5", "street_name") == ("Абая",)
