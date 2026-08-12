"""Run fixed live-LLM scenario suites for quality-test sub-skills (no ad-hoc agent scripts).

Suites live in `scripts/llm_scenario_suites/*.json` — mirrors the markdown catalogs in
`.cursor/skills/quality-test/*/llm-scenarios.md`. Each scenario invokes the real
`medicare-chat-invoke` CLI (never LLM_MOCK), optionally fetches batch/single estimate
oracles, and applies lightweight automated checks. Full rubric grading still uses the
printed JSON or `--output json` bundle.

Usage:
    # List suites / scenarios (no API calls)
    python scripts/run_llm_scenarios.py --list
    python scripts/run_llm_scenarios.py --suite mixed-basket --dry-run

    # Run a suite (default model gpt-5.6-luna)
    python scripts/run_llm_scenarios.py --suite mixed-basket
    python scripts/run_llm_scenarios.py --suite insulin --failures-only
    python scripts/run_llm_scenarios.py --suite quality-test-2g

    # Single scenario or JSON output for agent grading
    python scripts/run_llm_scenarios.py --suite mixed-basket --scenario M3-2
    python scripts/run_llm_scenarios.py --suite mixed-basket --output json > /tmp/mixed.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUITES_DIR = Path(__file__).resolve().parent / "llm_scenario_suites"

SUITE_ALIASES = {
    "mixed-basket": "mixed_basket.json",
    "mixed_basket": "mixed_basket.json",
    "insulin": "insulin.json",
    "quality-test-2g": "quality_test_2g.json",
    "quality_test_2g": "quality_test_2g.json",
    "2g": "quality_test_2g.json",
}


@dataclass
class TurnResult:
    message: str
    ok: bool
    session_id: str | None = None
    status: str | None = None
    response_source: str | None = None
    explanation: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class ScenarioResult:
    id: str
    turns: list[TurnResult]
    oracle: dict[str, Any] | None = None
    auto_failures: list[str] = field(default_factory=list)
    query_count: int = 0


def _load_suite(name: str) -> dict[str, Any]:
    filename = SUITE_ALIASES.get(name)
    if not filename:
        known = ", ".join(sorted({k for k in SUITE_ALIASES if "-" in k or k == "insulin"}))
        raise SystemExit(f"Unknown suite '{name}'. Known: {known}")
    path = SUITES_DIR / filename
    if not path.is_file():
        raise SystemExit(f"Suite file not found: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _grading_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    grading = bundle.get("grading")
    if isinstance(grading, dict):
        return grading
    response = bundle.get("response") or {}
    inner = response.get("grading")
    if isinstance(inner, dict):
        return inner
    return {}


def _invoke_chat(
    message: str,
    *,
    model: str,
    session_id: str | None = None,
    filters_json: dict[str, Any] | None = None,
) -> TurnResult:
    cmd = [
        "medicare-chat-invoke",
        "send",
        "--message",
        message,
        "--model",
        model,
    ]
    if session_id:
        cmd.extend(["--session-id", session_id])
    if filters_json:
        cmd.extend(["--filters-json", json.dumps(filters_json)])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return TurnResult(
            message=message,
            ok=False,
            error="medicare-chat-invoke not found — pip install -e '.[dev]'",
        )

    if proc.returncode != 0:
        return TurnResult(
            message=message,
            ok=False,
            error=(proc.stderr or proc.stdout or "invoke failed").strip(),
        )

    try:
        bundle = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return TurnResult(
            message=message,
            ok=False,
            error=f"invalid JSON from invoke: {exc}",
        )

    if not bundle.get("ok"):
        return TurnResult(
            message=message,
            ok=False,
            error=str(bundle.get("error") or "invoke returned ok=false"),
            raw=bundle,
        )

    grading = _grading_from_bundle(bundle)
    return TurnResult(
        message=message,
        ok=True,
        session_id=bundle.get("session_id"),
        status=grading.get("status"),
        response_source=grading.get("response_source"),
        explanation=grading.get("explanation") or "",
        raw=bundle,
    )


def _http_post_json(base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_oracle(
    base_url: str,
    scenario: dict[str, Any],
) -> dict[str, Any] | None:
    if scenario.get("batch"):
        return _http_post_json(base_url, "/api/estimate-batch", scenario["batch"])
    if scenario.get("estimate"):
        return _http_post_json(base_url, "/api/estimate", scenario["estimate"])
    return None


def _prose_checks(
    prose: str,
    expect: dict[str, Any],
    *,
    prefix: str = "",
) -> list[str]:
    failures: list[str] = []
    lower = prose.lower()

    for drug in expect.get("drugs_named") or []:
        if drug.lower() not in lower:
            failures.append(f"{prefix}missing drug in prose: {drug}")

    for substring in expect.get("prose_contains") or []:
        if substring.lower() not in lower:
            failures.append(f"{prefix}prose missing: {substring}")

    any_list = expect.get("prose_contains_any")
    if any_list and not any(s.lower() in lower for s in any_list):
        failures.append(f"{prefix}prose missing any of: {any_list}")

    for substring in expect.get("forbid_substrings") or []:
        if substring.lower() in lower:
            failures.append(f"{prefix}forbidden substring in prose: {substring}")

    return failures


def _auto_check(
    scenario: dict[str, Any],
    turns: list[TurnResult],
    oracle: dict[str, Any] | None,
) -> list[str]:
    expect = scenario.get("expect") or {}
    failures: list[str] = []
    primary = turns[0]

    if not primary.ok:
        failures.append(primary.error or "invoke failed")
        return failures

    if primary.response_source and str(primary.response_source).startswith("mock/"):
        failures.append(f"mock response_source: {primary.response_source}")

    expected_status = expect.get("status")
    if expected_status and primary.status != expected_status:
        failures.append(f"status: expected {expected_status}, got {primary.status}")

    prefix = expect.get("response_source_prefix")
    if prefix and primary.response_source and not str(primary.response_source).startswith(prefix):
        failures.append(
            f"response_source: expected prefix {prefix}, got {primary.response_source}"
        )

    failures.extend(_prose_checks(primary.explanation, expect))

    follow_expect = {
        "prose_contains": expect.get("follow_up_prose_contains"),
        "prose_contains_any": expect.get("follow_up_prose_contains_any"),
        "forbid_substrings": expect.get("follow_up_forbid_substrings"),
        "drugs_named": expect.get("follow_up_drugs_named"),
    }
    if len(turns) > 1 and any(follow_expect.values()):
        failures.extend(_prose_checks(turns[-1].explanation, follow_expect, prefix="follow-up: "))

    if oracle and scenario.get("batch"):
        low = oracle.get("combined_total_low")
        high = oracle.get("combined_total_high")
        if low is not None and high is not None and primary.explanation:
            snippet = primary.explanation.split("Disclaimer")[0]
            if "$" in snippet:
                nums = [float(x) for x in re.findall(r"\$(\d+(?:\.\d+)?)", snippet)]
                if nums and not any(low - 5 <= n <= high + 5 for n in nums):
                    failures.append(
                        f"no prose $ near batch combined oracle {low}-{high}"
                    )

    return failures


def _run_scenario(
    scenario: dict[str, Any],
    *,
    model: str,
    base_url: str,
    fetch_oracle: bool,
) -> ScenarioResult:
    turns: list[TurnResult] = []
    oracle: dict[str, Any] | None = None

    if fetch_oracle and (scenario.get("batch") or scenario.get("estimate")):
        try:
            oracle = _fetch_oracle(base_url, scenario)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            oracle = {"error": str(exc)}

    turn = _invoke_chat(
        scenario["message"],
        model=model,
        filters_json=scenario.get("filters_json"),
    )
    turns.append(turn)
    query_count = 1

    follow = scenario.get("follow_up")
    if follow and turn.ok and turn.session_id:
        follow_turn = _invoke_chat(
            follow["message"],
            model=model,
            session_id=turn.session_id,
            filters_json=follow.get("filters_json"),
        )
        turns.append(follow_turn)
        query_count += 1

    auto_failures = _auto_check(scenario, turns, oracle)

    return ScenarioResult(
        id=scenario["id"],
        turns=turns,
        oracle=oracle,
        auto_failures=auto_failures,
        query_count=query_count,
    )


def _check_health(base_url: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["medicare-chat-invoke", "health"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return False, proc.stderr or proc.stdout or "health check failed"
        data = json.loads(proc.stdout)
        if not data.get("ok"):
            return False, "medicare-chat-invoke health returned ok=false"
        health = data.get("health") or {}
        if not health.get("llm_configured"):
            return False, "llm_configured is false — enable real LLM before grading"
        return True, base_url
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return False, str(exc)


def _print_text_result(result: ScenarioResult, failures_only: bool) -> None:
    primary = result.turns[0]
    failed = bool(result.auto_failures) or not primary.ok
    if failures_only and not failed:
        return

    tag = "FAIL" if failed else "PASS"
    print(f"[{tag}] {result.id}  queries={result.query_count}")
    if not primary.ok:
        print(f"  error: {primary.error}")
    else:
        print(f"  status={primary.status}  source={primary.response_source}")
        snippet = primary.explanation.split("Disclaimer")[0].replace("\n", " ")[:200]
        print(f"  prose: {snippet}")
    for issue in result.auto_failures:
        print(f"  - {issue}")
    if len(result.turns) > 1:
        fu = result.turns[-1]
        print(f"  follow-up status={fu.status} source={fu.response_source}")
    if result.oracle and result.oracle.get("error"):
        print(f"  oracle error: {result.oracle['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed live-LLM quality-test scenario suites")
    parser.add_argument(
        "--suite",
        help="Suite name: mixed-basket, insulin, quality-test-2g",
    )
    parser.add_argument("--scenario", help="Run a single scenario id from the suite")
    parser.add_argument("--model", help="Override default model from suite JSON")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base for oracles")
    parser.add_argument("--no-oracle", action="store_true", help="Skip batch/estimate oracle fetch")
    parser.add_argument("--dry-run", action="store_true", help="List scenarios without invoking")
    parser.add_argument("--list", action="store_true", help="List available suites")
    parser.add_argument(
        "--failures-only",
        action="store_true",
        help="Print only scenarios with auto-check failures",
    )
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Output format (json for agent grading)",
    )
    parser.add_argument(
        "--skip-health",
        action="store_true",
        help="Skip medicare-chat-invoke health check",
    )
    args = parser.parse_args()

    if args.list:
        seen: set[str] = set()
        print("Suites (scripts/llm_scenario_suites/):")
        for filename in sorted(SUITES_DIR.glob("*.json")):
            with filename.open(encoding="utf-8") as f:
                data = json.load(f)
            name = data.get("suite") or filename.stem
            if name in seen:
                continue
            seen.add(name)
            count = len(data.get("scenarios") or [])
            print(f"  {name}: {count} scenarios")
        print("\nAliases:", ", ".join(sorted(SUITE_ALIASES.keys())))
        return

    if not args.suite:
        parser.error("--suite is required (or use --list)")

    suite = _load_suite(args.suite)
    model = args.model or suite.get("default_model") or "gpt-5.6-luna"
    scenarios = suite.get("scenarios") or []

    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] == args.scenario]
        if not scenarios:
            raise SystemExit(f"Scenario {args.scenario} not found in suite {args.suite}")

    if args.dry_run:
        print(f"Suite {suite.get('suite')} — model {model} — {len(scenarios)} scenario(s)")
        for s in scenarios:
            extra = " +follow-up" if s.get("follow_up") else ""
            print(f"  {s['id']}{extra}: {s['message'][:80]}")
        return

    if not args.skip_health:
        ok, msg = _check_health(args.base_url)
        if not ok:
            raise SystemExit(f"Health check failed: {msg}")

    results: list[ScenarioResult] = []
    total_queries = 0
    for scenario in scenarios:
        result = _run_scenario(
            scenario,
            model=model,
            base_url=args.base_url,
            fetch_oracle=not args.no_oracle,
        )
        results.append(result)
        total_queries += result.query_count

    if args.output == "json":
        payload = {
            "suite": suite.get("suite"),
            "model": model,
            "base_url": args.base_url,
            "query_count": total_queries,
            "results": [
                {
                    "id": r.id,
                    "query_count": r.query_count,
                    "auto_failures": r.auto_failures,
                    "oracle": r.oracle,
                    "turns": [
                        {
                            "message": t.message,
                            "ok": t.ok,
                            "status": t.status,
                            "response_source": t.response_source,
                            "session_id": t.session_id,
                            "explanation": t.explanation,
                            "error": t.error,
                        }
                        for t in r.turns
                    ],
                }
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2))
        return

    fail_count = sum(1 for r in results if r.auto_failures or not r.turns[0].ok)
    print(f"Suite {suite.get('suite')} — {len(results)} scenarios, {total_queries} queries, model {model}")
    for result in results:
        _print_text_result(result, args.failures_only)
    if not args.failures_only:
        print(f"\nSummary: {len(results) - fail_count}/{len(results)} auto-pass, {fail_count} need review")
    else:
        print(f"\n{fail_count} scenario(s) with failures or invoke errors")


if __name__ == "__main__":
    main()
