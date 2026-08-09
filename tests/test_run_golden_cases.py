"""Regression guard for scripts/run_golden_cases.py offline fixture cases."""

from scripts.run_golden_cases import _load_cases, run


def test_offline_golden_cases_all_pass():
    cases = _load_cases()
    offline = [c for c in cases if not c.get("requires_live_ingest")]
    assert len(offline) >= 32
    groups = {c.get("case_group") for c in offline}
    for required in (
        "tier_lookup",
        "channel",
        "benefit_phase",
        "copay",
        "coinsurance",
        "estimated_cost_copay",
        "estimated_cost_coinsurance",
    ):
        assert required in groups
    assert run(include_live=False, base_url="http://localhost:8000", by_group=False) == 0
