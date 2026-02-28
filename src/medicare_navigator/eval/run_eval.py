"""Evaluation runner for Phase 1 acceptance criteria."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from medicare_navigator.ingestion.seed import run_seed
from medicare_navigator.orchestrator.pipeline import orchestrator


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

    if case.get("expected_tier") is not None and resp.formulary:
        if resp.formulary.tier != case["expected_tier"]:
            result["passed"] = False
            result["failures"].append(
                f"tier: expected {case['expected_tier']}, got {resp.formulary.tier}"
            )

    if case.get("expected_copay") is not None and resp.formulary and resp.formulary.cost_share:
        actual = resp.formulary.cost_share.copay
        if actual != case["expected_copay"]:
            result["passed"] = False
            result["failures"].append(f"copay: expected {case['expected_copay']}, got {actual}")

    if case.get("expected_phase") and resp.formulary:
        if resp.formulary.benefit_phase != case["expected_phase"]:
            result["passed"] = False
            result["failures"].append(
                f"phase: expected {case['expected_phase']}, got {resp.formulary.benefit_phase}"
            )

    if case.get("expected_tool_status"):
        for tool, status in case["expected_tool_status"].items():
            actual = resp.tool_statuses.get(tool)
            if actual != status:
                result["passed"] = False
                result["failures"].append(f"{tool}: expected {status}, got {actual}")

    if case.get("expected_has_trend"):
        if not resp.cost_trend:
            result["passed"] = False
            result["failures"].append("expected cost trend data")

    if resp.status == "ok" and resp.explanation:
        if "Disclaimer" not in resp.explanation and "informational" not in resp.explanation.lower():
            result["passed"] = False
            result["failures"].append("missing disclaimer in explanation")

    if resp.status == "ok" and not resp.citations:
        result["passed"] = False
        result["failures"].append("no citations on ok response")

    return result


async def run_eval() -> int:
    run_seed()
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
