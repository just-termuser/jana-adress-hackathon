"""Волна 2: классы 11 и 8 из таксономии. Тесты написаны ДО правки."""

import pytest

from jana_extract.parser import parse_address

# ------------------------------------------- класс 11: казахские многословные области


@pytest.mark.parametrize(
    ("raw", "province"),
    [
        (
            "Батыс Қазақстан облысы, Аксай, проспект Космонавттар, дом 3/4, 18 қабат",
            "Западно-Казахстанская область",
        ),
        (
            "Шығыс Қазақстан облысы, город Усть-Каменогорск, ул. Казахстан 59",
            "Восточно-Казахстанская область",
        ),
        (
            "Оңтүстік Қазақстан облысы с. Достық ул. Сарбасова д. 69 кв 209",
            "Туркестанская область",
        ),
        (
            "Солтүстік Қазақстан облысы, Петропавл қаласы, Жамбыл көшесі 160",
            "Северо-Казахстанская область",
        ),
    ],
)
def test_kazakh_multiword_region(raw, province):
    res = parse_address(raw)
    assert res.values["province"] == province
    assert not res.leftovers, res.leftovers  # «Батыс»/«Шығыс» не остаются хвостом


def test_kazakh_multiword_region_without_city_still_gives_province():
    v = parse_address("Батыс Қазақстан облысы, Бәйтерек ауданы, Абай көшесі 5").values
    assert v["province"] == "Западно-Казахстанская область"


@pytest.mark.parametrize(
    "raw", ["Республика Казахстан, г. Алматы, ул. Абая 5", "Казахстан, Алматы, Абая 5"]
)
def test_country_word_still_noise(raw):
    res = parse_address(raw)
    assert res.values["city"] == "Алматы" and not res.leftovers


# ------------------------------------------- класс 8: опечатка в городе рядом с маркером


@pytest.mark.parametrize(
    ("raw", "city", "province"),
    [
        ("050000, Астона г, Асемконыр Орамы, 38/6, кв 158", "Астона", "Астана"),
        ("Азтана г, мкр Саранда, д. 96, 6 подъезд", "Азтана", "Астана"),
        ("Байконир г, ул.Первомайск, д. 105, flat 173", "Байконир", "Кызылординская область"),
        ("Тикели г, 20 микрорайон, 152, кв 275", "Тикели", "Жетысуская область"),
        ("г. Алмоты, ул. Абая 5", "Алмоты", "Алматы"),
    ],
)
def test_city_typo_next_to_marker(raw, city, province):
    v = parse_address(raw).values
    assert (v["city"], v["province"]) == (city, province)


@pytest.mark.parametrize("raw", ["Алматы, Абая 5 г", "г. Алматы, ул. Абая 5"])
def test_fuzzy_city_does_not_eat_street(raw):
    v = parse_address(raw).values
    assert v["city"] == "Алматы" and v["street_name"] == "Абая"
