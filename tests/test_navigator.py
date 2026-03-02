import pytest

from medicare_navigator.agent.navigator import navigator
from medicare_navigator.config import settings
from medicare_navigator.ingestion.seed import run_seed


@pytest.fixture(scope="module", autouse=True)
def seed_data():
    run_seed()


@pytest.fixture(autouse=True)
def mcp_agent_mode(monkeypatch):
    monkeypatch.setattr(settings, "navigator_mode", "mcp_agent")


@pytest.mark.asyncio
async def test_navigator_lisinopril_budgeting_fallback():
    message = (
        "Why did lisinopril costs go up on plan S5678-012? "
        "I want to buy 10 pieces. I have already spent $1000 this year. "
        "I want help in budgeting."
    )
    response = await navigator.run(message)
    assert response.status == "ok"
    assert response.drug_name == "lisinopril"
    assert response.formulary is not None
    assert response.formulary.benefit_phase == "initial_coverage"
    assert response.formulary.supply_estimate is not None
    lower = response.explanation.lower()
    assert "lisinopril" in lower
    assert "tier" in lower or "copay" in lower
    assert "navigator" in response.agents_invoked


@pytest.mark.asyncio
async def test_navigator_needs_plan_clarification():
    response = await navigator.run("metformin tier and copay")
    assert response.status in ("ok", "needs_clarification")
    assert "plan" in response.explanation.lower()


@pytest.mark.asyncio
async def test_router_uses_navigator_by_default():
    from medicare_navigator.orchestrator.router import orchestrator

    response = await orchestrator.run("lisinopril 10mg copay plan S5678-012")
    assert response.status == "ok"
    assert response.agents_invoked == ["navigator"]
