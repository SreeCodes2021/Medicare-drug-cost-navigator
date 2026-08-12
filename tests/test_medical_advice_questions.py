import pytest

from medicare_navigator.agent.medical_advice_questions import resolve_medical_advice_question
from medicare_navigator.agent.navigator import navigator


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


def test_switch_medication_question_returns_medical_refusal():
    resolved = resolve_medical_advice_question(
        "Should I switch from metformin to lisinopril for my blood pressure?"
    )
    assert resolved is not None
    explanation, artifacts, tools = resolved
    assert tools == []
    assert artifacts == {}
    assert "medical advice" in explanation.lower()
    assert "doctor" in explanation.lower() or "pharmacist" in explanation.lower()
    assert "10mg" not in explanation.lower()


def test_efficacy_comparison_returns_medical_refusal():
    resolved = resolve_medical_advice_question(
        "Is januvia better than metformin for diabetes?"
    )
    assert resolved is not None
    explanation, _, _ = resolved
    assert "medical advice" in explanation.lower()


def test_pregnancy_safety_question_returns_medical_refusal():
    resolved = resolve_medical_advice_question(
        "Is metformin safe during pregnancy?"
    )
    assert resolved is not None
    explanation, _, _ = resolved
    assert "medical advice" in explanation.lower()


def test_cost_compare_without_strength_not_intercepted():
    resolved = resolve_medical_advice_question(
        "Compare metformin and lisinopril costs on H1045-057"
    )
    assert resolved is None


def test_named_cost_estimate_not_intercepted():
    resolved = resolve_medical_advice_question("metformin 500mg cost on H1045-057")
    assert resolved is None


@pytest.mark.asyncio
async def test_navigator_refuses_medical_switch_advice():
    response = await navigator.run(
        "Should I switch from metformin to lisinopril for my blood pressure?"
    )
    assert response.response_source == "System/MedicalAdvice"
    lower = response.explanation.lower()
    assert "medical advice" in lower
    assert "doctor" in lower or "pharmacist" in lower
    assert "specify the strength" not in lower


@pytest.mark.asyncio
async def test_navigator_refuses_efficacy_comparison():
    response = await navigator.run("Is januvia better than metformin for diabetes?")
    assert response.response_source == "System/MedicalAdvice"
    assert "medical advice" in response.explanation.lower()
