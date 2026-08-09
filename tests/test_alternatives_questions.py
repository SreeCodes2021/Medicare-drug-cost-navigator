import pytest

from medicare_navigator.agent.alternatives_questions import resolve_alternatives_question


def test_open_ended_alternatives_returns_deferral_without_drug_names():
    resolved = resolve_alternatives_question(
        "What cheaper generic alternatives exist to Januvia and how much would they save?"
    )
    assert resolved is not None
    explanation, artifacts, tools = resolved
    assert tools == []
    assert "doctor" in explanation.lower() or "pharmacist" in explanation.lower()
    assert "sitagliptin" not in explanation.lower()
    assert "metformin" not in explanation.lower()


def test_named_drug_cost_estimate_not_intercepted():
    resolved = resolve_alternatives_question(
        "Compare januvia 100mg vs sitagliptin 100mg cost on H1045-057"
    )
    assert resolved is None


def test_non_alternatives_question_not_intercepted():
    resolved = resolve_alternatives_question("metformin 500mg cost on H1045-057")
    assert resolved is None
