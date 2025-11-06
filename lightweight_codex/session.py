from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Sequence

from .types import Message, ToolResult


@dataclass(slots=True)
class ConversationSession:
    """Tracks the evolving conversation history."""

    system_prompt: str | None = None
    messages: List[Message] = field(default_factory=list)

    def add_system_prompt(self, content: str) -> None:
        if self.system_prompt is None:
            self.system_prompt = content

    def add_user_message(self, content: str) -> None:
        self.messages.append(Message(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        self.messages.append(Message(role="assistant", content=content))

    def add_tool_result(self, result: ToolResult) -> None:
        self.messages.append(
            Message(
                role="tool",
                content=result.content,
                metadata={
                    "tool": result.tool,
                    "call_id": result.call_id,
                    "success": result.success,
                    **result.metadata,
                },
            )
        )

    def as_prompt(self) -> Sequence[Message]:
        if self.system_prompt is None:
            return list(self.messages)

        return [Message(role="system", content=self.system_prompt)] + list(self.messages)

    def merge_reasoning(self, chunks: Iterable[str]) -> None:
        for chunk in chunks:
            if not chunk:
                continue
            self.messages.append(
                Message(role="assistant", content=chunk, metadata={"reasoning": True})
            )

