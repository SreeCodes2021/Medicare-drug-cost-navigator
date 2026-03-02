from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from medicare_navigator.config import settings

T = TypeVar("T", bound=BaseModel)

_FOLLOW_UP_COUNT_RE = re.compile(
    r"only one|how many alternative|did you find|just one|any other alternative|is that all",
    re.I,
)


@dataclass
class ToolCallSpec:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatWithToolsResult:
    content: str | None
    tool_calls: list[ToolCallSpec] = field(default_factory=list)


class LLMClient:
    """Provider-agnostic LLM adapter with deterministic fallback when no API key."""

    def __init__(self) -> None:
        self.provider = settings.llm_provider.lower()
        self.model = settings.llm_model

    def _has_credentials(self) -> bool:
        if self.provider == "openai":
            return bool(settings.openai_api_key)
        return bool(settings.anthropic_api_key)

    def model_label(self) -> str:
        return f"{self.provider}/{self.model}"

    def fallback_label(self, agent_name: str = "agent") -> str:
        return f"Deterministic fallback ({agent_name})"

    async def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        agent_name: str = "agent",
    ) -> T:
        if not self._has_credentials():
            return self._fallback_structured(user_prompt, response_model, agent_name)

        import instructor

        if self.provider == "openai":
            from openai import AsyncOpenAI

            client = instructor.from_openai(AsyncOpenAI(api_key=settings.openai_api_key))
            return await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_model=response_model,
            )

        from anthropic import AsyncAnthropic

        client = instructor.from_anthropic(AsyncAnthropic(api_key=settings.anthropic_api_key))
        return await client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            response_model=response_model,
        )

    async def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatWithToolsResult:
        if not self._has_credentials():
            raise RuntimeError("chat_with_tools requires LLM API credentials")

        if self.provider == "openai":
            return await self._openai_chat_with_tools(system_prompt, messages, tools)
        return await self._anthropic_chat_with_tools(system_prompt, messages, tools)

    async def _openai_chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatWithToolsResult:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        oai_messages = [{"role": "system", "content": system_prompt}, *messages]
        response = await client.chat.completions.create(
            model=self.model,
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
        return ChatWithToolsResult(content=choice.content, tool_calls=tool_calls)

    async def _anthropic_chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatWithToolsResult:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=self.model,
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
        return ChatWithToolsResult(content=content, tool_calls=tool_calls)

    def _fallback_structured(
        self, user_prompt: str, response_model: type[T], agent_name: str
    ) -> T:
        """Rule-based fallback for local dev without API keys."""
        if response_model.__name__ == "IntakeLLMOutput":
            return self._fallback_intake(user_prompt, response_model)
        if response_model.__name__ == "PolicyLLMOutput":
            return self._fallback_policy(user_prompt, response_model)
        if response_model.__name__ == "SynthesisLLMOutput":
            return self._fallback_synthesis(user_prompt, response_model)
        if response_model.__name__ == "ClarificationLLMOutput":
            return response_model.model_validate({"message": ""})
        return response_model.model_validate({})

    def _fallback_intake(self, user_prompt: str, model: type[T]) -> T:
        import re

        text = user_prompt.lower()
        current_message = user_prompt
        if "current message:" in text:
            current_message = user_prompt.split("Current message:", 1)[-1].split("\n", 1)[0]
            text = current_message.lower()

        drug = None
        dosage = None
        plan_id = None
        ytd = None
        intents = ["tier_lookup"]
        is_follow_up = "recent conversation:" in user_prompt.lower()
        follow_up_type = None

        if is_follow_up and _FOLLOW_UP_COUNT_RE.search(current_message):
            follow_up_type = "clarify_count"
            intents = ["alternatives"]

        if follow_up_type != "clarify_count":
            for name in ["metformin", "lisinopril", "atorvastatin", "omeprazole", "eliquis", "januvia", "lipitor"]:
                if name in text:
                    drug = name
                    break

            if not drug:
                for_match = re.search(r"\bfor\s+([a-zA-Z][a-zA-Z0-9-]*)", current_message, re.I)
                if for_match:
                    drug = for_match.group(1).lower()

            if not drug:
                stop = {
                    "plan",
                    "spent",
                    "show",
                    "what",
                    "tier",
                    "copay",
                    "alternatives",
                    "alternative",
                    "cost",
                    "the",
                    "for",
                    "and",
                    "only",
                    "find",
                    "many",
                    "that",
                    "have",
                    "you",
                    "did",
                    "eligible",
                    "eligibility",
                    "filling",
                    "cover",
                    "covered",
                }
                tokens = re.findall(r"[a-zA-Z]{4,}", current_message)
                for token in tokens:
                    if token.lower() not in stop:
                        drug = token.lower()
                        break

        dose_match = re.search(r"(\d+)\s*mg", text)
        if dose_match:
            dosage = f"{dose_match.group(1)}mg"

        plan_match = re.search(r"plan\s+([A-Za-z0-9]+-\d{3})", text, re.I)
        if not plan_match:
            plan_match = re.search(r"[A-Za-z]\d{4}-\d{3}", text, re.I)
        if plan_match:
            plan_id = plan_match.group(1).upper() if plan_match.lastindex else plan_match.group(0).upper()

        spend_patterns = [
            r"spent\s+\$?\s*(\d+(?:\.\d+)?)",
            r"\$(\d+(?:\.\d+)?)\s+ytd",
        ]
        for pattern in spend_patterns:
            spend_match = re.search(pattern, text)
            if spend_match:
                ytd = float(spend_match.group(1))
                break

        if "alternative" in text:
            intents = ["alternatives"]
        elif "why" in text or "change" in text or "went up" in text:
            intents = ["explain_cost_change"]
        else:
            intents = ["tier_lookup"]

        return model.model_validate(
            {
                "drug": drug,
                "dosage": dosage,
                "plan_id": plan_id,
                "ytd_oop_spend": ytd,
                "intents": intents,
                "confidence": 0.8 if drug else 0.2,
                "is_follow_up": is_follow_up,
                "follow_up_type": follow_up_type,
            }
        )

    def _fallback_policy(self, user_prompt: str, model: type[T]) -> T:
        return model.model_validate(
            {
                "claims": [
                    {
                        "claim": "Part D benefit phases and tier placement affect what beneficiaries pay.",
                        "source_id": "cms_part_d_redesign_2026",
                    }
                ]
            }
        )

    def _fallback_synthesis(self, user_prompt: str, model: type[T]) -> T:
        return model.model_validate(
            {
                "explanation": (
                    "Based on the retrieved government data for your query, the drug's formulary tier "
                    "and cost-sharing apply under your plan's benefit phase. See the structured results "
                    "and citations for specific figures."
                ),
                "citations": [],
            }
        )


llm_client = LLMClient()
