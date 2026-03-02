from medicare_navigator.qa.chat_client import build_grading_bundle


def test_build_grading_bundle_ok_response():
    raw = {
        "session_id": "sess-1",
        "turn_count": 1,
        "response": {
            "status": "ok",
            "explanation": "Tier 1 — $0 copay for metformin.",
            "citations": [
                {
                    "claim": "Tier 1 copay",
                    "source_id": "formulary",
                    "as_of_date": "2024-01-01",
                }
            ],
            "formulary": {"tier": 1, "plan_key": "H1234-045"},
            "cost_trend": [{"year": 2022, "total_spend": 100.0}],
            "alternatives": [],
            "data_as_of": {"formulary": "2024-01-01"},
            "tool_statuses": {"formulary_benefit_lookup": "ok"},
            "tools_invoked": ["formulary_benefit_lookup"],
            "agents_invoked": ["synthesis"],
            "response_source": "Deterministic",
        },
    }

    bundle = build_grading_bundle("metformin copay H1234-045", raw)

    assert bundle["user_message"] == "metformin copay H1234-045"
    assert bundle["session_id"] == "sess-1"
    assert bundle["grading"]["explanation"] == "Tier 1 — $0 copay for metformin."
    assert bundle["grading"]["citations"][0]["source_id"] == "formulary"
    assert bundle["grading"]["formulary"]["tier"] == 1


def test_build_grading_bundle_clarification():
    raw = {
        "session_id": "sess-2",
        "turn_count": 1,
        "response": {
            "status": "needs_clarification",
            "explanation": "",
            "clarification_message": "Which plan are you asking about?",
        },
    }

    bundle = build_grading_bundle("metformin", raw)

    assert bundle["grading"]["explanation"] == "Which plan are you asking about?"
    assert bundle["grading"]["status"] == "needs_clarification"
