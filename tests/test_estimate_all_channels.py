"""Multi-channel deterministic cost estimates."""

import pytest

from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels
from medicare_navigator.tools.pharmacy_channels import PHARMACY_CHANNELS
from medicare_navigator.ui_test.checks import InProcessGetter
from tests.spuf_fixture import PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.fixture
def offline_getter(spuf_db):
    getter = InProcessGetter()
    yield getter
    getter.close()


@pytest.mark.asyncio
async def test_all_channels_returns_four_pharmacy_rows():
    result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP,
        drug_name="metformin",
        dosage="500mg",
        days_supply=30,
        ytd_oop_spend=0,
    )
    assert result.status == ToolStatus.ok
    data = result.data
    assert set(data.channels.keys()) == set(PHARMACY_CHANNELS)
    assert data.deductible is not None
    assert data.tier == 1
    assert data.ded_applies_yn == "N"
    assert data.benefit_phase == "pre_deductible"
    assert data.effective_phase == "initial_coverage"
    assert data.channels["preferred_retail"].cost_low == pytest.approx(5.00)
    pr = data.channels["preferred_retail"]
    assert pr.plan_copay == pytest.approx(5.00)
    assert pr.plan_coinsurance_pct is None
    assert pr.applied_copay == pytest.approx(5.00)
    assert pr.applied_coinsurance_pct is None
    assert data.annual_oop_cap == pytest.approx(2100.0)
    assert data.remaining_oop_headroom == pytest.approx(2100.0)
    assert data.annual_budget_cost_low is not None
    assert data.annual_budget_cost_high is not None
    assert data.remaining_year_days is not None
    assert data.remaining_year_fills is not None
    assert data.remaining_year_budget_cost_low is not None
    assert data.remaining_year_budget_cost_high is not None
    assert data.remaining_year_budget_cost_low < data.annual_budget_cost_low


@pytest.mark.asyncio
async def test_all_channels_metformin_60_day_copay_differs_by_days_supply_code():
    result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP,
        drug_name="metformin",
        dosage="500mg",
        days_supply=60,
        ytd_oop_spend=0,
    )
    assert result.status == ToolStatus.ok
    assert result.data.channels["preferred_retail"].cost_low == pytest.approx(10.00)


@pytest.mark.asyncio
async def test_all_channels_not_covered_includes_na_channels():
    from tests.spuf_fixture import PLAN_FL_MAPD

    result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_MAPD,
        drug_name="omeprazole",
        dosage="20mg",
        days_supply=30,
        ytd_oop_spend=0,
    )
    assert result.status == ToolStatus.not_covered
    assert result.data.covered is False
    for channel in PHARMACY_CHANNELS:
        assert result.data.channels[channel].cost_low is None


@pytest.mark.asyncio
async def test_estimate_api_endpoint(offline_getter):
    status, body = offline_getter.post_json(
        "/api/estimate",
        {
            "plan_id": PLAN_FL_PDP,
            "drug": "metformin",
            "dosage": "500mg",
            "days_supply": 30,
            "ytd_oop_spend": 0,
        },
    )
    assert status == 200
    assert body["status"] == "ok"
    assert body["data"]["channels"]["preferred_retail"]["cost_low"] == 5.0
    assert body["as_of_date"]


@pytest.mark.asyncio
async def test_all_channels_pre_deductible_applied_share_na():
    """Tier 3 omeprazole: deductible applies — applied copay/coinsurance NA, est. cost is pricing."""
    result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP,
        drug_name="omeprazole",
        dosage="20mg",
        days_supply=30,
        ytd_oop_spend=0,
    )
    assert result.status == ToolStatus.ok
    assert result.data.benefit_phase == "pre_deductible"
    assert result.data.ded_applies_yn == "Y"
    pr = result.data.channels["preferred_retail"]
    assert pr.applied_copay is None
    assert pr.applied_coinsurance_pct is None
    assert pr.cost_low == pytest.approx(10.50)
    assert pr.plan_copay is not None or pr.plan_coinsurance_pct is not None
