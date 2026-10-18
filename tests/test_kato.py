"""Волна 4, КАТО: районы городов областного значения и официальные названия.

Тесты написаны ДО интеграции КАТО.
"""

import pytest

from jana_extract.parser import parse_address
from jana_extract.reference import get_reference
from jana_extract.service import extract_record

NOT_YET = pytest.mark.xfail(strict=True, reason="волна 4: КАТО ещё не интегрирован")


def todo(*args):
    return pytest.param(*args, marks=NOT_YET)


def fields(raw, *names):
    v = parse_address(raw).values
    return tuple(v[n] for n in names)


@pytest.mark.parametrize(
    ("raw", "city", "district", "province"),
    [
        # «Астана» и «Алматы» здесь — районы города Актобе, а не города.
        ("Актобе, район Астана, ул. Абая 5", "Актобе", "Астана", "Актюбинская область"),
        todo(
            "г. Актобе, район Алматы, пр. Абилкайыр хана 55",
            "Актобе",
            "Алматы",
            "Актюбинская область",
        ),
        todo(
            "Караганда, район им. Казыбек би, ул. Ерубаева 50",
            "Караганда",
            "им. Казыбек би",
            "Карагандинская область",
        ),
        (
            "г. Караганда, Әлихан Бөкейхан ауданы, Бұқар жырау даңғылы 10",
            "Караганда",
            "Әлихан Бөкейхан",
            "Карагандинская область",
        ),
        todo(
            "Караганда, район Алихан Бокейхан, ул. Гоголя 12",
            "Караганда",
            "Әлихан Бөкейхан",
            "Карагандинская область",
        ),
    ],
)
def test_oblast_city_district_written_in_text(raw, city, district, province):
    assert fields(raw, "city", "district", "province") == (city, district, province)


def test_astana_district_almaty_stays_astana():
    """Район «Алматы» есть и в Астане: с городом Астана — это район Астаны."""
    assert fields("г. Астана, район Алматы, ул. Абая 5", "province", "district") == (
        "Астана",
        "Алматы",
    )


def test_oblast_city_district_not_restored_by_street():
    """Геометрии в КАТО нет: без района в строке district у Караганды/Актобе — null."""
    assert fields("г. Караганда, ул. Ерубаева 50", "district") == (None,)
    assert fields("г. Актобе, ул. Абая 5", "district") == (None,)


@NOT_YET
def test_kato_provenance_in_reference_meta():
    meta = get_reference().meta
    kato = meta["kato"]
    assert kato["sha256"] == "7b87e0a8a72818ca5adcc7b4a7792dd0afd1a3165ed163197012c7c154d4e990"
    assert kato["source"].startswith("https://stat.gov.kz/")
    assert kato["rows"] == 15586


@NOT_YET
def test_official_name_and_code_in_debug_meta():
    rec = extract_record(
        {"id": 1, "raw_address": "Караганда, район им. Казыбек би, ул. Ерубаева 50"}, debug=True
    )
    off = rec["_meta"]["official"]["district"]
    assert off["kato"].startswith("3510") and "Казыбек би" in off["name"]


def test_existing_output_format_unchanged():
    """Для сущностей из OSM выходной формат не меняется (формат примера ТЗ)."""
    assert fields("г. Алматы, Ауэзовский р-н, ул. Абая 5", "province", "district") == (
        "Алматы",
        "Ауэзовский",
    )
    v = parse_address("Абай облысы, Семей қаласы, Шәкәрім даңғылы 20").values
    assert v["province"] == "Абайская область"
