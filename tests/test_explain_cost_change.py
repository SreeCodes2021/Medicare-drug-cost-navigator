import pytest

from medicare_navigator.agents.synthesis import _explain_cost_change_answer
from medicare_navigator.models.query import ParsedQuery
from medicare_navigator.models.response import CostTrendPoint, FormularyResult
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.orchestrator.pipeline import orchestrator
from medicare_navigator.tools.ira_drugs import is_ira_negotiated
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


def _lisinopril_formulary(**kwargs) -> FormularyResult:
    defaults = {
        "plan_key": PLAN_FL_PDP,
        "plan_name": "Florida Test PDP",
        "tier": 1,
        "cost_share": {"tier": 1, "copay": 5.0, "cost_type": "copay"},
        "benefit_phase": "deductible",
        "ytd_oop_spend": 0.0,
        "oop_threshold": 2100.0,
        "deductible": 615.0,
        "covered": True,
        "ytd_oop_spend_assumed": True,
    }
    defaults.update(kwargs)
    return FormularyResult(**defaults)


def _tool_artifacts(formulary: FormularyResult | None = None, with_trend: bool = True):
    form = formulary or _lisinopril_formulary()
    tools = {
        "formulary_benefit_lookup": ToolResult(
            status=ToolStatus.ok,
            data=form,
            source_id="cms_spuf_2026_q1",
            as_of_date="2026-01-15",
        )
    }
    if with_trend:
        tools["cost_trend_lookup"] = ToolResult(
            status=ToolStatus.ok,
            data=[
                CostTrendPoint(year=2022, total_spend=800_000_000, avg_unit_cost=0.08),
                CostTrendPoint(year=2025, total_spend=1_050_000_000, avg_unit_cost=0.12),
            ],
            source_id="cms_part_d_spending",
            as_of_date="2026-01-15",
        )
    return tools


def test_explain_cost_change_leads_with_ytd_assumption():
    explanation, _ = _explain_cost_change_answer(_parsed(), _tool_artifacts())
    lower = explanation.lower()
    assert lower.index("assuming $0") < lower.index("deductible")
    assert "most likely you're paying full price" in lower
    assert "tier 1" in lower
    assert "no prior-year tier data" in lower


def test_explain_cost_change_omits_ira_and_tier_change_speculation():
    explanation, _ = _explain_cost_change_answer(_parsed(), _tool_artifacts())
    lower = explanation.lower()
    assert "ira" not in lower
    assert "negotiat" not in lower
    assert "moved to a higher tier" not in lower
    assert "---" not in explanation
    assert "###" not in explanation


def test_explain_cost_change_includes_unit_cost_trend():
    explanation, citations = _explain_cost_change_answer(_parsed(), _tool_artifacts())
    assert "$0.08" in explanation
    assert "$0.12" in explanation
    assert any("avg unit cost" in c.claim.lower() for c in citations)


def test_explain_cost_change_invites_ytd_when_assumed():
    explanation, _ = _explain_cost_change_answer(_parsed(), _tool_artifacts())
    assert "share your year-to-date" in explanation.lower()


def test_explain_cost_change_appends_policy_claims():
    claims = [
        {
            "claim": "During the deductible phase you pay the full drug cost until it is met.",
            "source_id": "part_d_deductible_phase",
        }
    ]
    explanation, citations = _explain_cost_change_answer(
        _parsed(), _tool_artifacts(), policy_claims=claims
    )
    assert "deductible phase" in explanation.lower()
    assert any(c.source_id == "cms_policy_corpus" for c in citations)


def test_explain_cost_change_policy_citations_added():
    claims = [
        {
            "claim": "Initial coverage uses tier copays.",
            "source_id": "part_d_initial_coverage",
        }
    ]
    _, citations = _explain_cost_change_answer(
        _parsed(), _tool_artifacts(), policy_claims=claims
    )
    assert any(c.source_id == "cms_policy_corpus" for c in citations)


def test_explain_cost_change_caps_policy_claims_at_two():
    claims = [
        {"claim": f"Policy claim {i}.", "source_id": "part_d_deductible_phase"}
        for i in range(4)
    ]
    explanation, _ = _explain_cost_change_answer(
        _parsed(), _tool_artifacts(), policy_claims=claims
    )
    assert explanation.count("Policy claim") <= 2


def test_explain_cost_change_policy_claims_filtered():
    claims = [
        {"claim": "IRA negotiation may change costs.", "source_id": "ira_negotiated_prices"},
        {"claim": "Benefit phases affect what you pay.", "source_id": "part_d_deductible_phase"},
    ]
    explanation, _ = _explain_cost_change_answer(
        _parsed(), _tool_artifacts(), policy_claims=claims
    )
    assert "ira" not in explanation.lower()
    assert "benefit phases" in explanation.lower()


def test_explain_cost_change_without_policy_claims_unchanged():
    with_claims, _ = _explain_cost_change_answer(
        _parsed(), _tool_artifacts(), policy_claims=None
    )
    without, _ = _explain_cost_change_answer(_parsed(), _tool_artifacts())
    assert with_claims == without


def test_explain_cost_change_with_provided_ytd():
    form = _lisinopril_formulary(ytd_oop_spend=400.0, ytd_oop_spend_assumed=False)
    explanation, _ = _explain_cost_change_answer(
        _parsed(ytd_oop_spend=400.0, ytd_oop_spend_provided=True),
        _tool_artifacts(formulary=form),
    )
    assert "$400.00" in explanation
    assert "assuming $0" not in explanation.lower()
    assert "share your year-to-date" not in explanation.lower()


def test_lisinopril_not_ira_negotiated():
    assert not is_ira_negotiated("lisinopril")
    assert is_ira_negotiated("eliquis")


@pytest.mark.asyncio
async def test_pipeline_lisinopril_cost_change_end_to_end(spuf_db):
    response = await orchestrator.run(f"why did lisinopril cost go up on plan {PLAN_FL_PDP}")
    assert response.status == "ok"
    assert response.formulary is not None
    lower = response.explanation.lower()
    assert "assuming $0" in lower or "lisinopril" in lower
    assert "ira" not in lower
    assert "policy" in response.agents_invoked
    assert "policy_retrieval" in response.tools_invoked
