import pytest

from medicare_navigator.agent.oop_questions import resolve_oop_question
from medicare_navigator.tools.part_d_benefit_lookup import get_part_d_benefit_params


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


def test_get_part_d_benefit_params_returns_2026_cap():
    result = get_part_d_benefit_params(2026)
    assert result.status.value == "ok"
    assert result.data["annual_oop_cap"] == 2100.0


def test_generic_any_plan_oop_uses_benefit_params_not_lookup():
    resolved = resolve_oop_question("for any plan, what is my max oop according to the cms?")
    assert resolved is not None
    explanation, artifacts, tools = resolved
    assert tools == ["get_part_d_benefit_params"]
    assert "lookup_plan" not in artifacts
    assert "$2,100.00" in explanation or "$2100.00" in explanation
    assert "medical" in explanation.lower() or "moop" in explanation.lower()


def test_part_d_annual_cap_question():
    resolved = resolve_oop_question(
        "what is the CMS Part D annual out-of-pocket maximum for 2026?"
    )
    assert resolved is not None
    explanation, _, tools = resolved
    assert tools == ["get_part_d_benefit_params"]
    assert "$2,100.00" in explanation


def test_medical_moop_with_plan_uses_lookup():
    resolved = resolve_oop_question(
        "compare max OOP in and out of network for H1889-014"
    )
    assert resolved is not None
    explanation, artifacts, tools = resolved
    assert tools == ["lookup_plan"]
    assert "lookup_plan" in artifacts
    assert "spuf" in explanation.lower() or "formulary" in explanation.lower()


def test_any_plan_wording_ignores_filter_plan():
    resolved = resolve_oop_question(
        "for any plan, what is my max oop according to the cms?",
        filter_plan_id="H1889-014",
    )
    assert resolved is not None
    explanation, artifacts, tools = resolved
    assert tools == ["get_part_d_benefit_params"]
    assert "H1889-014" not in explanation
