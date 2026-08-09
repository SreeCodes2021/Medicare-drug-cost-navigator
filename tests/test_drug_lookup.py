"""Tests for drug/dosage lookup (discovery/UX only, never used in cost estimates)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from medicare_navigator.storage.repository import BasicDrugsFormularyRepository
from medicare_navigator.tools.drug_lookup import (
    drug_on_formulary,
    list_drug_dosages,
    search_drugs,
)
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP


def test_search_drugs_returns_common_list_without_query():
    drugs = __import__("asyncio").run(search_drugs())
    assert "metformin" in drugs
    assert "lovastatin" in drugs


def test_search_drugs_filters_common_list():
    drugs = __import__("asyncio").run(search_drugs("met"))
    assert "metformin" in drugs


def test_list_drug_dosages_empty_for_blank_name():
    assert __import__("asyncio").run(list_drug_dosages("")) == []


def test_drugs_endpoint_returns_list():
    client = TestClient(app)
    response = client.get("/api/drugs")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("drugs"), list)
    assert "metformin" in body["drugs"]


def test_drugs_endpoint_accepts_query():
    client = TestClient(app)
    response = client.get("/api/drugs", params={"q": "lova"})
    assert response.status_code == 200
    assert "lovastatin" in response.json()["drugs"]


def test_drug_dosages_endpoint_requires_drug():
    client = TestClient(app)
    response = client.get("/api/drug-dosages", params={"drug": "  "})
    assert response.status_code == 400


def test_drug_dosages_endpoint_returns_dosages():
    client = TestClient(app)
    with patch(
        "medicare_navigator.tools.drug_lookup.list_drug_dosages",
        new=AsyncMock(return_value=["500mg", "850mg"]),
    ):
        response = client.get("/api/drug-dosages", params={"drug": "metformin"})
    assert response.status_code == 200
    body = response.json()
    assert body["drug"] == "metformin"
    assert body["dosages"] == ["500mg", "850mg"]


def test_has_any_rxcui(spuf_db):
    repo = BasicDrugsFormularyRepository()
    assert repo.has_any_rxcui("FORM0001", ["6809"])
    assert not repo.has_any_rxcui("FORM0001", ["999999"])
    assert not repo.has_any_rxcui("FORM0001", [])


@pytest.mark.asyncio
async def test_drug_on_formulary_with_mocked_strengths(spuf_db):
    metformin_concepts = [
        {"rxcui": "6809", "concept_name": "metformin 500 MG Oral Tablet", "tty": "SCD"},
    ]
    omeprazole_concepts = [
        {"rxcui": "7646", "concept_name": "omeprazole 20 MG Oral Capsule", "tty": "SCD"},
    ]

    with patch(
        "medicare_navigator.tools.drug_lookup._cached_list_strength_concepts",
        new=AsyncMock(side_effect=[metformin_concepts, omeprazole_concepts]),
    ):
        assert await drug_on_formulary(PLAN_FL_MAPD, "metformin") is True
        assert await drug_on_formulary(PLAN_FL_MAPD, "omeprazole") is False


@pytest.mark.asyncio
async def test_search_drugs_with_plan_id_returns_formulary_flags(spuf_db):
    metformin_concepts = [{"rxcui": "6809", "concept_name": "metformin 500 MG Oral Tablet"}]
    omeprazole_concepts = [{"rxcui": "7646", "concept_name": "omeprazole 20 MG Oral Capsule"}]

    async def fake_cached(name: str):
        if name == "metformin":
            return metformin_concepts
        if name == "omeprazole":
            return omeprazole_concepts
        return []

    with patch(
        "medicare_navigator.tools.drug_lookup._cached_list_strength_concepts",
        new=AsyncMock(side_effect=fake_cached),
    ):
        met_results = await search_drugs("met", plan_id=PLAN_FL_MAPD)
        ome_results = await search_drugs("ome", plan_id=PLAN_FL_MAPD)

    assert met_results[0]["name"] == "metformin"
    assert met_results[0]["on_formulary"] is True
    assert ome_results[0]["name"] == "omeprazole"
    assert ome_results[0]["on_formulary"] is False


@pytest.mark.asyncio
async def test_list_drug_dosages_with_plan_id_returns_formulary_flags(spuf_db):
    concepts = [
        {"rxcui": "6809", "concept_name": "metformin 500 MG Oral Tablet", "tty": "SCD"},
        {"rxcui": "861007", "concept_name": "metformin 850 MG Oral Tablet", "tty": "SCD"},
    ]
    with patch(
        "medicare_navigator.tools.drug_lookup._cached_list_strength_concepts",
        new=AsyncMock(return_value=concepts),
    ):
        results = await list_drug_dosages("metformin", plan_id=PLAN_FL_PDP)

    assert isinstance(results[0], dict)
    by_dosage = {item["dosage"]: item["on_formulary"] for item in results}
    assert by_dosage["500mg"] is True
    assert by_dosage["850mg"] is True


def test_drugs_endpoint_with_plan_id_returns_objects(spuf_db):
    client = TestClient(app)
    metformin_concepts = [{"rxcui": "6809", "concept_name": "metformin 500 MG Oral Tablet"}]
    with patch(
        "medicare_navigator.tools.drug_lookup._cached_list_strength_concepts",
        new=AsyncMock(return_value=metformin_concepts),
    ):
        response = client.get("/api/drugs", params={"q": "met", "plan_id": PLAN_FL_MAPD})
    assert response.status_code == 200
    drugs = response.json()["drugs"]
    metformin = next(item for item in drugs if item["name"] == "metformin")
    assert metformin["on_formulary"] is True


def test_drug_dosages_endpoint_with_plan_id_returns_objects(spuf_db):
    client = TestClient(app)
    concepts = [{"rxcui": "6809", "concept_name": "metformin 500 MG Oral Tablet", "tty": "SCD"}]
    with patch(
        "medicare_navigator.tools.drug_lookup._cached_list_strength_concepts",
        new=AsyncMock(return_value=concepts),
    ):
        response = client.get(
            "/api/drug-dosages",
            params={"drug": "metformin", "plan_id": PLAN_FL_PDP},
        )
    assert response.status_code == 200
    dosages = response.json()["dosages"]
    assert dosages[0]["dosage"] == "500mg"
    assert dosages[0]["on_formulary"] is True
