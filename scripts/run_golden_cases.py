"""Diff golden numeric-accuracy cases against the deterministic /api/estimate
oracle (no LLM in the loop).

Golden cases live in `.cursor/skills/numeric-accuracy/golden-cases.jsonl` —
each one either mirrors an offline fixture value (`requires_live_ingest: false`,
runs against `tests/fixtures/spuf` data) or a value manually re-verified
against a real CMS SPUF ingest (`requires_live_ingest: true`, see
docs/business-solution.md §3.3). This script never calls an LLM — it is a
ground-truth check on the cost pipeline itself.

Usage:
    # Offline-only cases, no server needed (fixture data auto-loaded)
    python scripts/run_golden_cases.py

    # Include live-ingest cases against a running server with real CMS data
    # already ingested (e.g. `medicare-ingest spuf --download --states AR --merge-states`)
    python scripts/run_golden_cases.py --include-live --base-url http://localhost:8000

    # Summary by case_group (tier_lookup, channel, benefit_phase, copay, etc.)
    python scripts/run_golden_cases.py --by-group
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

GOLDEN_CASES_PATH = PROJECT_ROOT / ".cursor" / "skills" / "numeric-accuracy" / "golden-cases.jsonl"


def _load_cases() -> list[dict]:
    cases = []
    with GOLDEN_CASES_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _channel_for_case(case: dict, channels: dict) -> dict | None:
    channel_name = case.get("channel")
    if not channel_name:
        return None
    return channels.get(channel_name)


def _check_case(case: dict, estimate: dict) -> list[str]:
    """Diff a golden case against the oracle response.

    If the case pins a `channel` (e.g. "preferred_retail"), channel-level
    fields (cost, copay, coinsurance) are checked on that channel only.
    """
    failures = []
    if estimate.get("status") != "ok":
        failures.append(f"estimate status: expected ok, got {estimate.get('status')}")
        return failures

    data = estimate.get("data") or {}
    channels = data.get("channels") or {}
    channel = _channel_for_case(case, channels)

    if case.get("expected_tier") is not None and data.get("tier") != case["expected_tier"]:
        failures.append(f"tier: expected {case['expected_tier']}, got {data.get('tier')}")

    if case.get("expected_benefit_phase") is not None:
        actual = data.get("benefit_phase")
        if actual != case["expected_benefit_phase"]:
            failures.append(
                f"benefit_phase: expected {case['expected_benefit_phase']}, got {actual}"
            )

    if case.get("expected_effective_phase") is not None:
        actual = data.get("effective_phase")
        if actual != case["expected_effective_phase"]:
            failures.append(
                f"effective_phase: expected {case['expected_effective_phase']}, got {actual}"
            )

    if case.get("channel") and channel is None:
        failures.append(f"channel '{case['channel']}' missing from response")
        return failures

    if channel is not None:
        actual_low = channel.get("cost_low")
        actual_high = channel.get("cost_high", actual_low)

        if case.get("expect_cost_na"):
            if actual_low is not None or actual_high is not None:
                failures.append(
                    f"cost: expected NA, got low={actual_low} high={actual_high}"
                )
        else:
            if case.get("expected_cost_low") is not None:
                if actual_low is None:
                    failures.append("cost_low: expected a value, got None")
                elif actual_low != case["expected_cost_low"]:
                    failures.append(
                        f"cost_low: expected {case['expected_cost_low']}, got {actual_low}"
                    )
            if case.get("expected_cost_high") is not None:
                if actual_high is None:
                    failures.append("cost_high: expected a value, got None")
                elif actual_high != case["expected_cost_high"]:
                    failures.append(
                        f"cost_high: expected {case['expected_cost_high']}, got {actual_high}"
                    )

        for field, key in (
            ("plan_copay", "expected_plan_copay"),
            ("applied_copay", "expected_applied_copay"),
            ("plan_coinsurance_pct", "expected_plan_coinsurance_pct"),
            ("applied_coinsurance_pct", "expected_applied_coinsurance_pct"),
        ):
            expected = case.get(key)
            if expected is None:
                continue
            actual = channel.get(field)
            if actual != expected:
                failures.append(f"{field}: expected {expected}, got {actual}")

    elif case.get("expected_cost_low") is not None or case.get("expected_cost_high") is not None:
        lows = [c["cost_low"] for c in channels.values() if c.get("cost_low") is not None]
        highs = [
            c.get("cost_high", c.get("cost_low"))
            for c in channels.values()
            if c.get("cost_low") is not None
        ]
        if not lows:
            failures.append("no priced channel found")
        else:
            actual_low, actual_high = min(lows), max(highs)
            if case.get("expected_cost_low") is not None and actual_low != case["expected_cost_low"]:
                failures.append(f"cost_low: expected {case['expected_cost_low']}, got {actual_low}")
            if case.get("expected_cost_high") is not None and actual_high != case["expected_cost_high"]:
                failures.append(
                    f"cost_high: expected {case['expected_cost_high']}, got {actual_high}"
                )

    return failures


def _offline_post_estimate():
    """Fixture-backed in-process client for requires_live_ingest=false cases."""
    import tempfile

    from fastapi.testclient import TestClient

    from medicare_navigator.api.app import app
    from medicare_navigator.config import settings
    from tests.spuf_fixture import load_spuf_fixture

    tmp = Path(tempfile.mkdtemp())
    load_spuf_fixture(data_dir=tmp)
    settings.data_dir = tmp
    settings.duckdb_path = tmp / "navigator.duckdb"
    client = TestClient(app)

    def post_estimate(payload: dict) -> dict:
        return client.post("/api/estimate", json=payload).json()

    return post_estimate


def _live_post_estimate(base_url: str):
    """Real HTTP client against a running server with real ingested CMS data."""
    import httpx

    http_client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0)

    def post_estimate(payload: dict) -> dict:
        return http_client.post("/api/estimate", json=payload).json()

    return post_estimate


def run(*, include_live: bool, base_url: str, by_group: bool = False) -> int:
    """Fixture-only cases always run against the offline fixture DB (they only
    exist there); requires_live_ingest cases run against `base_url` only when
    --include-live is passed. Never cross the two — a fixture plan key will
    never resolve on a real CMS ingest and vice versa."""
    cases = _load_cases()
    offline_post_estimate = None
    live_post_estimate = None

    passed = 0
    skipped = 0
    total = 0
    group_totals: dict[str, list[bool]] = defaultdict(list)

    for case in cases:
        requires_live = bool(case.get("requires_live_ingest"))
        if requires_live and not include_live:
            skipped += 1
            print(f"[SKIP] {case['id']}: requires --include-live (real CMS ingest)")
            continue

        if requires_live:
            if live_post_estimate is None:
                live_post_estimate = _live_post_estimate(base_url)
            post_estimate = live_post_estimate
        else:
            if offline_post_estimate is None:
                offline_post_estimate = _offline_post_estimate()
            post_estimate = offline_post_estimate

        total += 1
        payload = {
            "plan_id": case["plan_id"],
            "drug": case["drug"],
            "days_supply": case.get("days_supply", 30),
            "ytd_oop_spend": case.get("ytd_oop_spend", 0),
        }
        if case.get("dosage") is not None:
            payload["dosage"] = case["dosage"]

        estimate = post_estimate(payload)
        failures = _check_case(case, estimate)
        label = f"{case['drug']} {case.get('dosage') or ''} on {case['plan_id']}"
        if case.get("channel"):
            label += f" [{case['channel']}]"
        group = case.get("case_group", "uncategorized")
        group_totals[group].append(not failures)

        if failures:
            print(f"[FAIL] {case['id']} ({group}): {label}")
            for f in failures:
                print(f"       - {f}")
        else:
            passed += 1
            print(f"[PASS] {case['id']} ({group}): {label}")

    print(f"\nResults: {passed}/{total} passed, {skipped} skipped (use --include-live to run them)")

    if by_group and group_totals:
        print("\nBy case_group:")
        for group in sorted(group_totals):
            wins = sum(group_totals[group])
            count = len(group_totals[group])
            print(f"  {group}: {wins}/{count} passed")

    return 0 if passed == total else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-live",
        action="store_true",
        help="Also run cases requiring a real CMS ingest (needs --base-url pointed at a live server)",
    )
    parser.add_argument("--base-url", default="http://localhost:8000", help="Live API base URL")
    parser.add_argument(
        "--by-group",
        action="store_true",
        help="Print pass counts grouped by case_group after the run",
    )
    args = parser.parse_args()
    sys.exit(run(include_live=args.include_live, base_url=args.base_url, by_group=args.by_group))


if __name__ == "__main__":
    main()
