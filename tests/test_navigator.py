import pytest

from medicare_navigator.agent.prompts import NAVIGATOR_SYSTEM_PROMPT
from medicare_navigator.agent.navigator import navigator
from medicare_navigator.config import settings
from medicare_navigator.llm.client import llm_client
from medicare_navigator.llm.errors import LLMNotConfiguredError
from tests.spuf_fixture import PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.fixture(autouse=True)
def mcp_agent_mode(monkeypatch):
    monkeypatch.setattr(settings, "navigator_mode", "mcp_agent")


@pytest.mark.asyncio
async def test_navigator_lisinopril_budgeting():
    message = (
        f"Why did lisinopril costs go up on plan {PLAN_FL_PDP}? "
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

    response = await orchestrator.run(f"lisinopril 10mg copay plan {PLAN_FL_PDP}")
    assert response.status == "ok"
    assert response.agents_invoked == ["navigator"]


def test_navigator_prompt_mentions_policy_retrieval():
    assert "policy_retrieval" in NAVIGATOR_SYSTEM_PROMPT
    assert "deductible" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "catastrophic" in NAVIGATOR_SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_navigator_includes_policy_on_why_question():
    response = await navigator.run(f"Why did lisinopril cost go up on plan {PLAN_FL_PDP}?")
    assert "policy_retrieval" in response.tools_invoked
    assert response.explanation


@pytest.mark.asyncio
async def test_navigator_skips_policy_on_tier_only():
    response = await navigator.run(f"lisinopril copay on plan {PLAN_FL_PDP}")
    assert "policy_retrieval" not in response.tools_invoked


@pytest.mark.asyncio
async def test_navigator_policy_passages_in_explanation():
    response = await navigator.run(f"Why did lisinopril cost go up on plan {PLAN_FL_PDP}?")
    lower = response.explanation.lower()
    assert "deductible" in lower or "benefit" in lower or "part d" in lower


@pytest.mark.asyncio
async def test_navigator_policy_citations_built():
    response = await navigator.run(f"Why did lisinopril cost go up on plan {PLAN_FL_PDP}?")
    assert any(c.source_id == "cms_policy_corpus" for c in response.citations)
    assert response.explanation


def test_llm_requires_configuration(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(LLMNotConfiguredError):
        llm_client.require_available()
