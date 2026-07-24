import pytest

from medicare_navigator.agent.navigator import navigator
from medicare_navigator.agent.prompts import NAVIGATOR_SYSTEM_PROMPT
from medicare_navigator.config import settings
from medicare_navigator.llm.client import llm_client
from medicare_navigator.llm.errors import LLMNotConfiguredError
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_MAPD_MOOP, PLAN_FL_PDP, PLAN_FL_SUPPRESSED


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.mark.asyncio
async def test_navigator_metformin_uses_all_channels_tool():
    message = f"What's the cost for metformin 500mg on plan {PLAN_FL_PDP}?"
    response = await navigator.run(message)
    assert response.status == "ok"
    assert "estimate_drug_cost_all_channels" in response.tools_invoked
    assert response.estimate is not None
    assert response.estimate.cost_low is not None
    assert response.estimate.cost_high is not None
    assert response.estimate.cost_low <= response.estimate.cost_high


@pytest.mark.asyncio
async def test_navigator_metformin_cost_estimate():
    message = (
        f"What's the cost for metformin 500mg on plan {PLAN_FL_MAPD}? "
        "I have already spent $1000 this year."
    )
    response = await navigator.run(message)
    assert response.status == "ok"
    assert response.drug_name == "metformin"
    assert response.estimate is not None
    assert response.estimate.benefit_phase == "initial_coverage"
    lower = response.explanation.lower()
    assert "metformin" in lower


@pytest.mark.asyncio
async def test_navigator_needs_plan_clarification():
    response = await navigator.run("metformin cost")
    assert response.status in ("ok", "needs_clarification")
    assert "plan" in response.explanation.lower()


@pytest.mark.asyncio
async def test_router_uses_navigator():
    from medicare_navigator.orchestrator.router import orchestrator

    response = await orchestrator.run(f"lisinopril 10mg cost plan {PLAN_FL_PDP}")
    assert response.status == "ok"


@pytest.mark.asyncio
async def test_navigator_suppressed_plan_hard_stop():
    response = await navigator.run(f"metformin cost on plan {PLAN_FL_SUPPRESSED}")
    assert "suppressed" in response.explanation.lower() or "contact the plan" in response.explanation.lower()
    assert "$" not in response.explanation.split(response.disclaimer)[0]


@pytest.mark.asyncio
async def test_navigator_insulin_out_of_scope():
    response = await navigator.run(f"lantus cost on plan {PLAN_FL_PDP}")
    assert "insulin" in response.explanation.lower()


def test_navigator_prompt_describes_scope():
    assert "insulin" in NAVIGATOR_SYSTEM_PROMPT.lower()
    assert "estimate_drug_cost_all_channels" in NAVIGATOR_SYSTEM_PROMPT
    assert "moop" in NAVIGATOR_SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_navigator_moop_question_scope_refusal():
    message = (
        f"I want to know the comparison of Max OOP for in and out of network for "
        f"{PLAN_FL_MAPD_MOOP}."
    )
    response = await navigator.run(message)
    lower = response.explanation.lower()

    assert "lookup_plan" in response.tools_invoked
    assert "estimate_drug_cost" not in response.tools_invoked
    assert "estimate_drug_cost_all_channels" not in response.tools_invoked
    assert "which drug" not in lower
    assert "pharmacy channel" not in lower
    assert "moop" in lower or "maximum out-of-pocket" in lower
    assert response.status == "ok"


def test_llm_requires_configuration(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(LLMNotConfiguredError):
        llm_client.require_available()
