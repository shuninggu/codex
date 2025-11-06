from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import AsyncIterator, Iterable, List, Optional, Sequence

from .types import Message, ModelEvent, ToolCall


class ModelClient(ABC):
    """Abstract base class for model integrations."""

    @abstractmethod
    async def stream(
        self,
        messages: Sequence[Message],
        tools: Optional[List[dict]],
    ) -> AsyncIterator[ModelEvent]:
        """Yield streaming events for the assistant's turn."""


class OpenAIModelClient(ModelClient):
    """Minimal wrapper around the OpenAI Chat Completions API."""

    def __init__(self, model: str, client: Optional[object] = None):
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "OpenAIModelClient requires the 'openai' package. "
                "Install with `pip install openai`."
            ) from exc

        self._model = model
        self._client = client or AsyncOpenAI()

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Optional[List[dict]],
    ) -> AsyncIterator[ModelEvent]:
        payload_messages = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        api_tools = None
        if tools:
            api_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": spec["name"],
                        "description": spec.get("description", ""),
                        "parameters": spec.get("input_schema", {"type": "object"}),
                    },
                }
                for spec in tools
            ]

        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=payload_messages,
            tools=api_tools,
            stream=True,
        )

        async for chunk in stream:
            for choice in chunk.choices:
                delta = choice.delta
                if getattr(delta, "content", None):
                    for piece in delta.content:
                        if isinstance(piece, dict):
                            text = piece.get("text")
                            if text:
                                yield ModelEvent.message(text)
                        else:
                            yield ModelEvent.message(str(piece))
                if getattr(delta, "tool_calls", None):
                    for call in delta.tool_calls:
                        function = call.function
                        arguments = function.arguments or "{}"
                        yield ModelEvent.tool_call(
                            ToolCall(
                                tool=function.name,
                                arguments=_safe_json_loads(arguments),
                                call_id=call.id,
                            )
                        )


class MockModelClient(ModelClient):
    """
    Simple deterministic client that mimics tool calling.

    Patterns:
    - If the latest user message starts with ``run `` the remainder becomes a shell command.
    - Otherwise the assistant echoes the latest user content.
    """

    def __init__(self, reasoning: bool = True):
        self._reasoning = reasoning

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Optional[List[dict]],
    ) -> AsyncIterator[ModelEvent]:
        if not messages:
            return

        last_user = next((msg for msg in reversed(messages) if msg.role == "user"), None)
        if last_user is None:
            return

        content = last_user.content.strip()
        if content.startswith("run "):
            command = content.removeprefix("run ").strip()
            if self._reasoning:
                yield ModelEvent.reasoning(f"Considering shell command: {command}")
            await asyncio.sleep(0)
            yield ModelEvent.tool_call(
                ToolCall(tool="shell", arguments={"command": command})
            )
        else:
            yield ModelEvent.message(f"(mock) {content}")


def _safe_json_loads(payload: str) -> dict:
    import json

    payload = payload or "{}"
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload}
    if not isinstance(result, dict):
        return {"raw": result}
    return result

