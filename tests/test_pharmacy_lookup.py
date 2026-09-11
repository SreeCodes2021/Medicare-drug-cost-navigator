import pytest

from medicare_navigator.ingestion.zip_centroids import (
    centroid_for_zip,
    distance_between_zips,
    haversine_miles,
)
from medicare_navigator.agent.pharmacy_questions import _pharmacy_list_sentence
from medicare_navigator.models.response import PharmacyResult
from medicare_navigator.tools.pharmacy_lookup import find_pharmacies
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


def test_centroid_for_zip_known():
    centroid = centroid_for_zip("32801")
    assert centroid is not None
    lat, lon = centroid
    assert 28 < lat < 29
    assert -82 < lon < -81


def test_centroid_for_zip_unknown():
    assert centroid_for_zip("00000") is None


def test_centroid_for_zip_malformed():
    assert centroid_for_zip("123") is None
    assert centroid_for_zip("abcde") is None
    assert centroid_for_zip(None) is None


def test_haversine_zero_distance_same_point():
    assert haversine_miles(28.5, -81.4, 28.5, -81.4) == 0.0


def test_haversine_known_distance_orlando_to_miami():
    distance = distance_between_zips("32801", "33157")
    assert distance is not None
    assert 200 < distance < 225


def test_distance_between_zips_unknown_zip_returns_none():
    assert distance_between_zips("32801", "00000") is None


def test_find_pharmacies_sorted_nearest_first():
    result = find_pharmacies(zip_code="32801", plan_key=PLAN_FL_PDP, preferred_only=True)
    assert result.status.value == "ok"
    distances = [p.distance_miles for p in result.data]
    assert distances == sorted(distances)
    assert result.data[0].pharmacy_name == "Icon Pharmacy"
    assert result.data[0].distance_miles == 0.0


def test_find_pharmacies_preferred_only_excludes_standard():
    result = find_pharmacies(zip_code="32801", plan_key=PLAN_FL_PDP, preferred_only=True)
    assert result.status.value == "ok"
    names = {p.pharmacy_name for p in result.data}
    assert "Angels Pharmacy I Inc" not in names


def test_find_pharmacies_without_preferred_only_includes_standard():
    result = find_pharmacies(zip_code="32801", plan_key=PLAN_FL_PDP)
    assert result.status.value == "ok"
    names = {p.pharmacy_name for p in result.data}
    assert "Angels Pharmacy I Inc" in names


def test_find_pharmacies_radius_excludes_far_pharmacy():
    result = find_pharmacies(zip_code="32801", plan_key=PLAN_FL_PDP, radius_miles=25)
    assert result.status.value == "ok"
    names = {p.pharmacy_name for p in result.data}
    assert "Jackson Pharmacy Jackson South" not in names


def test_find_pharmacies_wide_radius_includes_far_pharmacy():
    result = find_pharmacies(zip_code="32801", plan_key=PLAN_FL_PDP, radius_miles=250)
    assert result.status.value == "ok"
    names = {p.pharmacy_name for p in result.data}
    assert "Jackson Pharmacy Jackson South" in names


def test_find_pharmacies_channel_filter_preferred_retail_excludes_mail():
    result = find_pharmacies(
        zip_code="32801",
        plan_key=PLAN_FL_PDP,
        preferred_only=True,
        channel="preferred_retail",
    )
    assert result.status.value == "ok"
    assert all(p.channel == "preferred_retail" for p in result.data)
    names = {p.pharmacy_name for p in result.data}
    assert "Accredo Health Group Inc" not in names  # preferred_mail, not retail


def test_find_pharmacies_no_plan_scans_all_pharmacies():
    result = find_pharmacies(zip_code="32801", radius_miles=250)
    assert result.status.value == "ok"
    names = {p.pharmacy_name for p in result.data}
    # H8888-001-only membership shouldn't matter; plan-agnostic search returns every
    # enriched pharmacy regardless of which plan(s) it's networked with.
    assert "Icon Pharmacy" in names
    assert "Angels Pharmacy I Inc" in names


def test_find_pharmacies_unknown_zip_not_found():
    result = find_pharmacies(zip_code="00000")
    assert result.status.value == "not_found"
    assert result.data is None


def test_find_pharmacies_no_match_within_radius():
    # 90001 (Los Angeles) is a recognized ZIP centroid, but nowhere near any fixture
    # pharmacy (all in central Florida) within the default 25mi radius.
    result = find_pharmacies(zip_code="90001", plan_key=PLAN_FL_PDP)
    assert result.status.value == "no_match"
    assert result.data is None


def test_find_pharmacies_limit_truncates():
    result = find_pharmacies(zip_code="32801", plan_key=PLAN_FL_PDP, limit=1)
    assert result.status.value == "ok"
    assert len(result.data) == 1


def test_find_pharmacies_shared_npi_across_plans():
    """Icon Pharmacy (NPI 1841304730) is in both S9999-001 and H8888-001's networks."""
    result = find_pharmacies(zip_code="32801", plan_key=PLAN_FL_MAPD, preferred_only=True)
    assert result.status.value == "ok"
    names = {p.pharmacy_name for p in result.data}
    assert "Icon Pharmacy" in names


def test_find_pharmacies_enriches_cms_stub_records(monkeypatch):
    monkeypatch.setattr(
        "medicare_navigator.tools.pharmacy_lookup.PharmacyRepository.nearby_candidates",
        lambda self, **kwargs: [
            {
                "npi": "101124789573",
                "pharmacy_name": "Pharmacy near 72712",
                "address_line1": None,
                "city": None,
                "state": None,
                "zip_code": "72712",
                "preferred_yn": None,
                "retail_yn": None,
                "mail_yn": None,
            }
        ],
    )
    monkeypatch.setattr(
        "medicare_navigator.tools.pharmacy_lookup.PharmacyRepository.enrich_stub_records",
        lambda self, identifiers: {
            "101124789573": {
                "pharmacy_name": "TRISTATE INFUSION, LLC",
                "address_line1": "901 SE 28th St",
                "city": "Bentonville",
                "state": "AR",
                "zip_code": "72712",
                "enrichment_source": "nppes_api",
            }
        },
    )

    result = find_pharmacies(zip_code="72712", limit=1)
    assert result.status.value == "ok"
    assert result.data[0].pharmacy_name == "TRISTATE INFUSION, LLC"
    assert result.data[0].address_line1 == "901 SE 28th St"


def test_pharmacy_list_sentence_collapses_identical_zip_stubs():
    pharmacies = [
        PharmacyResult(
            npi="101124789573",
            pharmacy_name="Pharmacy near 72712",
            zip_code="72712",
            distance_miles=0.0,
        ),
        PharmacyResult(
            npi="101134229453",
            pharmacy_name="Pharmacy near 72712",
            zip_code="72712",
            distance_miles=0.0,
        ),
    ]
    sentence = _pharmacy_list_sentence(pharmacies)
    assert "CMS lists 2 in-network pharmacies in ZIP 72712" in sentence
    assert sentence.count("Pharmacy near 72712") == 0
