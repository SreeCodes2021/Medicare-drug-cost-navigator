import pytest

from medicare_navigator.intake.agent import run_intake
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.mark.asyncio
async def test_intake_complete():
    result = await run_intake(f"metformin 500mg copay on {PLAN_FL_MAPD}")
    assert result.status == "complete"
    assert result.parsed_query is not None
    assert result.parsed_query.drug_name == "metformin"
    assert result.parsed_query.plan_key == PLAN_FL_MAPD


@pytest.mark.asyncio
async def test_intake_not_found():
    result = await run_intake(f"xyznonexistentdrug 500mg plan {PLAN_FL_MAPD}")
    assert result.status == "not_found"


@pytest.mark.asyncio
async def test_intake_needs_clarification():
    result = await run_intake("metformin")
    assert result.status == "needs_clarification"
    assert "plan_id" in result.missing_slots


@pytest.mark.asyncio
async def test_intake_eligibility_without_plan_does_not_guess_plan():
    result = await run_intake("Am I eligible for filling omeprazole?")
    assert result.status == "needs_clarification"
    assert "plan_id" in result.missing_slots
    assert result.slots.plan_id is None


@pytest.mark.asyncio
async def test_intake_ignores_quantity_as_dosage():
    message = (
        f"Why did lisinopril costs go up on plan {PLAN_FL_PDP}? "
        "I want buy 10 pieces of it. I have already spend $1000 this year. "
        "I want help in budgeting"
    )
    result = await run_intake(message)
    assert result.status == "complete"
    assert result.parsed_query is not None
    assert result.parsed_query.drug_name == "lisinopril"
    assert result.parsed_query.plan_key == PLAN_FL_PDP
    assert result.parsed_query.ytd_oop_spend == 1000.0
