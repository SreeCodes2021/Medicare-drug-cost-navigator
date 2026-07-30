from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from medicare_navigator.config import settings
from medicare_navigator.llm.client import LLMClient
from medicare_navigator.llm.models import resolve_model


@pytest.mark.asyncio
async def test_openai_reasoning_model_sets_effort_none_for_tool_calls(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    spec = resolve_model("gpt-5.6-luna")
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
        )
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = create

    with patch(
        "openai.AsyncOpenAI",
        return_value=mock_client,
    ):
        client = LLMClient()
        await client._openai_chat_with_tools("system", [], [], spec)

    create.assert_awaited_once()
    assert create.await_args.kwargs["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_openai_non_reasoning_model_omits_reasoning_effort(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    spec = resolve_model("gpt-5.4-nano")
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
        )
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = create

    with patch(
        "openai.AsyncOpenAI",
        return_value=mock_client,
    ):
        client = LLMClient()
        await client._openai_chat_with_tools("system", [], [], spec)

    create.assert_awaited_once()
    assert "reasoning_effort" not in create.await_args.kwargs
