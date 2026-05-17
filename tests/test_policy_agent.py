from unittest.mock import patch

import pytest

from medicare_navigator.agents.policy import PolicyLLMOutput, filter_policy_claims, run_policy_agent
from medicare_navigator.models.query import ParsedQuery
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from tests.spuf_fixture import PLAN_FL_PDP


def _parsed(**kwargs) -> ParsedQuery:
    defaults = {
        "drug_name": "lisinopril",
        "plan_key": PLAN_FL_PDP,
        "intents": ["explain_cost_change"],
        "raw_message": f"why did lisinopril cost go up on plan {PLAN_FL_PDP}",
        "ytd_oop_spend_provided": False,
    }
    defaults.update(kwargs)
    return ParsedQuery(**defaults)


@pytest.mark.asyncio
async def test_run_policy_agent_returns_retrieval_artifact(spuf_db):
    parsed = _parsed()
    output, retrieval = await run_policy_agent(parsed, {})
    assert isinstance(output, PolicyLLMOutput)
    assert retrieval.status == ToolStatus.ok
    assert retrieval.data


@pytest.mark.asyncio
async def test_policy_agent_query_includes_drug_and_intents(spuf_db):
    parsed = _parsed()
    captured: dict = {}

    def _capture(query: str, top_k: int = 3):
        captured["query"] = query
        from medicare_navigator.tools.policy_retrieval import policy_retrieval

        return policy_retrieval(query, top_k=top_k)

    with patch(
        "medicare_navigator.agents.policy.policy_retrieval",
        side_effect=_capture,
    ):
        await run_policy_agent(parsed, {})

    assert "lisinopril" in captured["query"]
    assert "explain_cost_change" in captured["query"]


def test_filter_policy_claims_drops_ira_for_generic():
    claims = [
        {"claim": "IRA negotiation may change costs.", "source_id": "ira_negotiated_prices"},
        {"claim": "Benefit phases affect what you pay.", "source_id": "part_d_deductible_phase"},
        {"claim": "A formulary tier change can increase cost sharing.", "source_id": "formulary_tier_explanation"},
    ]
    filtered = filter_policy_claims(claims, "lisinopril", {})
    assert len(filtered) == 1
    assert filtered[0]["source_id"] == "part_d_deductible_phase"


def test_filter_policy_claims_drops_tier_change_claims():
    claims = [
        {"claim": "The drug moved to a higher tier last year.", "source_id": "formulary_tier_explanation"},
        {"claim": "Initial coverage uses tier copays.", "source_id": "part_d_initial_coverage"},
    ]
    filtered = filter_policy_claims(claims, "metformin", {})
    assert len(filtered) == 1
    assert filtered[0]["source_id"] == "part_d_initial_coverage"


def test_filter_policy_claims_keeps_benefit_phase_claims():
    claims = [
        {"claim": "During the deductible phase you pay full price.", "source_id": "part_d_deductible_phase"},
    ]
    filtered = filter_policy_claims(claims, "lisinopril", {})
    assert len(filtered) == 1
