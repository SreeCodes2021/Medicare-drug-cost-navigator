import pytest

from medicare_navigator.agents.clarification import _deterministic_clarification, run_clarification_agent
from medicare_navigator.ingestion.seed import run_seed
from medicare_navigator.intake.agent import run_intake
from medicare_navigator.models.query import IntakeResult, QuerySlots
from medicare_navigator.orchestrator.pipeline import orchestrator


@pytest.fixture(scope="module", autouse=True)
def seed_data():
    run_seed()


@pytest.mark.asyncio
async def test_intake_omeprazol_typo_resolves_and_needs_plan():
    result = await run_intake("Am I eligible for omeprazol ?")
    assert result.status == "needs_clarification"
    assert result.missing_slots == ["plan_id"]
    assert result.resolved_drug is not None
    assert result.resolved_drug["drug_name"] == "omeprazole"
    assert result.slots.drug == "omeprazol"


@pytest.mark.asyncio
async def test_intake_eligibility_without_plan_does_not_guess_plan():
    result = await run_intake("Am I eligible for filling omeprazole?")
    assert result.status == "needs_clarification"
    assert "plan_id" in result.missing_slots
    assert result.slots.plan_id is None
    assert result.resolved_drug is not None


@pytest.mark.asyncio
async def test_clarification_for_resolved_drug_asks_for_plan():
    intake = IntakeResult(
        status="needs_clarification",
        slots=QuerySlots(drug="omeprazol", raw_message="Am I eligible for omeprazol ?"),
        missing_slots=["plan_id"],
        resolved_drug={"drug_name": "omeprazole", "dosage": "20mg"},
    )
    message, source = await run_clarification_agent("Am I eligible for omeprazol ?", intake)
    assert "omeprazole" in message.lower()
    assert "plan" in message.lower()
    assert "tier" not in message.lower()
    assert "copay" not in message.lower()
    assert "covered" not in message.lower()
    assert "Deterministic fallback" in source


@pytest.mark.asyncio
async def test_clarification_not_found_suggests_candidates():
    intake = IntakeResult(
        status="not_found",
        slots=QuerySlots(drug="omeprazol", raw_message="omeprazol"),
        missing_slots=["drug"],
        drug_candidates=[{"drug_name": "omeprazole", "dosage": "20mg"}],
    )
    message = _deterministic_clarification(intake)
    assert "omeprazole" in message.lower()
    assert "did you mean" in message.lower()


@pytest.mark.asyncio
async def test_clarification_not_found_without_resolved_drug_does_not_crash():
    intake = IntakeResult(
        status="not_found",
        slots=QuerySlots(drug="xyzdrug", raw_message="xyzdrug"),
        missing_slots=["drug"],
        drug_candidates=[{"drug_name": "omeprazole", "dosage": "20mg"}],
        resolved_drug=None,
    )
    message, source = await run_clarification_agent("xyzdrug", intake)
    assert message
    assert "Deterministic fallback" in source


@pytest.mark.asyncio
async def test_pipeline_omeprazol_typo_end_to_end():
    response = await orchestrator.run("Am I eligible for omeprazol ?")
    assert response.status == "needs_clarification"
    assert "omeprazole" in response.explanation.lower()
    assert "plan" in response.explanation.lower()
    assert "clarification" in response.agents_invoked
