"""Конвенции трактовки полей, согласованные для хакатона.

1. Район райцентра восстанавливается из справочника, только если «НП → район»
   однозначно: после учёта области/района из строки остаётся ровно один НП.
   Иначе district = null и confidence ниже.
2. Район по улице/мкр в городах респ. значения — только при доле ≥ 0.75.
3. Источник района при мкр + улице: сначала мкр, затем улица.
4. «мкр X N, M» с маркером мкр: N — часть названия, M — дом; без маркера мкр
   и без подтверждения справочником число — дом.
"""

import pytest

from jana_extract.parser import parse_address

NOT_YET = pytest.mark.xfail(strict=True, reason="конвенция ещё не реализована")


def district(raw):
    return parse_address(raw).values["district"]


# --------------------------------------------------------------------- 1


@NOT_YET
def test_ambiguous_settlement_gives_no_district():
    assert district("Алексеевка, ул. Абая 5") is None  # 10 одноимённых НП


@NOT_YET
def test_ambiguous_settlement_rank_dominance_is_not_enough():
    assert district("Боралдай, ул. Ленина 1") is None  # 3 одноимённых НП


def test_region_in_text_makes_settlement_unambiguous():
    assert district("Алматинская обл, Боралдай, ул. Ленина 1") == "Илийский"


def test_unique_district_centre_gets_its_district():
    assert district("Сарыагаш, ул. Абая 5") == "Сарыагашский"


@NOT_YET
def test_ambiguous_settlement_lowers_confidence():
    amb = parse_address("Алексеевка, ул. Абая 5").confidence
    uniq = parse_address("Сарыагаш, ул. Абая 5").confidence
    assert amb < uniq


# --------------------------------------------------------------------- 2


@NOT_YET
def test_street_share_below_threshold_gives_null():
    assert district("г. Алматы, ул. Розыбакиева 100") is None  # доля 0.72


@NOT_YET
def test_street_share_just_below_threshold_gives_null():
    assert district("г. Алматы, ул. Жумабаева 10") is None  # доля 0.74


def test_street_in_single_district_gives_district():
    assert district("г. Алматы, ул. Садовникова 5") == "Ауэзовский"  # доля 1.0


# --------------------------------------------------------------------- 3


@NOT_YET
def test_microdistrict_wins_over_street():
    assert district("г. Алматы, мкр Алгабас, ул. Садовникова 5") == "Алатауский"


@NOT_YET
def test_microdistrict_wins_over_street_other_district():
    assert district("г. Алматы, мкр Алгабас, ул. Майлина 5") == "Алатауский"


@NOT_YET
def test_microdistrict_wins_over_street_third():
    assert district("г. Алматы, мкр Алгабас, ул. Оренбургская 3") == "Алатауский"


def test_unknown_microdistrict_falls_back_to_street():
    assert district("г. Алматы, мкр Несуществующий, ул. Садовникова 5") == "Ауэзовский"


# --------------------------------------------------------------------- 4


@pytest.mark.parametrize(
    ("raw", "street", "house", "apt"),
    [
        ("город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70", "Аксай 3а", "59", "70"),
        ("Атырауская обл., г. Курмангазы, мкр Мортык 104а, 27", "Мортык 104а", "27", None),
        ("г. Астана, мкр. Улы Дала 185, 173", "Улы Дала 185", "173", None),
        ("Актюбинская обл, Қайнар ауылы, мкр. Аши 61, кв 339", "Аши", "61", "339"),
        ("г. Алматы, Абая 12, 5", "Абая", "12", "5"),
    ],
)
def test_microdistrict_number_then_house(raw, street, house, apt):
    v = parse_address(raw).values
    assert (v["street_name"], v["house"], v["apartment"]) == (street, house, apt)
