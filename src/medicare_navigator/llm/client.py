from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from pydantic import BaseModel

from medicare_navigator.config import settings
from medicare_navigator.llm.errors import LLMNotConfiguredError, LLMRequestError
from medicare_navigator.llm.mock import mock_chat_with_tools
from medicare_navigator.llm.models import ModelSpec, resolve_model
from medicare_navigator.llm.types import ChatWithToolsResult, TokenUsage, ToolCallSpec

T = TypeVar("T", bound=BaseModel)

__all__ = ["LLMClient", "llm_client", "ChatWithToolsResult", "ToolCallSpec", "TokenUsage"]


class LLMClient:
    """Provider-agnostic LLM adapter. Requires API credentials or explicit mock mode."""

    def __init__(self) -> None:
        self.provider = settings.llm_provider.lower()
        self.model = settings.llm_model

    def _has_credentials(self, provider: str | None = None) -> bool:
        active = (provider or self.provider).lower()
        if active == "openai":
            return bool(settings.openai_api_key)
        return bool(settings.anthropic_api_key)

    def is_available(self) -> bool:
        if settings.llm_mock_mode:
            return True
        return bool(settings.openai_api_key) or bool(settings.anthropic_api_key)

    def require_available(self, provider: str | None = None) -> None:
        active = (provider or self.provider).lower()
        if not self._has_credentials(active) and not settings.llm_mock_mode:
            hint = settings.llm_configuration_hint(active)
            raise LLMNotConfiguredError(
                f"LLM is not configured for provider '{active}'. {hint} "
                "Alternatively, enable LLM_MOCK=1 for local testing."
            )

    def model_label(self, model: str | None = None, provider: str | None = None) -> str:
        spec = resolve_model(model)
        active_provider = provider or spec.provider
        active_model = model or spec.id
        if settings.llm_mock_mode:
            return f"mock/{active_provider}/{active_model}"
        return f"{active_provider}/{active_model}"

    def resolve_request(self, model: str | None = None) -> ModelSpec:
        spec = resolve_model(model)
        self.require_available(spec.provider)
        return spec

    async def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> ChatWithToolsResult:
        spec = self.resolve_request(model)
        if settings.llm_mock_mode:
            return await mock_chat_with_tools(system_prompt, messages, tools, model=spec.id)

        return await self._with_retry(
            lambda: self._chat_with_tools_live(system_prompt, messages, tools, spec)
        )

    async def _with_retry(self, coro_factory):
        last_exc: Exception | None = None
        for attempt in range(settings.llm_max_retries + 1):
            try:
                return await asyncio.wait_for(
                    coro_factory(), timeout=settings.llm_timeout_seconds
                )
            except Exception as exc:
                last_exc = exc
                if attempt < settings.llm_max_retries:
                    await asyncio.sleep(0.5 * (2**attempt))
        raise LLMRequestError(
            f"LLM request failed after {settings.llm_max_retries + 1} attempt(s): {last_exc}"
        ) from last_exc

    async def _chat_with_tools_live(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        spec: ModelSpec,
    ) -> ChatWithToolsResult:
        if spec.provider == "openai":
            return await self._openai_chat_with_tools(system_prompt, messages, tools, spec.id)
        return await self._anthropic_chat_with_tools(system_prompt, messages, tools, spec.id)

    async def _openai_chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
    ) -> ChatWithToolsResult:
        import json

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        oai_messages = [{"role": "system", "content": system_prompt}, *messages]
        response = await client.chat.completions.create(
            model=model,
            messages=oai_messages,
            tools=tools,
            tool_choice="auto",
        )
        choice = response.choices[0].message
        tool_calls: list[ToolCallSpec] = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                tool_calls.append(
                    ToolCallSpec(id=tc.id, name=tc.function.name, arguments=args)
                )
        usage = TokenUsage()
        if response.usage:
            usage = TokenUsage(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
            )
        return ChatWithToolsResult(content=choice.content, tool_calls=tool_calls, usage=usage)

    async def _anthropic_chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
    ) -> ChatWithToolsResult:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=tools,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCallSpec] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCallSpec(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )
        content = "\n".join(text_parts).strip() or None
        usage = TokenUsage()
        if response.usage:
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens or 0,
                output_tokens=response.usage.output_tokens or 0,
            )
        return ChatWithToolsResult(content=content, tool_calls=tool_calls, usage=usage)


llm_client = LLMClient()
