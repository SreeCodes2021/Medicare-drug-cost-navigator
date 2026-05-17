import pytest

from medicare_navigator.agent.fallback import run_fallback_navigator
from medicare_navigator.agent.prompts import NAVIGATOR_SYSTEM_PROMPT
from medicare_navigator.agent.navigator import navigator
from medicare_navigator.config import settings
from tests.spuf_fixture import PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.fixture(autouse=True)
def mcp_agent_mode(monkeypatch):
    monkeypatch.setattr(settings, "navigator_mode", "mcp_agent")


@pytest.mark.asyncio
async def test_navigator_lisinopril_budgeting_fallback():
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
async def test_fallback_navigator_includes_policy_on_why_question():
    explanation, tool_artifacts, tools_invoked = await run_fallback_navigator(
        f"Why did lisinopril cost go up on plan {PLAN_FL_PDP}?"
    )
    assert "policy_retrieval" in tools_invoked
    assert explanation


@pytest.mark.asyncio
async def test_fallback_navigator_skips_policy_on_tier_only():
    _, _, tools_invoked = await run_fallback_navigator(
        f"lisinopril copay on plan {PLAN_FL_PDP}"
    )
    assert "policy_retrieval" not in tools_invoked


@pytest.mark.asyncio
async def test_fallback_policy_passages_in_explanation():
    explanation, _, _ = await run_fallback_navigator(
        f"Why did lisinopril cost go up on plan {PLAN_FL_PDP}?"
    )
    lower = explanation.lower()
    assert "deductible" in lower or "benefit" in lower or "part d" in lower


@pytest.mark.asyncio
async def test_fallback_policy_citations_built():
    explanation, tool_artifacts, tools_invoked = await run_fallback_navigator(
        f"Why did lisinopril cost go up on plan {PLAN_FL_PDP}?"
    )
    from medicare_navigator.guardrails.citations import build_citations_from_artifacts

    citations = build_citations_from_artifacts(tool_artifacts)
    assert any(c.source_id == "cms_policy_corpus" for c in citations)
    assert explanation
