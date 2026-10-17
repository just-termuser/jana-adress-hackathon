"""Волна 1: классы 2, 4, 5 из таксономии (reports/taxonomy_baseline.md).

Тесты написаны ДО правки; примеры — живые записи синтетики плюс ручные.
"""

import pytest

from jana_extract.parser import parse_address

NOT_YET = pytest.mark.xfail(strict=True, reason="волна 1: ещё не исправлено")


def todo(*args):
    return pytest.param(*args, marks=NOT_YET)


def fields(raw, *names):
    v = parse_address(raw).values
    return tuple(v[n] for n in names)


# ------------------------------------------- класс 4: улица без маркера перед помеченным домом


@pytest.mark.parametrize(
    ("raw", "street", "house"),
    [
        todo(
            "Алматинская область, Талғар ауданы, с. Кербулак, Попова, д. 210, кв 107",
            "Попова",
            "210",
        ),
        todo("г.Нарынкол Валиханва д. 50/2", "Валиханва", "50/2"),
        todo("город Астана Генерала Сабыра Ракымова д. 2 кв168", "Генерала Сабыра Ракымова", "2"),
        todo("город Астана, Ушкара, 116дом", "Ушкара", "116"),
        todo("г.Астана Жузим 98адом kv 298", "Жузим", "98а"),
    ],
)
def test_unmarked_street_before_marked_house(raw, street, house):
    assert fields(raw, "street_name", "house") == (street, house)


# ------------------------------------------- класс 2: хвост в названии при отсутствии запятых


@pytest.mark.parametrize(
    ("raw", "city", "street", "house"),
    [
        todo(
            "Жамбылская обл Айша-Биби Гагарин Бурылысы д. 118а 10 подъезд",
            "Айша-Биби",
            "Гагарин Бурылысы",
            "118а",
        ),
        todo("Кызылординская обл Байконур Ибраимов даңғылы 96 214", "Байконур", "Ибраимов", "96"),
        todo(
            "Северо-Казахстанская обл. Красноярка Кырыкуй микрорайон 31дом 5 подьезд 135 кв",
            "Красноярка",
            "Кырыкуй",
            "31",
        ),
        todo("шымкент г абылай хана д.153 кв321", "шымкент", "абылай хана", "153"),
    ],
)
def test_no_commas_city_is_not_glued_into_street(raw, city, street, house):
    assert fields(raw, "city", "street_name", "house") == (city, street, house)


@NOT_YET
def test_no_commas_ordinal_microdistrict_before_marker():
    raw = "Туркестанская обл. Мактааральский район село Ииржар 7-й микрорайон 73а кв 111"
    assert fields(raw, "street_name", "house", "apartment") == ("7", "73а", "111")


@NOT_YET
def test_no_commas_zhk_name_split_by_reference():
    raw = "Шымкент ЖК Армандастар Сандыккол дом 180 кв. 232"
    assert fields(raw, "living_area", "street_name", "house") == ("Армандастар", "Сандыккол", "180")


# ------------------------------------------- класс 5: ориентир в падеже / с предлогом


@pytest.mark.parametrize(
    ("raw", "landmark"),
    [
        todo("г. Астана, пер. Бекет Ата, 207, кв150, за мечетью", "за мечетью"),
        todo("г. Шымкент, ул. Жолдасова, д. 219, за мечетью", "за мечетью"),
        ("Алматы, ул. Абая 10, возле школы №45", "возле школы №45"),
        ("Алматы, напротив аптеки, ул. Абая 10", "напротив аптеки"),
        ("Костанай, пр. Абая 5, кв 3, около больницы", "около больницы"),
    ],
)
def test_landmark_inflected_with_preposition(raw, landmark):
    assert parse_address(raw).values["landmark"] == landmark


@NOT_YET
def test_landmark_does_not_leak_into_street_without_commas():
    raw = "Аксюат улица Аккожа Би 178 за мечетью"
    assert fields(raw, "street_name", "house", "landmark") == ("Аккожа Би", "178", "за мечетью")


def test_landmark_before_street_keeps_street():
    raw = "Алматы, напротив аптеки, ул. Абая 10"
    assert fields(raw, "street_name", "house") == ("Абая", "10")


@pytest.mark.parametrize(
    "raw", ["г. Алматы, ул. Школьная 5", "Костанай, ул. Аптечная 3", "Алматы, Магазинный пер. 2"]
)
def test_street_named_like_landmark_is_not_landmark(raw):
    v = parse_address(raw).values
    assert v["landmark"] is None and v["street_name"] is not None
