import pytest

from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.formulary_benefit import formulary_benefit_lookup
from tests.spuf_fixture import NDC_LISINOPRIL, NDC_METFORMIN, PLAN_FL_MAPD, PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


def test_supply_estimate_copay_single_fill():
    result = formulary_benefit_lookup(
        PLAN_FL_PDP,
        NDC_LISINOPRIL,
        ytd_oop_spend=1000.0,
        ytd_oop_spend_provided=True,
        quantity=10,
    )
    assert result.status == ToolStatus.ok
    supply = result.data.supply_estimate
    assert supply is not None
    assert supply.scenarios is not None
    assert len(supply.scenarios) == 2
    assert supply.scenarios[0].estimated_patient_cost == 5.0
    assert supply.scenarios[1].estimated_patient_cost == 50.0


def test_supply_estimate_with_explicit_fills():
    result = formulary_benefit_lookup(
        PLAN_FL_PDP,
        NDC_LISINOPRIL,
        ytd_oop_spend=1000.0,
        ytd_oop_spend_provided=True,
        fills=3,
    )
    assert result.status == ToolStatus.ok
    supply = result.data.supply_estimate
    assert supply is not None
    assert supply.estimated_patient_cost == 15.0
    assert "3 fill" in supply.formula_description


def test_no_supply_estimate_without_quantity():
    result = formulary_benefit_lookup(PLAN_FL_MAPD, NDC_METFORMIN)
    assert result.data.supply_estimate is None
