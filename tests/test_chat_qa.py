from medicare_navigator.guardrails.channel_parity import (
    prose_channel_overclaim_warnings,
    summarize_channel_coverage,
)
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
            "estimate": {"tiers_matched": [1], "plan_key": "H1234-045"},
            "data_as_of": {"estimate": "2024-01-01"},
            "tool_statuses": {"estimate_drug_cost": "ok"},
            "tools_invoked": ["estimate_drug_cost"],
            "response_source": "Deterministic",
        },
    }

    bundle = build_grading_bundle("metformin copay H1234-045", raw)

    assert bundle["user_message"] == "metformin copay H1234-045"
    assert bundle["session_id"] == "sess-1"
    assert bundle["grading"]["explanation"] == "Tier 1 — $0 copay for metformin."
    assert bundle["grading"]["citations"][0]["source_id"] == "formulary"
    assert bundle["grading"]["estimate"]["tiers_matched"] == [1]


def test_summarize_channel_coverage_flags_missing_channels():
    estimates = [
        {
            "plan_key": "H2802-063",
            "plan_name": "UHC Giveback",
            "tier": 1,
            "channels": {
                "preferred_retail": {"cost_low": None, "cost_high": None},
                "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                "preferred_mail": {"cost_low": None, "cost_high": None},
                "standard_mail": {"cost_low": None, "cost_high": None},
            },
        }
    ]
    summary = summarize_channel_coverage(estimates)
    assert summary[0]["priced_channels"] == ["standard_retail"]
    assert len(summary[0]["missing_channels"]) == 3
    assert summary[0]["aggregate_cost_low"] == 0.0


def test_prose_channel_overclaim_warnings_detects_all_channels_claim():
    coverage = summarize_channel_coverage(
        [
            {
                "plan_key": "H2802-063",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                    "preferred_mail": {"cost_low": None},
                    "standard_mail": {"cost_low": None},
                },
            }
        ]
    )
    warnings = prose_channel_overclaim_warnings(
        "Estimated cost is $0.00 across all CMS pharmacy channels.",
        coverage,
    )
    assert warnings
    assert "H2802-063" in warnings[0]


def test_build_grading_bundle_includes_channel_coverage():
    raw = {
        "session_id": "sess-3",
        "turn_count": 1,
        "response": {
            "status": "ok",
            "explanation": "$0 across all CMS pharmacy channels.",
            "channel_estimates": [
                {
                    "plan_key": "H2802-063",
                    "channels": {
                        "preferred_retail": {"cost_low": None},
                        "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                        "preferred_mail": {"cost_low": None},
                        "standard_mail": {"cost_low": None},
                    },
                }
            ],
        },
    }
    bundle = build_grading_bundle("compare plans", raw)
    assert bundle["grading"]["channel_coverage"][0]["missing_channels"]
    assert bundle["grading"]["channel_warnings"]


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
