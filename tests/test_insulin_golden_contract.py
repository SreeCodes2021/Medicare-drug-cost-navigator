"""Parametric contract tests for insulin_cap golden cases (golden-037 through golden-045).

Mirrors scripts/run_golden_cases.py checks in pytest so /quality-test/insulin can
cite a stable test module name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from medicare_navigator.config import settings
from tests.spuf_fixture import load_spuf_fixture

GOLDEN_PATH = (
    Path(__file__).resolve().parent.parent
    / ".cursor"
    / "skills"
    / "tests"
    / "utils"
    / "numeric-accuracy"
    / "golden-cases.jsonl"
)

INSULIN_FIXTURE_IDS = tuple(f"golden-0{i}" for i in range(37, 46))


def _load_insulin_fixture_cases() -> list[dict]:
    cases = []
    with GOLDEN_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            if case.get("id") in INSULIN_FIXTURE_IDS and not case.get("requires_live_ingest"):
                cases.append(case)
    return cases


@pytest.fixture(scope="module")
def estimate_client(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("insulin_golden")
    load_spuf_fixture(data_dir=data_dir)
    settings.data_dir = data_dir
    settings.duckdb_path = data_dir / "navigator.duckdb"
    return TestClient(app)


def _post_estimate(client: TestClient, case: dict) -> dict:
    payload = {
        "plan_id": case["plan_id"],
        "drug": case["drug"],
        "days_supply": case.get("days_supply", 30),
        "ytd_oop_spend": case.get("ytd_oop_spend", 0),
    }
    if case.get("dosage") is not None:
        payload["dosage"] = case["dosage"]
    return client.post("/api/estimate", json=payload).json()


@pytest.mark.parametrize("case", _load_insulin_fixture_cases(), ids=lambda c: c["id"])
def test_insulin_golden_fixture_case(estimate_client, case):
    from scripts.run_golden_cases import _check_case

    estimate = _post_estimate(estimate_client, case)
    failures = _check_case(case, estimate)
    assert not failures, failures
