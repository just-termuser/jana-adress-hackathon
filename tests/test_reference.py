import pytest

from jana_extract.reference import get_reference
from jana_extract.text import core, fold


@pytest.fixture(scope="module")
def ref():
    return get_reference()


def test_fold_only_for_matching():
    assert fold("Ақтөбе") == fold("Актобе")
    assert fold("Әуезов") == fold("Ауэзов")
    assert fold("подьезд") == fold("подъезд")
    assert fold("4-й микрорайон") == "4 микрорайон"
    assert core("микрорайон Аксай-3А") == "аксай 3а"
    assert core("Абая улица") == "абая"


def test_regions(ref):
    assert len(ref.regions) == 20
    i, _ = ref.find_region("Актюбинская обл")
    assert ref.region_name(i) == "Актюбинская область"
    i, _ = ref.find_region("Ақтөбе облысы")
    assert ref.region_name(i) == "Актюбинская область"
    i, _ = ref.find_region("Алматы")
    assert ref.region_name(i) == "Алматы"


def test_city_district_from_microdistrict(ref):
    almaty = ref.region_by_name["Алматы"]
    d, share = ref.area_or_street_district(almaty, core("Аксай 3а"))
    assert ref.district_name(d) == "Ауэзовский"
    assert share > 0.8


def test_settlement_disambiguation(ref):
    cands, _ = ref.find_settlement("Актобе")
    best = ref.best_settlement(cands)
    assert ref.region_name(best.region) == "Актюбинская область"


def test_district_lookup(ref):
    d, _ = ref.find_district("Ауэзовский р-н", region=ref.region_by_name["Алматы"])
    assert ref.district_name(d) == "Ауэзовский"
    d, _ = ref.find_district("Бостандыкский район")
    assert ref.district_name(d) == "Бостандыкский"
