from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallSpec:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatWithToolsResult:
    content: str | None
    tool_calls: list[ToolCallSpec] = field(default_factory=list)
