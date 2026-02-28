import pytest

from medicare_navigator.ingestion.seed import run_seed
from medicare_navigator.intake.merger import InputMerger
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.formulary_benefit import formulary_benefit_lookup
from medicare_navigator.tools.normalize_drug import compute_benefit_phase


@pytest.fixture(scope="module", autouse=True)
def seed_data():
    run_seed()


def test_benefit_phase_deductible():
    assert compute_benefit_phase(100, 615, 2100) == "deductible"


def test_benefit_phase_initial():
    assert compute_benefit_phase(800, 615, 2100) == "initial_coverage"


def test_benefit_phase_catastrophic():
    assert compute_benefit_phase(2500, 615, 2100) == "catastrophic"


def test_input_merger_chat_overrides_filter():
    chat = QuerySlots(drug="metformin", dosage="500mg", plan_id="H1234-045")
    filters = QuerySlots(plan_id="H1234-001")
    merged = InputMerger.merge(chat, filters)
    assert merged.drug == "metformin"
    assert merged.plan_id == "H1234-045"


def test_formulary_lookup_ok():
    result = formulary_benefit_lookup("H1234-045", "00093-7214-01", ytd_oop_spend=0)
    assert result.status == ToolStatus.ok
    assert result.data.tier == 1
    assert result.data.cost_share.copay == 0.0


def test_formulary_not_covered():
    result = formulary_benefit_lookup("S5678-018", "00006-0112-54")
    assert result.status == ToolStatus.not_covered
    assert result.data is not None
    assert result.data.covered is False
    assert result.data.plan_key == "S5678-018"


def test_formulary_plan_not_found():
    result = formulary_benefit_lookup("ZZZZ-999", "00093-7214-01")
    assert result.status == ToolStatus.not_found
