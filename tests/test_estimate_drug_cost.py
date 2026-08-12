"""Coverage for docs/navigator-implementation-spec.md Section 5's Bugs 1-6, plus the
insulin/suppressed-plan hard stops and prior-authorization/step-therapy caveat."""

import pytest

from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.disclaimers import (
    BUG2_CAVEAT,
    BUG4_CAVEAT,
    INSULIN_STATUTORY_CAP_CAVEAT,
    NO_COST_SHARE_DATA_MESSAGE,
)
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost, estimate_drug_cost_all_channels
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PARTIAL_CHANNELS, PLAN_FL_PDP, PLAN_FL_SUPPRESSED


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.mark.asyncio
async def test_bug6_suppressed_plan_is_hard_stop():
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_SUPPRESSED, drug_name="metformin", dosage="500mg"
    )
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
async def test_insulin_returns_real_capped_estimate_not_hard_stop():
    """Fixture: S9999-001 tier 3, 30-day preferred_retail copay is $35.00 (at the
    statutory cap) — lantus must now be priced, not hard-stopped."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=30, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "insulin_cap"
    assert result.data.cost_low == pytest.approx(35.00)
    assert result.data.cost_high == pytest.approx(35.00)
    assert INSULIN_STATUTORY_CAP_CAVEAT in result.data.caveats
    assert BUG2_CAVEAT not in result.data.caveats


@pytest.mark.asyncio
async def test_insulin_60_and_90_day_scaling():
    """Fixture: preferred_retail copay is $35/$70/$105 for 30/60/90-day — the statutory
    cap scaled by the 30-day multiple, sourced directly from CMS's own days-supply CODE,
    not computed via a local ceil(days_supply/30)."""
    result_60 = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=60, ytd_oop_spend=0
    )
    result_90 = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=90, ytd_oop_spend=0
    )
    assert result_60.data.cost_low == pytest.approx(70.00)
    assert result_90.data.cost_low == pytest.approx(105.00)


@pytest.mark.asyncio
async def test_insulin_catastrophic_phase_overrides_cap_to_zero():
    """Once YTD OOP crosses the annual cap, insulin is $0 like every other covered
    drug — the $35 statutory cap is a pre-catastrophic ceiling, not a fixed price."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=30, ytd_oop_spend=2200
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "catastrophic"
    assert result.data.cost_low == pytest.approx(0.0)
    assert result.data.cost_high == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_insulin_no_deductible_phase_and_channel_differentiation():
    """Multi-channel view: ded_applies_yn is forced NA (insulin has no deductible phase,
    so the general beneficiary_cost table's per-tier flag for an unrelated drug at the
    same tier number must never leak through), and channels differ per the fixture
    (preferred_mail $30 vs preferred_retail $35 at 30-day)."""
    result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=30, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "insulin_cap"
    assert result.data.ded_applies_yn == "NA"
    assert result.data.channels["preferred_retail"].cost_low == pytest.approx(35.00)
    assert result.data.channels["preferred_mail"].cost_low == pytest.approx(30.00)


@pytest.mark.asyncio
async def test_insulin_narrow_fallback_when_plan_has_no_cost_share_data():
    """H8888-001 (PLAN_FL_MAPD) has lantus on its formulary (tier 2) but no insulin
    cost-share row at all — a genuine CMS data gap, distinct from "insulin unsupported".
    Must not silently fall through to the general (wrong) tiered pricing path either."""
    result = await estimate_drug_cost(plan_key=PLAN_FL_MAPD, drug_name="lantus")
    assert result.status == ToolStatus.insulin_out_of_scope
    assert "cost-share" in result.message.lower()
    assert result.data is not None
    assert result.data.covered is True


@pytest.mark.asyncio
async def test_negative_days_supply_is_rejected():
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="metformin", dosage="500mg", days_supply=-30
    )
    assert result.status == ToolStatus.no_match
    assert result.data is None
    assert "30" in result.message


@pytest.mark.asyncio
async def test_insulin_unmapped_days_supply_returns_ok_without_dollar_estimate():
    """Fill sizes outside 30/60/90 skip the insulin existence gate and return ok with
    no cost + unmapped-days-supply caveat — same contract as the general pipeline."""
    from medicare_navigator.tools.disclaimers import unmapped_days_supply_caveat

    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=45, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "insulin_cap"
    assert result.data.cost_low is None
    assert result.data.cost_high is None
    assert any(
        unmapped_days_supply_caveat(days_supply=45, has_cost=False) in c
        for c in result.data.caveats
    )


@pytest.mark.asyncio
async def test_insulin_under_cap_tier_one_copay():
    """Tier-1 humalog fixture copay is $10 — below the $35 statutory cap, unchanged."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="humalog", days_supply=30, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "insulin_cap"
    assert result.data.tiers_matched == [1]
    assert result.data.cost_low == pytest.approx(10.0)
    assert result.data.cost_high == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_insulin_all_channels_cost_range():
    """Lantus tier 3: preferred_mail $30 vs preferred_retail $35 — range, not a blend."""
    result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=30, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    lows = [c.cost_low for c in result.data.channels.values() if c.cost_low is not None]
    highs = [c.cost_high or c.cost_low for c in result.data.channels.values() if c.cost_low is not None]
    assert min(lows) == pytest.approx(30.0)
    assert max(highs) == pytest.approx(35.0)


@pytest.mark.asyncio
async def test_insulin_statutory_cap_caveat_attached():
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=30, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert INSULIN_STATUTORY_CAP_CAVEAT in result.data.caveats


@pytest.mark.asyncio
async def test_insulin_null_tier_fallback_on_defined_standard_plan():
    """S9999-004 has only a CMS '.' (NULL-tier) insulin row — tier-1 humalog falls back."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PARTIAL_CHANNELS,
        drug_name="humalog",
        days_supply=30,
        ytd_oop_spend=0,
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "insulin_cap"
    assert result.data.cost_low == pytest.approx(12.0)
    assert result.data.cost_high == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_insulin_brand_rxcui_falls_back_to_formulary_sbd(monkeypatch):
    """Bare 'lantus' may resolve to brand RXCUI 261551 while CMS formulary rows use SBD
    285018 for the same product — expand via strength concepts before not_covered."""
    from unittest.mock import AsyncMock

    import importlib

    from medicare_navigator.storage.repository import BasicDrugsFormularyRepository

    edc = importlib.import_module("medicare_navigator.tools.estimate_drug_cost")
    real_get_matches = BasicDrugsFormularyRepository.get_matches
    real_get_matches_any = BasicDrugsFormularyRepository.get_matches_any

    def get_matches_hide_brand(self, formulary_id, rxcui):
        if str(rxcui) == "261551":
            return []
        return real_get_matches(self, formulary_id, rxcui)

    def get_matches_any_remap(self, formulary_id, rxcuis):
        if "285018" in [str(r) for r in rxcuis]:
            rows = real_get_matches(self, formulary_id, "261551")
            for row in rows:
                row.rxcui = "285018"
            return rows
        return real_get_matches_any(self, formulary_id, rxcuis)

    monkeypatch.setattr(BasicDrugsFormularyRepository, "get_matches", get_matches_hide_brand)
    monkeypatch.setattr(
        edc,
        "list_strength_concepts",
        AsyncMock(
            return_value=[
                {
                    "rxcui": "285018",
                    "name": "insulin glargine 100 UNT/ML Injectable Solution [Lantus]",
                    "concept_name": "insulin glargine 100 UNT/ML Injectable Solution [Lantus]",
                }
            ]
        ),
    )
    monkeypatch.setattr(BasicDrugsFormularyRepository, "get_matches_any", get_matches_any_remap)

    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=30, ytd_oop_spend=0
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "insulin_cap"
    assert result.data.cost_low == pytest.approx(35.00)
    assert result.data.rxcui == "285018"


@pytest.mark.asyncio
async def test_bug3_unit_cost_to_fill_cost_conversion():
    """Omeprazole is tier 3 (deductible applies, no Bug 2 exemption) so pre-deductible cost
    is unit_cost * fill_quantity, not the bare per-unit price: 0.35 * 90 = 31.50."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP,
        drug_name="omeprazole",
        dosage="20mg",
        days_supply=90,
        ytd_oop_spend=0,
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
        plan_key=PLAN_FL_PDP,
        drug_name="metformin",
        dosage="500mg",
        days_supply=30,
        ytd_oop_spend=0,
    )
    assert exempt.status == ToolStatus.ok
    assert exempt.data.benefit_phase == "pre_deductible"
    # tier-1 copay at days_supply_code=1 (30 day) is $5.00 preferred_retail
    assert exempt.data.cost_low == pytest.approx(5.00)
    assert BUG2_CAVEAT in exempt.data.caveats

    not_exempt = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP,
        drug_name="omeprazole",
        dosage="20mg",
        days_supply=30,
        ytd_oop_spend=0,
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
        plan_key=PLAN_FL_PDP,
        drug_name="januvia",
        days_supply=30,
        ytd_oop_spend=700,
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
        plan_key=PLAN_FL_PDP,
        drug_name="metformin",
        dosage="500mg",
        days_supply=90,
        ytd_oop_spend=700,
    )
    assert result.status == ToolStatus.ok
    assert result.data.matched_ndc_count == 2
    assert result.data.same_tier is True
    assert result.data.tiers_matched == [1]
    assert 9 not in result.data.tiers_matched


@pytest.mark.asyncio
async def test_catastrophic_phase_when_ytd_at_or_above_annual_oop_cap():
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP,
        drug_name="lisinopril",
        dosage="10mg",
        days_supply=30,
        ytd_oop_spend=2200,
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "catastrophic"
    assert result.data.cost_low == pytest.approx(0.0)
    assert result.data.cost_high == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_catastrophic_phase_unmapped_days_supply_does_not_fabricate_zero_cost():
    """Regression guard: an unmapped days-supply (no beneficiary_cost CODE at all, same gap as
    test_unmapped_days_supply_without_cost_does_not_claim_ingredient_cost) must not fabricate a
    $0.00 catastrophic-phase cost. Every dollar figure must trace back to a real CMS
    coverage_level=3 record — the same standard already enforced for every other phase."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP,
        drug_name="lisinopril",
        dosage="10mg",
        days_supply=45,
        ytd_oop_spend=2200,
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "catastrophic"
    assert result.data.cost_low is None
    assert result.data.cost_high is None
    assert any("45-day supply" in c for c in result.data.caveats)
    assert not any("reflects ingredient cost only" in c for c in result.data.caveats)


@pytest.mark.asyncio
async def test_common_drug_without_dosage_requires_strength_via_estimate(spuf_db):
    from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels

    result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP,
        drug_name="metformin",
        days_supply=30,
        ytd_oop_spend=0,
    )
    assert result.status == ToolStatus.needs_dosage
    assert "500mg" in result.message


@pytest.mark.asyncio
async def test_januvia_without_dosage_still_estimates_brand_rxcui():
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="januvia", days_supply=30, ytd_oop_spend=700
    )
    assert result.status == ToolStatus.ok


@pytest.mark.asyncio
async def test_bug5_multiple_ndcs_cross_tier_flagged_more_severely():
    """Lisinopril matches NDCs at tier 1 and tier 2 -> same_tier False, stronger caveat."""
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP, drug_name="lisinopril", dosage="10mg", days_supply=30, ytd_oop_spend=0
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
        plan_key=PLAN_FL_PDP,
        drug_name="omeprazole",
        dosage="20mg",
        days_supply=30,
        ytd_oop_spend=0,
    )
    assert result.status == ToolStatus.ok
    assert result.data.cost_low is not None
    assert any("prior authorization" in c.lower() for c in result.data.caveats)


@pytest.mark.asyncio
async def test_bug1_days_supply_code_mapping_not_conflated_with_raw_count():
    """60-day pricing must resolve via days_supply_code=4 (beneficiary_cost), not be
    confused with the raw day count. Tier-1 60-day copay is $10.00 vs 30-day's $5.00."""
    result_30 = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP,
        drug_name="metformin",
        dosage="500mg",
        days_supply=30,
        ytd_oop_spend=0,
    )
    result_60 = await estimate_drug_cost(
        plan_key=PLAN_FL_PDP,
        drug_name="metformin",
        dosage="500mg",
        days_supply=60,
        ytd_oop_spend=0,
    )
    assert result_30.data.cost_low == pytest.approx(5.00)
    assert result_60.data.cost_low == pytest.approx(10.00)


@pytest.mark.asyncio
async def test_plan_not_found():
    result = await estimate_drug_cost(plan_key="ZZZZ-999", drug_name="metformin", dosage="500mg")
    assert result.status == ToolStatus.not_found


@pytest.mark.asyncio
async def test_drug_not_on_formulary():
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_MAPD, drug_name="omeprazole", dosage="20mg"
    )
    assert result.status == ToolStatus.not_covered


@pytest.mark.asyncio
async def test_ma_pd_zero_deductible_plan_always_initial_coverage():
    result = await estimate_drug_cost(
        plan_key=PLAN_FL_MAPD, drug_name="metformin", dosage="500mg", ytd_oop_spend=0
    )
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
        plan_key=PLAN_FL_PDP,
        drug_name="metformin",
        dosage="500mg",
        days_supply=90,
        ytd_oop_spend=700,
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
        plan_key=PLAN_FL_PDP,
        drug_name="metformin",
        dosage="500mg",
        days_supply=45,
        ytd_oop_spend=700,
    )
    assert result.status == ToolStatus.ok
    assert result.data.cost_low is None
    assert result.data.cost_high is None
    assert any("45-day supply" in c for c in result.data.caveats)
    assert not any("reflects ingredient cost only" in c for c in result.data.caveats)
