import pytest
from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.fixture
def client():
    return TestClient(app)


def test_compare_plans_happy_path(client):
    resp = client.post(
        "/api/compare-plans",
        json={
            "drug": "metformin",
            "dosage": "500mg",
            "plan_ids": [PLAN_FL_PDP, PLAN_FL_MAPD],
            "days_supply": 30,
            "ytd_oop_spend": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["items"]) == 2
    plan_ids = {item["plan_id"] for item in body["items"]}
    assert plan_ids == {PLAN_FL_PDP, PLAN_FL_MAPD}
    for item in body["items"]:
        assert item["status"] == "ok"
        assert item["data"] is not None


def test_compare_plans_disclaimer_always_present(client):
    resp = client.post(
        "/api/compare-plans",
        json={"drug": "metformin", "plan_ids": [PLAN_FL_PDP, PLAN_FL_MAPD]},
    )
    body = resp.json()
    disclaimer = body["disclaimer"].lower()
    assert "premium" in disclaimer
    assert "not a recommendation" in disclaimer


def test_compare_plans_response_never_includes_ranking(client):
    resp = client.post(
        "/api/compare-plans",
        json={"drug": "metformin", "plan_ids": [PLAN_FL_PDP, PLAN_FL_MAPD]},
    )
    body = resp.json()
    assert set(body.keys()) == {"status", "items", "disclaimer"}
    for item in body["items"]:
        assert set(item.keys()) == {"plan_id", "data", "status", "message"}


def test_compare_plans_cap_enforcement(client):
    resp = client.post(
        "/api/compare-plans",
        json={"drug": "metformin", "plan_ids": [f"P{i:04d}-000" for i in range(5)]},
    )
    assert resp.status_code == 400


def test_compare_plans_requires_at_least_two_plans(client):
    resp = client.post(
        "/api/compare-plans",
        json={"drug": "metformin", "plan_ids": [PLAN_FL_PDP]},
    )
    assert resp.status_code == 400


def test_compare_plans_requires_drug(client):
    resp = client.post(
        "/api/compare-plans",
        json={"drug": "  ", "plan_ids": [PLAN_FL_PDP, PLAN_FL_MAPD]},
    )
    assert resp.status_code == 400
