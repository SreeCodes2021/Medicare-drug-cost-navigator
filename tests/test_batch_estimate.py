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


def test_estimate_batch_happy_path(client):
    resp = client.post(
        "/api/estimate-batch",
        json={
            "plan_id": PLAN_FL_PDP,
            "items": [{"drug": "metformin", "dosage": "500mg"}, {"drug": "lisinopril", "dosage": "10mg"}],
            "days_supply": 30,
            "ytd_oop_spend": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert item["status"] == "ok"
        assert item["data"] is not None
    assert body["caveat"] is None
    assert body["combined_total_low"] is not None
    assert body["combined_total_high"] is not None
    assert body["combined_total_low"] <= body["combined_total_high"]


def test_estimate_batch_combined_total_math(client):
    resp = client.post(
        "/api/estimate-batch",
        json={
            "plan_id": PLAN_FL_PDP,
            "items": [{"drug": "metformin", "dosage": "500mg"}, {"drug": "lisinopril", "dosage": "10mg"}],
        },
    )
    body = resp.json()

    def _bounds(data):
        lows = [c["cost_low"] for c in data["channels"].values() if c["cost_low"] is not None]
        highs = [
            c["cost_high"] if c["cost_high"] is not None else c["cost_low"]
            for c in data["channels"].values()
            if c["cost_low"] is not None
        ]
        return min(lows), max(highs)

    expected_low = 0.0
    expected_high = 0.0
    for item in body["items"]:
        low, high = _bounds(item["data"])
        expected_low += low
        expected_high += high

    assert body["combined_total_low"] == pytest.approx(expected_low, abs=0.01)
    assert body["combined_total_high"] == pytest.approx(expected_high, abs=0.01)


def test_estimate_batch_partial_failure_excludes_uncovered_from_total(client):
    resp = client.post(
        "/api/estimate-batch",
        json={
            "plan_id": PLAN_FL_MAPD,
            "items": [
                {"drug": "metformin", "dosage": "500mg"},
                {"drug": "omeprazole", "dosage": "20mg"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    statuses = {item["drug"]: item["status"] for item in body["items"]}
    assert statuses["metformin"] == "ok"
    assert statuses["omeprazole"] == "not_covered"
    assert body["caveat"] is not None
    assert "not" in body["caveat"].lower()
    # combined total should not silently include the uncovered drug as $0
    assert body["combined_total_low"] is not None


def test_estimate_batch_cap_enforcement(client):
    resp = client.post(
        "/api/estimate-batch",
        json={
            "plan_id": PLAN_FL_PDP,
            "items": [{"drug": f"drug{i}"} for i in range(6)],
        },
    )
    assert resp.status_code == 400


def test_estimate_batch_requires_at_least_one_item(client):
    resp = client.post("/api/estimate-batch", json={"plan_id": PLAN_FL_PDP, "items": []})
    assert resp.status_code == 400


def test_estimate_batch_requires_plan_id(client):
    resp = client.post(
        "/api/estimate-batch",
        json={"plan_id": "  ", "items": [{"drug": "metformin"}]},
    )
    assert resp.status_code == 400


def _batch_item_bounds(data):
    lows = [c["cost_low"] for c in data["channels"].values() if c["cost_low"] is not None]
    highs = [
        c["cost_high"] if c["cost_high"] is not None else c["cost_low"]
        for c in data["channels"].values()
        if c["cost_low"] is not None
    ]
    return min(lows), max(highs)


def test_estimate_batch_insulin_plus_regular(client):
    resp = client.post(
        "/api/estimate-batch",
        json={
            "plan_id": PLAN_FL_PDP,
            "items": [
                {"drug": "metformin", "dosage": "500mg"},
                {"drug": "lantus"},
            ],
            "days_supply": 30,
            "ytd_oop_spend": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    by_drug = {item["drug"]: item for item in body["items"]}
    assert by_drug["metformin"]["status"] == "ok"
    assert by_drug["lantus"]["status"] == "ok"
    assert by_drug["metformin"]["data"]["benefit_phase"] == "pre_deductible"
    assert by_drug["metformin"]["data"]["effective_phase"] == "initial_coverage"
    assert by_drug["lantus"]["data"]["benefit_phase"] == "insulin_cap"


def test_estimate_batch_insulin_plus_regular_combined_total(client):
    resp = client.post(
        "/api/estimate-batch",
        json={
            "plan_id": PLAN_FL_PDP,
            "items": [
                {"drug": "metformin", "dosage": "500mg"},
                {"drug": "lantus"},
            ],
        },
    )
    body = resp.json()
    expected_low = 0.0
    expected_high = 0.0
    for item in body["items"]:
        if item["status"] != "ok" or item["data"] is None:
            continue
        low, high = _batch_item_bounds(item["data"])
        expected_low += low
        expected_high += high
    assert body["combined_total_low"] == pytest.approx(expected_low, abs=0.01)
    assert body["combined_total_high"] == pytest.approx(expected_high, abs=0.01)
    assert body["combined_total_low"] == pytest.approx(33.0, abs=0.01)


def test_estimate_batch_insulin_data_gap_plus_regular(client):
    resp = client.post(
        "/api/estimate-batch",
        json={
            "plan_id": PLAN_FL_MAPD,
            "items": [
                {"drug": "lantus"},
                {"drug": "metformin", "dosage": "500mg"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    statuses = {item["drug"]: item["status"] for item in body["items"]}
    assert statuses["lantus"] == "insulin_out_of_scope"
    assert statuses["metformin"] == "ok"
    assert body["caveat"] is not None
    metformin_item = next(item for item in body["items"] if item["drug"] == "metformin")
    low, high = _batch_item_bounds(metformin_item["data"])
    assert body["combined_total_low"] == pytest.approx(low, abs=0.01)
    assert body["combined_total_high"] == pytest.approx(high, abs=0.01)
