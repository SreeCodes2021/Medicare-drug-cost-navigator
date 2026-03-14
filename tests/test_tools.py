import pytest

from medicare_navigator.intake.merger import InputMerger
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.formulary_benefit import formulary_benefit_lookup
from medicare_navigator.tools.normalize_drug import compute_benefit_phase
from tests.spuf_fixture import NDC_JANUVIA, NDC_LISINOPRIL, NDC_METFORMIN, PLAN_FL_MAPD, PLAN_FL_PDP, PLAN_TX_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


def test_benefit_phase_deductible():
    assert compute_benefit_phase(100, 615, 2100) == "deductible"


def test_benefit_phase_initial():
    assert compute_benefit_phase(800, 615, 2100) == "initial_coverage"


def test_benefit_phase_catastrophic():
    assert compute_benefit_phase(2500, 615, 2100) == "catastrophic"


def test_input_merger_chat_overrides_filter():
    chat = QuerySlots(drug="metformin", dosage="500mg", plan_id=PLAN_FL_MAPD)
    filters = QuerySlots(plan_id=PLAN_FL_PDP)
    merged = InputMerger.merge(chat, filters)
    assert merged.drug == "metformin"
    assert merged.plan_id == PLAN_FL_MAPD


def test_formulary_lookup_ok():
    result = formulary_benefit_lookup(PLAN_FL_MAPD, NDC_METFORMIN, ytd_oop_spend=0)
    assert result.status == ToolStatus.ok
    assert result.data.tier == 2
    assert result.data.cost_share.copay == 8.0


def test_formulary_not_covered():
    result = formulary_benefit_lookup(PLAN_TX_PDP, NDC_JANUVIA)
    assert result.status == ToolStatus.not_covered
    assert result.data is not None
    assert result.data.covered is False
    assert result.data.plan_key == PLAN_TX_PDP
    assert result.data.benefit_phase is None
    assert result.data.ytd_oop_spend_assumed is True


def test_formulary_covered_ytd_provided():
    result = formulary_benefit_lookup(
        PLAN_FL_MAPD, NDC_METFORMIN, ytd_oop_spend=400.0, ytd_oop_spend_provided=True
    )
    assert result.status == ToolStatus.ok
    assert result.data.ytd_oop_spend_assumed is False
    assert result.data.benefit_phase == "initial_coverage"


def test_formulary_plan_not_found():
    result = formulary_benefit_lookup("ZZZZ-999", NDC_METFORMIN)
    assert result.status == ToolStatus.not_found
