from medicare_navigator.tools.part_d_benefit_params import (
    annual_oop_cap,
    cap_fill_copay,
    effective_tier_cost_ceiling,
    project_annual_budget,
)


def test_annual_oop_cap_by_year():
    assert annual_oop_cap(2025) == 2000.0
    assert annual_oop_cap(2026) == 2100.0


def test_effective_tier_cost_ceiling_uses_min_of_cms_and_statutory():
    assert effective_tier_cost_ceiling(2500.0, 2026) == 2100.0
    assert effective_tier_cost_ceiling(45.0, 2026) == 45.0
    assert effective_tier_cost_ceiling(None, 2026) is None


def test_cap_fill_copay():
    assert cap_fill_copay(95.0, 50.0, 2026) == 50.0
    assert cap_fill_copay(95.0, 2500.0, 2026) == 95.0


def test_project_annual_budget_clamps_at_cap():
    cap, headroom, low, high = project_annual_budget(
        ytd_oop_spend=0,
        days_supply=30,
        cost_low=200.0,
        cost_high=200.0,
        contract_year=2026,
    )
    assert cap == 2100.0
    assert headroom == 2100.0
    assert low == 2100.0
    assert high == 2100.0
