"""Deterministic oracle consistency: API estimate data and guided UI contracts align."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "frontend" / "src" / "app.js"


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.fixture
def client():
    return TestClient(app)


def _estimate(client, *, plan_id: str, drug: str, dosage: str, ytd: float = 0):
    resp = client.post(
        "/api/estimate",
        json={
            "plan_id": plan_id,
            "drug": drug,
            "dosage": dosage,
            "days_supply": 30,
            "ytd_oop_spend": ytd,
        },
    )
    assert resp.status_code == 200
    return resp.json()


def test_metformin_covered_on_pdp_has_tier_and_cost(client):
    body = _estimate(client, plan_id=PLAN_FL_PDP, drug="metformin", dosage="500mg")
    data = body["data"]
    assert body["status"] == "ok"
    assert data["covered"] is True
    assert data["tier"] == 1
    assert data["channels"]["preferred_retail"]["cost_low"] == 5.0


def test_omeprazole_not_covered_on_mapd(client):
    body = _estimate(client, plan_id=PLAN_FL_MAPD, drug="omeprazole", dosage="20mg")
    data = body["data"]
    assert data["covered"] is False
    assert data["channels"]["preferred_retail"]["cost_low"] is None


def test_compare_plans_items_match_individual_estimates(client):
    drug = "metformin"
    dosage = "500mg"
    plans = [PLAN_FL_PDP, PLAN_FL_MAPD]
    compare = client.post(
        "/api/compare-plans",
        json={
            "drug": drug,
            "dosage": dosage,
            "plan_ids": plans,
            "days_supply": 30,
            "ytd_oop_spend": 0,
        },
    ).json()
    assert compare["status"] == "ok"
    by_plan = {item["plan_id"]: item for item in compare["items"]}
    for plan_id in plans:
        single = _estimate(client, plan_id=plan_id, drug=drug, dosage=dosage)
        item = by_plan[plan_id]
        assert item["status"] == single["status"]
        if single["status"] == "ok":
            assert item["data"]["tier"] == single["data"]["tier"]
            assert item["data"]["covered"] == single["data"]["covered"]
            pr_compare = item["data"]["channels"]["preferred_retail"]["cost_low"]
            pr_single = single["data"]["channels"]["preferred_retail"]["cost_low"]
            assert pr_compare == pr_single


def test_same_drug_tier_differs_across_fixture_plans(client):
    """Oracle anchor for live LLM B5: metformin tier differs PDP vs MAPD."""
    pdp = _estimate(client, plan_id=PLAN_FL_PDP, drug="metformin", dosage="500mg")
    mapd = _estimate(client, plan_id=PLAN_FL_MAPD, drug="metformin", dosage="500mg")
    assert pdp["data"]["tier"] != mapd["data"]["tier"]


def test_app_js_shows_not_covered_blocked_state_for_false_covered():
    text = APP_JS.read_text(encoding="utf-8")
    assert "estimate.covered === false" in text
    assert "Not covered" in text
    assert "estimate-cost--blocked" in text
