import pytest

from medicare_navigator.agent.dosage_questions import (
    drugs_missing_dosage,
    resolve_dosage_question,
)
from medicare_navigator.guardrails.citations import repair_false_not_covered_for_missing_dosage


@pytest.mark.asyncio
async def test_benefit_phase_without_strength_returns_clarification():
    resolved = await resolve_dosage_question(
        "Which benefit phase am I in for lovastatin on S5921-400 if I've spent $0 YTD?"
    )
    assert resolved is not None
    explanation, artifacts, tools = resolved
    assert tools == []
    assert artifacts == {}
    assert "lovastatin" in explanation.lower()
    assert "strength" in explanation.lower() or "10 mg" in explanation.lower()


@pytest.mark.asyncio
async def test_multi_drug_compare_without_strengths_returns_clarification():
    resolved = await resolve_dosage_question(
        "Compare januvia and metformin costs on S5921-400 and also tell me if I should change plans"
    )
    assert resolved is not None
    explanation, _, _ = resolved
    assert "januvia" in explanation.lower()
    assert "metformin" in explanation.lower()
    assert "not covered" not in explanation.lower()


def test_drugs_missing_dosage_ignores_named_strength():
    assert drugs_missing_dosage("lovastatin 40mg on S5921-400") == []


def test_repair_false_not_covered_when_covered_variant_exists():
    artifacts = {
        "estimate_drug_cost_all_channels__calls": [
            {
                "status": "not_covered",
                "data": {
                    "plan_key": "S5921-400",
                    "drug_name": "januvia",
                    "dosage": None,
                    "covered": False,
                },
            },
            {
                "status": "ok",
                "data": {
                    "plan_key": "S5921-400",
                    "drug_name": "januvia",
                    "dosage": "100 mg",
                    "covered": True,
                },
            },
        ]
    }
    repaired = repair_false_not_covered_for_missing_dosage(
        "Januvia is not covered on plan S5921-400.", artifacts
    )
    assert repaired.lower().startswith("i need the strength")
    assert "januvia" in repaired.lower()
