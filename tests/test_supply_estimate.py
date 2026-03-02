import pytest

from medicare_navigator.ingestion.seed import run_seed
from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.formulary_benefit import formulary_benefit_lookup


@pytest.fixture(scope="module", autouse=True)
def seed_data():
    run_seed()


def test_supply_estimate_copay_single_fill():
    result = formulary_benefit_lookup(
        "S5678-012",
        "00378-1805-01",
        ytd_oop_spend=1000.0,
        ytd_oop_spend_provided=True,
        quantity=10,
    )
    assert result.status == ToolStatus.ok
    supply = result.data.supply_estimate
    assert supply is not None
    assert supply.scenarios is not None
    assert len(supply.scenarios) == 2
    assert supply.scenarios[0].estimated_patient_cost == 2.0
    assert supply.scenarios[1].estimated_patient_cost == 20.0


def test_supply_estimate_with_explicit_fills():
    result = formulary_benefit_lookup(
        "S5678-012",
        "00378-1805-01",
        ytd_oop_spend=1000.0,
        ytd_oop_spend_provided=True,
        fills=3,
    )
    assert result.status == ToolStatus.ok
    supply = result.data.supply_estimate
    assert supply is not None
    assert supply.estimated_patient_cost == 6.0
    assert "3 fill" in supply.formula_description


def test_no_supply_estimate_without_quantity():
    result = formulary_benefit_lookup("H1234-045", "00093-7214-01")
    assert result.data.supply_estimate is None
