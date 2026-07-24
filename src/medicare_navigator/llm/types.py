from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallSpec:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass
class ChatWithToolsResult:
    content: str | None
    tool_calls: list[ToolCallSpec] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
