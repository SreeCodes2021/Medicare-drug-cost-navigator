"""Coverage for llm/mock.py's provider-agnostic tool-result parsing.

Regression: mock_chat_with_tools is used regardless of LLM_PROVIDER, but its internal
_tool_result() helper originally only understood Anthropic-shape tool_result content blocks
and explicitly skipped OpenAI-shape {"role": "tool", ...} messages. With
LLM_PROVIDER=openai + LLM_MOCK=1, the navigator could never see its own tool result and the
mock always fell back to a generic "could not build a supported summary" response instead of
a real cost estimate.
"""

import pytest

from medicare_navigator.agent.navigator import navigator
from medicare_navigator.llm.client import llm_client
from tests.spuf_fixture import PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.fixture
def openai_provider(monkeypatch):
    monkeypatch.setattr(llm_client, "provider", "openai")
    yield
    monkeypatch.setattr(llm_client, "provider", "anthropic")


@pytest.mark.asyncio
async def test_mock_llm_openai_message_format_produces_real_cost_estimate(openai_provider):
    response = await navigator.run(
        f"metformin 500mg cost on plan {PLAN_FL_PDP}, spent $0 this year"
    )
    assert "could not build a supported summary" not in response.explanation
    assert response.estimate is not None
    assert response.estimate.cost_low == pytest.approx(5.00)
