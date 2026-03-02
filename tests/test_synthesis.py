from medicare_navigator.agents.synthesis import (
    _deterministic_explanation,
    _follow_up_alternatives_answer,
)
from medicare_navigator.models.query import ParsedQuery
from medicare_navigator.models.response import AlternativesResult, FormularyResult
from medicare_navigator.models.tool_result import ToolResult, ToolStatus


def _parsed(**kwargs) -> ParsedQuery:
    defaults = {
        "drug_name": "omeprazole",
        "plan_key": "B6789-009",
        "raw_message": "omeprazole plan B6789-009",
    }
    defaults.update(kwargs)
    return ParsedQuery(**defaults)


def test_not_covered_skips_benefit_phase():
    form = FormularyResult(
        plan_key="B6789-009",
        plan_name="Blue MedicareRx Standard",
        benefit_phase=None,
        ytd_oop_spend=0.0,
        oop_threshold=2100.0,
        deductible=400.0,
        covered=False,
        ytd_oop_spend_assumed=True,
    )
    tools = {
        "formulary_benefit_lookup": ToolResult(
            status=ToolStatus.not_covered,
            data=form,
            source_id="cms_spuf_2026_q1_demo",
            as_of_date="2026-01-15",
        )
    }
    explanation, _ = _deterministic_explanation(_parsed(), tools, None)
    lower = explanation.lower()
    assert "not covered" in lower or "does not appear" in lower
    assert "deductible phase" not in lower
    assert "ytd" not in lower


def test_covered_assumed_ytd_disclosed():
    form = FormularyResult(
        plan_key="H1234-045",
        plan_name="Humana Gold Plus HMO",
        tier=1,
        cost_share={"tier": 1, "copay": 0.0, "cost_type": "copay"},
        benefit_phase="deductible",
        ytd_oop_spend=0.0,
        oop_threshold=2100.0,
        deductible=0.0,
        covered=True,
        ytd_oop_spend_assumed=True,
    )
    tools = {
        "formulary_benefit_lookup": ToolResult(
            status=ToolStatus.ok,
            data=form,
            source_id="cms_spuf_2026_q1_demo",
            as_of_date="2026-01-15",
        )
    }
    explanation, _ = _deterministic_explanation(
        _parsed(drug_name="metformin", plan_key="H1234-045", ytd_oop_spend_provided=False),
        tools,
        None,
    )
    assert "assuming $0.00" in explanation
    assert "did not provide" in explanation


def test_alternatives_follow_up_includes_as_of_date():
    tools = {
        "alternatives_finder": ToolResult(
            status=ToolStatus.ok,
            data=[
                AlternativesResult(
                    drug_name="atorvastatin",
                    rxcui="83367",
                    te_code="A",
                    equivalent=True,
                )
            ],
            source_id="fda_orange_book_demo",
            as_of_date="2026-01-15",
        )
    }
    result = _follow_up_alternatives_answer(
        _parsed(drug_name="lipitor", raw_message="did you find only one?"),
        tools,
    )
    assert result is not None
    explanation, citations = result
    assert "As of January 15, 2026" in explanation
    assert "atorvastatin" in explanation
    assert citations[0].as_of_date == "2026-01-15"
