from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class Message:
    """Represents a conversation message."""

    role: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCall:
    """Description of a tool invocation requested by the model."""

    tool: str
    arguments: Dict[str, Any]
    call_id: Optional[str] = None


@dataclass(slots=True)
class ToolResult:
    """Output returned by a tool implementation."""

    tool: str
    call_id: Optional[str]
    content: str
    success: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelEvent:
    """Streaming event returned by a model client."""

    type: str
    value: Any

    @staticmethod
    def message(content: str) -> "ModelEvent":
        return ModelEvent(type="message", value=content)

    @staticmethod
    def tool_call(call: ToolCall) -> "ModelEvent":
        return ModelEvent(type="tool_call", value=call)

    @staticmethod
    def reasoning(content: str) -> "ModelEvent":
        return ModelEvent(type="reasoning", value=content)


@dataclass(slots=True)
class TurnResult:
    """Summarises the outcome of a single user turn."""

    assistant_response: Optional[str]
    tool_results: List[ToolResult] = field(default_factory=list)
    reasoning: List[str] = field(default_factory=list)

