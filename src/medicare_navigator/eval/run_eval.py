"""Evaluation runner for Phase 1 acceptance criteria."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from medicare_navigator.config import settings
from medicare_navigator.ingestion.spuf import IngestFilters, ingest_spuf
from medicare_navigator.orchestrator.router import orchestrator


def _eval_fixture_dir() -> Path:
    return settings.project_root / "tests" / "fixtures" / "spuf"


def _queries_path() -> Path:
    return Path(__file__).parent / "queries.jsonl"


async def _run_case(case: dict) -> dict:
    resp = await orchestrator.run(message=case["message"], session_id=None)
    result = {
        "id": case["id"],
        "message": case["message"],
        "actual_status": resp.status,
        "passed": True,
        "failures": [],
    }

    expected_status = case.get("expected_status")
    if expected_status and resp.status != expected_status:
        result["passed"] = False
        result["failures"].append(f"status: expected {expected_status}, got {resp.status}")

    if case.get("expected_tier") is not None and resp.estimate:
        if case["expected_tier"] not in resp.estimate.tiers_matched:
            result["passed"] = False
            result["failures"].append(
                f"tier: expected {case['expected_tier']}, got {resp.estimate.tiers_matched}"
            )

    if case.get("expected_cost") is not None and resp.estimate:
        actual = resp.estimate.cost_low
        if actual != case["expected_cost"]:
            result["passed"] = False
            result["failures"].append(f"cost: expected {case['expected_cost']}, got {actual}")

    if case.get("expected_phase") and resp.estimate:
        if resp.estimate.benefit_phase != case["expected_phase"]:
            result["passed"] = False
            result["failures"].append(
                f"phase: expected {case['expected_phase']}, got {resp.estimate.benefit_phase}"
            )

    if case.get("expected_tool_status"):
        for tool, status in case["expected_tool_status"].items():
            actual = resp.tool_statuses.get(tool)
            if actual != status:
                result["passed"] = False
                result["failures"].append(f"{tool}: expected {status}, got {actual}")

    if resp.status == "ok" and resp.explanation:
        if "Disclaimer" not in resp.explanation and "informational" not in resp.explanation.lower():
            result["passed"] = False
            result["failures"].append("missing disclaimer in explanation")

    hard_stop_statuses = {"suppressed", "insulin_out_of_scope", "quantity_limit_blocked", "not_covered"}
    estimate_status = resp.tool_statuses.get("estimate_drug_cost")
    if resp.status == "ok" and not resp.citations and estimate_status not in hard_stop_statuses:
        # Mock navigator may build citations from artifacts; allow empty only for clarification-like
        # ok responses or the spec's hard-stop/no-cost statuses, which have no computed data to cite.
        if "which drug" not in resp.explanation.lower() and "which medicare plan" not in resp.explanation.lower():
            result["passed"] = False
            result["failures"].append("no citations on ok response")

    return result


async def run_eval() -> int:
    from medicare_navigator.config import settings

    settings.llm_mock_mode = True
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    filters = IngestFilters(
        contract_year=2026,
        states=["FL", "TX"],
        pdp_region_codes={"FL": "11", "TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    ingest_spuf(
        _eval_fixture_dir(),
        filters=filters,
        version="SPUF.2026.20260115",
    )
    from tests.spuf_fixture import TEST_DRUGS
    from medicare_navigator.storage.connection import DuckDBConnection

    conn = DuckDBConnection().connect()
    try:
        for row in TEST_DRUGS:
            conn.execute("INSERT INTO drugs VALUES (?, ?, ?, ?, ?)", list(row))
    finally:
        conn.close()
    cases = []
    with _queries_path().open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    results = []
    for case in cases:
        result = await _run_case(case)
        results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {result['id']}: {case['message'][:60]}")
        for failure in result["failures"]:
            print(f"       - {failure}")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    rate = passed / total if total else 0
    print(f"\nResults: {passed}/{total} passed ({rate:.0%})")

    out_path = Path(__file__).parent / "results.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0 if passed == total else 1


def main() -> None:
    raise SystemExit(asyncio.run(run_eval()))


if __name__ == "__main__":
    main()
