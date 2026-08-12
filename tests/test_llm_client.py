from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from medicare_navigator.config import settings
from medicare_navigator.llm.client import LLMClient
from medicare_navigator.llm.errors import LLMRequestError
from medicare_navigator.llm.models import resolve_model


class _DummyStructuredModel(BaseModel):
    normalized_message: str
    duration_count: int | None = None


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


@pytest.mark.asyncio
async def test_openai_structured_complete_sets_reasoning_effort_for_luna(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    payload = json.dumps({"normalized_message": "Lantus, 30-day supply", "duration_count": None})
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = create

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        client = LLMClient()
        result, usage = await client.structured_complete(
            "system", "user", _DummyStructuredModel, model="gpt-5.6-luna"
        )

    create.assert_awaited_once()
    assert create.await_args.kwargs["reasoning_effort"] == "none"
    assert create.await_args.kwargs["response_format"] == {"type": "json_object"}
    assert result.normalized_message == "Lantus, 30-day supply"
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5


@pytest.mark.asyncio
async def test_openai_structured_complete_strips_code_fence(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    fenced = "```json\n" + json.dumps({"normalized_message": "Metformin 500mg"}) + "\n```"
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=fenced))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = create

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        client = LLMClient()
        result, _usage = await client.structured_complete(
            "system", "user", _DummyStructuredModel, model="gpt-5.4-nano"
        )

    assert result.normalized_message == "Metformin 500mg"


@pytest.mark.asyncio
async def test_openai_structured_complete_malformed_json_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "mediator_max_retries", 0)

    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json at all"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = create

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        client = LLMClient()
        with pytest.raises(LLMRequestError):
            await client.structured_complete("system", "user", _DummyStructuredModel)


@pytest.mark.asyncio
async def test_anthropic_structured_complete_forces_tool_choice(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    tool_use_block = SimpleNamespace(
        type="tool_use",
        name="emit_result",
        input={"normalized_message": "Lantus on H0270-001"},
    )
    create = AsyncMock(
        return_value=SimpleNamespace(
            content=[tool_use_block],
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )
    )
    mock_client = MagicMock()
    mock_client.messages.create = create

    with patch("anthropic.AsyncAnthropic", return_value=mock_client):
        client = LLMClient()
        result, usage = await client.structured_complete(
            "system", "user", _DummyStructuredModel, model="claude-haiku-4-5-20251001"
        )

    create.assert_awaited_once()
    assert create.await_args.kwargs["tool_choice"] == {"type": "tool", "name": "emit_result"}
    assert result.normalized_message == "Lantus on H0270-001"
    assert usage.input_tokens == 7
    assert usage.output_tokens == 3
