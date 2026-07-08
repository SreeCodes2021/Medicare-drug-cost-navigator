"""Coverage for docs/navigator-implementation-spec.md Section 5's Bugs 1-6, plus the
insulin/suppressed-plan hard stops and prior-authorization/step-therapy caveat."""

import pytest

from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.disclaimers import BUG2_CAVEAT, BUG4_CAVEAT, NO_COST_SHARE_DATA_MESSAGE
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP, PLAN_FL_SUPPRESSED


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.mark.asyncio
async def test_bug6_suppressed_plan_is_hard_stop():
    result = await estimate_drug_cost(plan_key=PLAN_FL_SUPPRESSED, drug_name="metformin")
    assert result.status == ToolStatus.suppressed
    assert result.data is None
    assert "suppress" in result.message.lower()


@pytest.mark.asyncio
async def test_bug6_suppressed_plan_is_still_ingested_and_selectable():
    """Regression guard: Bug 6 requires the plan to be resolvable, not filtered at ingest."""
    from medicare_navigator.storage.repository import PlanRepository

    plan = PlanRepository().get_plan(PLAN_FL_SUPPRESSED)
    assert plan is not None
    assert plan["plan_suppressed"] is True


@pytest.mark.asyncio
async def test_insulin_routes_to_out_of_scope_before_formulary_lookup():
    result = await estimate_drug_cost(plan_key=PLAN_FL_PDP, drug_name="lantus")
    assert result.status == ToolStatus.insulin_out_of_scope
    assert "insulin" in result.message.lower()


@pytest.mark.asyncio
async def test_bug3_unit_cost_to_fill_cost_conversion():
    """Omeprazole is tier 3 (deductible applies, no Bug 2 exemption) so pre-deductible cost
    is unit_cost * fill_quantity, not the bare per-unit price: 0.35 * 90 = 31.50."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="omeprazole", days_supply=90, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "pre_deductible"
    assert result.data.cost_low == pytest.approx(31.50)
    assert result.data.cost_high == pytest.approx(31.50)


@pytest.mark.asyncio
async def test_bug2_per_tier_deductible_exemption_overrides_phase():
    """Tier 1 (metformin) has DED_APPLIES_YN=N -> even pre-deductible YTD spend uses the
    initial-coverage copay, not full price. Tier 3 (omeprazole) has DED_APPLIES_YN=Y -> stays
    at full price pre-deductible. The Bug 2 disclaimer is present in both cases."""
    exempt = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="metformin", days_supply=30, ytd_oop_spend=0
    )
    assert exempt.status == ToolStatus.ok
    assert exempt.data.benefit_phase == "pre_deductible"
    # tier-1 copay at days_supply_code=1 (30 day) is $5.00 preferred_retail
    assert exempt.data.cost_low == pytest.approx(5.00)
    assert BUG2_CAVEAT in exempt.data.caveats

    not_exempt = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="omeprazole", days_supply=30, ytd_oop_spend=0
    )
    assert not_exempt.status == ToolStatus.ok
    assert not_exempt.data.benefit_phase == "pre_deductible"
    # tier-3 full price: unit_cost 0.35 * 30 = 10.50 (no override, deductible applies)
    assert not_exempt.data.cost_low == pytest.approx(10.50)
    assert BUG2_CAVEAT in not_exempt.data.caveats


@pytest.mark.asyncio
async def test_bug4_coinsurance_excluded_from_cost_range():
    """Tier 2 (januvia) is coinsurance-typed. Past the deductible, coinsurance must not
    produce a dollar figure — only the verbatim Bug 4 disclaimer."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="januvia", days_supply=30, ytd_oop_spend=700
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "initial_coverage"
    assert result.data.cost_low is None
    assert result.data.cost_high is None
    assert BUG4_CAVEAT in result.data.caveats


@pytest.mark.asyncio
async def test_bug5_multiple_ndcs_same_tier_produce_a_range():
    """Metformin matches 2 NDCs, both tier 1, with different unit costs -> range, not a
    single figure; same_tier flag set; stale FORMULARY_VERSION 00000 row (tier 9) excluded."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="metformin", days_supply=90, ytd_oop_spend=700
    )
    assert result.status == ToolStatus.ok
    assert result.data.matched_ndc_count == 2
    assert result.data.same_tier is True
    assert result.data.tiers_matched == [1]
    assert 9 not in result.data.tiers_matched


@pytest.mark.asyncio
async def test_bug5_multiple_ndcs_cross_tier_flagged_more_severely():
    """Lisinopril matches NDCs at tier 1 and tier 2 -> same_tier False, stronger caveat."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="lisinopril", days_supply=30, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert result.data.matched_ndc_count == 2
    assert result.data.same_tier is False
    assert sorted(result.data.tiers_matched) == [1, 2]
    assert any("different tiers" in c for c in result.data.caveats)


@pytest.mark.asyncio
async def test_bug5b_quantity_limit_blocks_oversized_fill():
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="januvia", days_supply=90, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.quantity_limit_blocked
    assert result.data.quantity_limit_blocked is True
    assert result.data.max_allowed_days_supply == 30
    assert "30" in result.message


@pytest.mark.asyncio
async def test_bug5b_within_limit_is_not_blocked():
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="januvia", days_supply=30, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert result.data.quantity_limit_blocked is False


@pytest.mark.asyncio
async def test_prior_authorization_and_step_therapy_caveat_not_hard_stop():
    """Omeprazole (tier 3) requires PA + ST — a cost is still returned, with a caveat,
    per the spec's contrast with Bug 6's true hard stop."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="omeprazole", days_supply=30, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert result.data.cost_low is not None
    assert any("prior authorization" in c.lower() for c in result.data.caveats)


@pytest.mark.asyncio
async def test_bug1_days_supply_code_mapping_not_conflated_with_raw_count():
    """60-day pricing must resolve via days_supply_code=4 (beneficiary_cost), not be
    confused with the raw day count. Tier-1 60-day copay is $10.00 vs 30-day's $5.00."""
    result_30 = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="metformin", days_supply=30, ytd_oop_spend=0
    )
    result_60 = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="metformin", days_supply=60, ytd_oop_spend=0
    )
    assert result_30.data.cost_low == pytest.approx(5.00)
    assert result_60.data.cost_low == pytest.approx(10.00)


@pytest.mark.asyncio
async def test_plan_not_found():
    result = await estimate_drug_cost(plan_key="ZZZZ-999", drug_name="metformin")
    assert result.status == ToolStatus.not_found


@pytest.mark.asyncio
async def test_drug_not_on_formulary():
    result = await estimate_drug_cost(plan_key=PLAN_FL_MAPD, drug_name="omeprazole")
    assert result.status == ToolStatus.not_covered


@pytest.mark.asyncio
async def test_ma_pd_zero_deductible_plan_always_initial_coverage():
    result = await estimate_drug_cost(plan_key=PLAN_FL_MAPD, drug_name="metformin", ytd_oop_spend=0)
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "initial_coverage"
    assert result.data.cost_low == pytest.approx(8.00)


@pytest.mark.asyncio
async def test_missing_cost_share_row_is_flagged_not_silently_empty():
    """Live-reproduced gap: tier 1 has beneficiary_cost rows for DAYS_SUPPLY codes 1 (30-day)
    and 4 (60-day) only — no code 2 (90-day) row. A 90-day, post-deductible request for
    metformin must not come back status=ok with blank cost_low/cost_high and no explanation;
    it must carry NO_COST_SHARE_DATA_MESSAGE, and must NOT claim a (nonexistent) multi-NDC
    price range via the Bug 5 caveat."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="metformin", days_supply=90, ytd_oop_spend=700
    )
    assert result.status == ToolStatus.ok
    assert result.data.cost_low is None
    assert result.data.cost_high is None
    assert NO_COST_SHARE_DATA_MESSAGE in result.data.caveats
    assert not any("formulary NDCs" in c for c in result.data.caveats)


@pytest.mark.asyncio
async def test_unmapped_days_supply_without_cost_does_not_claim_ingredient_cost():
    """days_supply=45 has no beneficiary_cost CODE at all (Section 4's "other" branch). In the
    initial-coverage phase there is no pricing-table fallback, so no ingredient cost is ever
    computed either — the caveat must not falsely claim "the estimate below reflects ingredient
    cost only" when cost_low/cost_high are both None."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="metformin", days_supply=45, ytd_oop_spend=700
    )
    assert result.status == ToolStatus.ok
    assert result.data.cost_low is None
    assert result.data.cost_high is None
    assert any("45-day supply" in c for c in result.data.caveats)
    assert not any("reflects ingredient cost only" in c for c in result.data.caveats)
