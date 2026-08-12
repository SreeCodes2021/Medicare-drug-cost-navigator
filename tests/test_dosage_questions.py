import pytest

from medicare_navigator.agent.dosage_questions import (
    drugs_missing_dosage,
    resolve_dosage_question,
    should_clarify_dosage_before_estimate,
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


def test_should_clarify_multi_drug_even_when_plan_known():
    message = "Compare metformin and januvia costs on S5921-400"
    assert should_clarify_dosage_before_estimate(message, plan_known=True) is True


def test_should_not_clarify_single_drug_when_plan_known():
    message = "lovastatin cost on S5921-400"
    assert should_clarify_dosage_before_estimate(message, plan_known=True) is False


@pytest.mark.asyncio
async def test_mixed_insulin_and_regular_clarifies_dosage():
    message = "Compare metformin and lantus costs on S9999-001"
    assert should_clarify_dosage_before_estimate(message, plan_known=True) is True
    resolved = await resolve_dosage_question(message)
    assert resolved is not None
    explanation, _, tools = resolved
    assert tools == []
    lower = explanation.lower()
    assert "metformin" in lower
    assert "lantus" in lower
    assert "not covered" not in lower


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


def test_repair_false_not_covered_when_tool_shows_covered():
    from medicare_navigator.guardrails.citations import repair_false_not_covered_when_covered

    artifacts = {
        "estimate_drug_cost_all_channels__calls": [
            {
                "status": "ok",
                "data": {
                    "plan_key": "H1045-057",
                    "plan_name": "Test Plan",
                    "drug_name": "januvia",
                    "dosage": "100mg",
                    "covered": True,
                    "days_supply": 30,
                    "channels": {
                        "preferred_retail": {"cost_low": 117.24, "cost_high": 117.24},
                        "standard_retail": {"cost_low": 117.24, "cost_high": 117.24},
                        "preferred_mail": {"cost_low": None, "cost_high": None},
                        "standard_mail": {"cost_low": None, "cost_high": None},
                    },
                },
            }
        ],
        "estimate_drug_cost_all_channels": {
            "status": "ok",
            "data": {
                "plan_key": "H1045-057",
                "plan_name": "Test Plan",
                "drug_name": "januvia",
                "dosage": "100mg",
                "covered": True,
                "days_supply": 30,
                "channels": {
                    "preferred_retail": {"cost_low": 117.24, "cost_high": 117.24},
                    "standard_retail": {"cost_low": 117.24, "cost_high": 117.24},
                    "preferred_mail": {"cost_low": None, "cost_high": None},
                    "standard_mail": {"cost_low": None, "cost_high": None},
                },
            },
        },
    }
    # Re-fetched guardrail channel list disagrees (covered=false) — repair must use tool artifact.
    stale_channel_estimates = [
        {
            "plan_key": "H1045-057",
            "drug_name": "januvia",
            "covered": False,
            "days_supply": 30,
            "channels": {
                "preferred_retail": {"cost_low": None, "cost_high": None},
                "standard_retail": {"cost_low": None, "cost_high": None},
                "preferred_mail": {"cost_low": None, "cost_high": None},
                "standard_mail": {"cost_low": None, "cost_high": None},
            },
        }
    ]
    repaired = repair_false_not_covered_when_covered(
        "Januvia is not covered on plan H1045-057, so no 30-day out-of-pocket estimate is available.",
        artifacts,
        stale_channel_estimates,
    )
    assert "not covered" not in repaired.lower()
    assert "$117.24" in repaired


def test_apply_guardrails_repairs_false_not_covered_with_stale_channel_estimates():
    from medicare_navigator.guardrails.citations import apply_guardrails

    artifacts = {
        "estimate_drug_cost_all_channels": {
            "status": "ok",
            "source_id": "cms_spuf_2026_q3",
            "as_of_date": "2026-01-15",
            "data": {
                "plan_key": "H1045-057",
                "plan_name": "Test Plan",
                "drug_name": "januvia",
                "dosage": "100mg",
                "covered": True,
                "days_supply": 30,
                "channels": {
                    "preferred_retail": {"cost_low": 117.24, "cost_high": 117.24},
                    "standard_retail": {"cost_low": 117.24, "cost_high": 117.24},
                    "preferred_mail": {"cost_low": None, "cost_high": None},
                    "standard_mail": {"cost_low": None, "cost_high": None},
                },
            },
        }
    }
    stale_channel_estimates = [
        {
            "plan_key": "H1045-057",
            "drug_name": "januvia",
            "covered": False,
            "days_supply": 30,
            "channels": {
                "preferred_retail": {"cost_low": None, "cost_high": None},
                "standard_retail": {"cost_low": None, "cost_high": None},
                "preferred_mail": {"cost_low": None, "cost_high": None},
                "standard_mail": {"cost_low": None, "cost_high": None},
            },
        }
    ]
    out, _, _ = apply_guardrails(
        "Januvia is not covered on plan H1045-057, so no 30-day out-of-pocket estimate is available.",
        artifacts,
        channel_estimates=stale_channel_estimates,
    )
    assert "not covered" not in out.lower()
    assert "$117.24" in out
