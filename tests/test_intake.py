import pytest

from medicare_navigator.ingestion.seed import run_seed
from medicare_navigator.intake.agent import run_intake


@pytest.fixture(scope="module", autouse=True)
def seed_data():
    run_seed()


@pytest.mark.asyncio
async def test_intake_complete():
    result = await run_intake("metformin 500mg copay on H1234-045")
    assert result.status == "complete"
    assert result.parsed_query is not None
    assert result.parsed_query.drug_name == "metformin"
    assert result.parsed_query.plan_key == "H1234-045"


@pytest.mark.asyncio
async def test_intake_not_found():
    result = await run_intake("xyznonexistentdrug 500mg plan H1234-045")
    assert result.status == "not_found"


@pytest.mark.asyncio
async def test_intake_needs_clarification():
    result = await run_intake("metformin")
    assert result.status == "needs_clarification"
