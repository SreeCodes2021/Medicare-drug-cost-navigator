from medicare_navigator.agent.navigator import _normalize_estimate_tool_args
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.tools.days_supply import (
    coerce_estimate_days_supply,
    extract_explicit_days_supply,
)


def test_extract_explicit_days_supply_recognizes_common_phrases():
    assert extract_explicit_days_supply("lovastatin for a 90-day supply on S5921-400") == 90
    assert extract_explicit_days_supply("cost for 60 day supply") == 60
    assert extract_explicit_days_supply("30 days supply of metformin") == 30


def test_extract_explicit_days_supply_ignores_unspecified_queries():
    message = (
        "How much will metformin 500mg cost on Arkansas Medicare plan S5921-400? "
        "I have not spent anything out of pocket yet this year."
    )
    assert extract_explicit_days_supply(message) is None


def test_coerce_estimate_days_supply_defaults_to_30():
    assert coerce_estimate_days_supply(message="metformin 500mg on S5921-400") == 30


def test_coerce_estimate_days_supply_honors_explicit_and_filter_values():
    assert (
        coerce_estimate_days_supply(
            message="metformin on S5921-400 for a 90-day supply",
        )
        == 90
    )
    assert (
        coerce_estimate_days_supply(
            message="metformin on S5921-400",
            filter_days_supply=60,
        )
        == 60
    )


def test_coerce_estimate_days_supply_carries_forward_last_estimate():
    last_call = {
        "name": "estimate_drug_cost_all_channels",
        "arguments": {
            "plan_key": "S5921-400",
            "drug_name": "metformin",
            "dosage": "500mg",
            "days_supply": 90,
        },
    }
    assert (
        coerce_estimate_days_supply(
            message="what if I've already spent $800 this year?",
            last_tool_call=last_call,
        )
        == 90
    )


def test_normalize_estimate_tool_args_overrides_llm_hallucinated_fill_size():
    message = (
        "How much will metformin 500mg cost on Arkansas Medicare plan S5921-400? "
        "I have not spent anything out of pocket yet this year."
    )
    normalized = _normalize_estimate_tool_args(
        "estimate_drug_cost_all_channels",
        {
            "plan_key": "S5921-400",
            "drug_name": "metformin",
            "dosage": "500mg",
            "days_supply": 90,
            "ytd_oop_spend": 0,
        },
        message=message,
        filter_slots=QuerySlots(contract_year=2026),
        last_tool_calls=None,
    )
    assert normalized["days_supply"] == 30
