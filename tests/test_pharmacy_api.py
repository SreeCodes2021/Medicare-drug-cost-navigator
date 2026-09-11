"""End-to-end /api/chat coverage for the three chat-driven pharmacy locator question types.

Confirms the feature is reachable purely through free chat text with no new request field —
ChatRequest/FilterPayload gain nothing new; ZIP is parsed out of the message itself.
"""

import pytest
from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from tests.spuf_fixture import PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.fixture
def client():
    return TestClient(app)


def _chat(client, message: str, *, plan_id: str | None = None):
    resp = client.post(
        "/api/chat",
        json={
            "message": message,
            "filters": {"plan_id": plan_id} if plan_id else {},
        },
    )
    assert resp.status_code == 200
    return resp.json()["response"]


def test_preferred_pharmacy_question(client):
    body = _chat(
        client,
        f"What are the preferred pharmacies for zip 32801 and plan {PLAN_FL_PDP}?",
    )
    assert body["status"] == "ok"
    assert "Icon Pharmacy" in body["explanation"]
    assert body["response_source"] == "System/PreferredPharmacy"
    assert any(c["source_id"] == "nppes_npi_registry" for c in body["citations"])


def test_drug_cost_at_preferred_pharmacy_question(client):
    body = _chat(
        client,
        "How much does metformin 500mg cost at my preferred pharmacy in zip 32801 "
        f"for plan {PLAN_FL_PDP}?",
    )
    assert body["status"] == "ok"
    assert "Icon Pharmacy" in body["explanation"]
    assert "$" in body["explanation"]
    assert body["response_source"] == "System/PharmacyCost"


def test_nearby_pharmacy_question(client):
    body = _chat(client, "What pharmacies are near me? I live in zip 32801.")
    assert body["status"] == "ok"
    assert "Icon Pharmacy" in body["explanation"]
    assert body["response_source"] == "System/NearbyPharmacy"


def test_pharmacy_question_far_from_any_network_pharmacy_is_honest(client):
    body = _chat(
        client,
        f"What are the preferred pharmacies for zip 90001 and plan {PLAN_FL_PDP}?",
    )
    assert body["status"] == "ok"
    assert "no pharmacies" in body["explanation"].lower()
    assert "Icon Pharmacy" not in body["explanation"]


def test_preferred_pharmacy_question_without_zip_asks_for_clarification(client):
    body = _chat(client, f"What's my preferred pharmacy for plan {PLAN_FL_PDP}?")
    assert body["status"] == "needs_clarification"
    assert "zip" in body["explanation"].lower()


def test_pharmacy_filters_never_gain_zip_field():
    """No frontend change is allowed to reach the backend — FilterPayload must stay exactly
    drug/dosage/plan_id/contract_year/ytd_oop_spend/days_supply, no zip field added."""
    from medicare_navigator.api.app import FilterPayload

    assert "zip" not in FilterPayload.model_fields
    assert "zip_code" not in FilterPayload.model_fields
