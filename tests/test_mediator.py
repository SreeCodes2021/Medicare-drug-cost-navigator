"""Coverage for the mediator rewrite/extraction layer (agent/mediator.py).

llm_mock_mode is on by default for all tests (tests/conftest.py:use_mock_llm), so these
exercise the real rewrite_and_extract() -> LLMClient.structured_complete() ->
mock_structured_completion() path end to end, against the exact worked examples documented
in the mediator's prompt design.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from medicare_navigator.agent.mediator import (
    MediatorRewrite,
    _build_user_prompt,
    rewrite_and_extract,
)
from medicare_navigator.llm.client import llm_client
from medicare_navigator.llm.types import TokenUsage


@pytest.mark.asyncio
async def test_casual_typo_message_never_fabricates_a_specific_drug():
    result, usage, model = await rewrite_and_extract(
        "Hey!ssup. need 30 days supply of durg for mom. how much?"
    )
    assert result is not None
    normalized = result.normalized_message.lower()
    assert "drug" in normalized
    assert "durg" not in normalized
    # No real drug name exists anywhere in the source message — the rewrite must not invent
    # one just because a specific product name would make the sentence read more naturally.
    assert "metformin" not in normalized and "lantus" not in normalized
    assert result.duration_count is None
    assert result.explicit_month is None
    assert usage.total_tokens > 0
    assert model


@pytest.mark.asyncio
async def test_already_clean_message_is_near_identity():
    result, _usage, _model = await rewrite_and_extract("Lantus 30day supply H0270-001")
    assert result is not None
    normalized = result.normalized_message
    assert "Lantus" in normalized
    assert "H0270-001" in normalized
    assert result.duration_count is None


@pytest.mark.asyncio
async def test_followup_duration_uses_last_tool_call_context_not_guessed_facts():
    result, _usage, _model = await rewrite_and_extract(
        "what about the next 4 months",
        last_tool_call={
            "name": "estimate_drug_cost_all_channels",
            "arguments": {"drug_name": "lantus", "plan_key": "H0270-001", "days_supply": 30},
        },
    )
    assert result is not None
    normalized = result.normalized_message.lower()
    assert "lantus" in normalized
    assert "h0270-001" in normalized
    assert result.duration_count == 4
    assert result.duration_unit == "months"


@pytest.mark.asyncio
async def test_followup_without_last_tool_call_does_not_invent_a_drug():
    result, _usage, _model = await rewrite_and_extract("what about the next 4 months")
    assert result is not None
    normalized = result.normalized_message.lower()
    assert "lantus" not in normalized and "metformin" not in normalized
    assert result.duration_count == 4
    assert result.duration_unit == "months"


@pytest.mark.asyncio
async def test_explicit_start_date_extracted_as_raw_components_never_computed():
    result, _usage, _model = await rewrite_and_extract(
        "Lantus on H0270-001 starting September 1"
    )
    assert result is not None
    assert result.explicit_month == 9
    assert result.explicit_day == 1
    assert result.explicit_year is None
    # The schema has no field for a computed end date at all — nothing to assert absent,
    # since MediatorRewrite structurally cannot carry one.


@pytest.mark.asyncio
async def test_mediator_failure_falls_back_to_none_never_raises(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated API failure")

    monkeypatch.setattr(llm_client, "structured_complete", AsyncMock(side_effect=_boom))
    result, usage, model = await rewrite_and_extract("Lantus 30day supply H0270-001")
    assert result is None
    assert usage == TokenUsage()
    assert model


@pytest.mark.asyncio
async def test_mediator_empty_output_treated_as_failure(monkeypatch):
    async def _empty(*args, **kwargs):
        return MediatorRewrite(normalized_message="   "), TokenUsage(input_tokens=5, output_tokens=1)

    monkeypatch.setattr(llm_client, "structured_complete", AsyncMock(side_effect=_empty))
    result, usage, _model = await rewrite_and_extract("Lantus 30day supply H0270-001")
    assert result is None
    assert usage == TokenUsage()


def test_build_user_prompt_contains_current_message_marker_and_context():
    prompt = _build_user_prompt(
        "what about the next 4 months",
        last_tool_call={
            "name": "estimate_drug_cost_all_channels",
            "arguments": {"drug_name": "lantus", "plan_key": "H0270-001"},
        },
        pending_clarification=None,
        timezone=None,
    )
    assert "Current user message: what about the next 4 months" in prompt
    assert "Last cost estimate call this session: drug_name=lantus, plan_key=H0270-001" in prompt
    assert "Pending clarification: none" in prompt
