import pytest

from medicare_navigator.agent.enrollment_questions import resolve_enrollment_question
from medicare_navigator.agent.invalid_input_questions import resolve_invalid_input_question


def test_enrollment_request_returns_refusal():
    resolved = resolve_enrollment_question("sign me up for plan H2802-060 please")
    assert resolved is not None
    explanation, artifacts, tools = resolved
    assert tools == []
    assert artifacts == {}
    assert "enroll" in explanation.lower()


def test_invalid_negative_days_supply_returns_clarification():
    resolved = resolve_invalid_input_question(
        "metformin 500mg on S5921-400 with -30 day supply"
    )
    assert resolved is not None
    explanation, _, tools = resolved
    assert tools == []
    assert "-30" in explanation
