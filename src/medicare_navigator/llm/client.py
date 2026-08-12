from __future__ import annotations

import asyncio
import json
from typing import Any, TypeVar

from pydantic import BaseModel

from medicare_navigator.config import settings
from medicare_navigator.llm.errors import LLMNotConfiguredError, LLMRequestError
from medicare_navigator.llm.mock import mock_chat_with_tools, mock_structured_completion
from medicare_navigator.llm.models import ModelSpec, resolve_model
from medicare_navigator.llm.types import ChatWithToolsResult, TokenUsage, ToolCallSpec

T = TypeVar("T", bound=BaseModel)


def _strip_json_fence(content: str) -> str:
    """Defensive parsing: strip a leading/trailing ```json fence or leading prose that a
    model occasionally emits around JSON despite instructions not to."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text.lstrip("`")
        if text.endswith("```"):
            text = text[:-3]
        elif "```" in text:
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    return text

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

    async def structured_complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        model: str | None = None,
    ) -> tuple[T, TokenUsage]:
        """No-tool structured-output call: one JSON object back, validated against
        response_model. Used by the mediator, not the main tool-calling chat loop."""
        spec = self.resolve_request(model)
        if settings.llm_mock_mode:
            return await mock_structured_completion(user_prompt, response_model, model=spec.id)

        return await self._with_retry(
            lambda: self._structured_complete_live(system_prompt, user_prompt, response_model, spec),
            timeout=settings.mediator_timeout_seconds,
            max_retries=settings.mediator_max_retries,
        )

    async def _structured_complete_live(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        spec: ModelSpec,
    ) -> tuple[T, TokenUsage]:
        if spec.provider == "openai":
            return await self._openai_structured_complete(system_prompt, user_prompt, response_model, spec)
        return await self._anthropic_structured_complete(system_prompt, user_prompt, response_model, spec.id)

    async def _openai_structured_complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        spec: ModelSpec,
    ) -> tuple[T, TokenUsage]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        schema = response_model.model_json_schema()
        full_user_prompt = (
            f"{user_prompt}\n\n"
            "Respond with a single JSON object matching this schema exactly, and nothing "
            f"else — no prose, no code fence:\n{json.dumps(schema)}"
        )
        create_kwargs: dict[str, Any] = {
            "model": spec.id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": full_user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        if spec.openai_reasoning_effort is not None:
            create_kwargs["reasoning_effort"] = spec.openai_reasoning_effort
        response = await client.chat.completions.create(**create_kwargs)
        content = response.choices[0].message.content or ""
        usage = TokenUsage()
        if response.usage:
            usage = TokenUsage(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
            )
        result = response_model.model_validate_json(_strip_json_fence(content))
        return result, usage

    async def _anthropic_structured_complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        model: str,
    ) -> tuple[T, TokenUsage]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        schema = response_model.model_json_schema()
        tool_name = "emit_result"
        tool = {
            "name": tool_name,
            "description": "Emit the structured result for this request.",
            "input_schema": schema,
        }
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
        )
        tool_input: dict[str, Any] = {}
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                tool_input = block.input if isinstance(block.input, dict) else {}
                break
        usage = TokenUsage()
        if response.usage:
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens or 0,
                output_tokens=response.usage.output_tokens or 0,
            )
        result = response_model.model_validate(tool_input)
        return result, usage

    async def _with_retry(
        self,
        coro_factory,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        timeout = settings.llm_timeout_seconds if timeout is None else timeout
        max_retries = settings.llm_max_retries if max_retries is None else max_retries
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await asyncio.wait_for(coro_factory(), timeout=timeout)
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(0.5 * (2**attempt))
        raise LLMRequestError(
            f"LLM request failed after {max_retries + 1} attempt(s): {last_exc}"
        ) from last_exc

    async def _chat_with_tools_live(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        spec: ModelSpec,
    ) -> ChatWithToolsResult:
        if spec.provider == "openai":
            return await self._openai_chat_with_tools(system_prompt, messages, tools, spec)
        return await self._anthropic_chat_with_tools(system_prompt, messages, tools, spec.id)

    async def _openai_chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        spec: ModelSpec,
    ) -> ChatWithToolsResult:
        import json

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        oai_messages = [{"role": "system", "content": system_prompt}, *messages]
        create_kwargs: dict[str, Any] = {
            "model": spec.id,
            "messages": oai_messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        if spec.openai_reasoning_effort is not None:
            create_kwargs["reasoning_effort"] = spec.openai_reasoning_effort
        response = await client.chat.completions.create(**create_kwargs)
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
