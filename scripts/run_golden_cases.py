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
"""

from __future__ import annotations

import argparse
import json
import sys
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


def _check_case(case: dict, estimate: dict) -> list[str]:
    """Diff a golden case against the oracle response.

    If the case pins a `channel` (e.g. "preferred_retail"), check that
    channel only — CMS pricing legitimately differs per pharmacy channel
    (preferred vs. standard, retail vs. mail), so aggregating min/max across
    all four channels would silently mask a real per-channel regression, or
    flag a false failure when only one channel's price actually moved.
    """
    failures = []
    data = estimate.get("data") or {}
    channels = data.get("channels") or {}

    channel_name = case.get("channel")
    if channel_name:
        channel = channels.get(channel_name)
        if channel is None or channel.get("cost_low") is None:
            failures.append(f"channel '{channel_name}' has no price (status={estimate.get('status')})")
            return failures
        actual_low = channel["cost_low"]
        actual_high = channel.get("cost_high", actual_low)
    else:
        lows = [c["cost_low"] for c in channels.values() if c.get("cost_low") is not None]
        highs = [c.get("cost_high", c.get("cost_low")) for c in channels.values() if c.get("cost_low") is not None]
        if not lows:
            failures.append(f"no priced channel found (status={estimate.get('status')})")
            return failures
        actual_low, actual_high = min(lows), max(highs)

    if case.get("expected_cost_low") is not None and actual_low != case["expected_cost_low"]:
        failures.append(f"cost_low: expected {case['expected_cost_low']}, got {actual_low}")
    if case.get("expected_cost_high") is not None and actual_high != case["expected_cost_high"]:
        failures.append(f"cost_high: expected {case['expected_cost_high']}, got {actual_high}")
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


def run(*, include_live: bool, base_url: str) -> int:
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
            "dosage": case.get("dosage"),
            "days_supply": case.get("days_supply", 30),
            "ytd_oop_spend": case.get("ytd_oop_spend", 0),
        }
        estimate = post_estimate(payload)
        failures = _check_case(case, estimate)
        label = f"{case['drug']} {case.get('dosage', '')} on {case['plan_id']}"
        if case.get("channel"):
            label += f" [{case['channel']}]"
        if failures:
            print(f"[FAIL] {case['id']}: {label}")
            for f in failures:
                print(f"       - {f}")
        else:
            passed += 1
            print(f"[PASS] {case['id']}: {label}")

    print(f"\nResults: {passed}/{total} passed, {skipped} skipped (use --include-live to run them)")
    return 0 if passed == total else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-live",
        action="store_true",
        help="Also run cases requiring a real CMS ingest (needs --base-url pointed at a live server)",
    )
    parser.add_argument("--base-url", default="http://localhost:8000", help="Live API base URL")
    args = parser.parse_args()
    sys.exit(run(include_live=args.include_live, base_url=args.base_url))


if __name__ == "__main__":
    main()
