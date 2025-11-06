from __future__ import annotations

from typing import Awaitable, Callable, Optional, Sequence

from .model import ModelClient
from .session import ConversationSession
from .tools import ToolRegistry, ToolResult
from .types import ModelEvent, TurnResult

StreamCallback = Callable[[ModelEvent], Awaitable[None] | None]


class LightweightCodexAgent:
    """
    High level orchestration loop inspired by the Codex Rust agent.

    The agent keeps conversation state, streams model events, executes tools on
    demand, and feeds tool outputs back into the next model turn.
    """

    def __init__(
        self,
        model: ModelClient,
        tools: ToolRegistry | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._model = model
        self._tools = tools or ToolRegistry()
        self._session = ConversationSession(system_prompt=system_prompt)

    @property
    def session(self) -> ConversationSession:
        return self._session

    async def run_turn(
        self,
        user_message: str,
        *,
        on_event: Optional[StreamCallback] = None,
    ) -> TurnResult:
        """Process a single user message."""

        self._session.add_user_message(user_message)
        tool_results: list[ToolResult] = []
        reasoning_chunks: list[str] = []

        while True:
            assistant_chunks: list[str] = []
            tool_invoked = False

            async for event in self._model.stream(
                self._session.as_prompt(), self._tools.specs()
            ):
                if on_event:
                    maybe_await = on_event(event)
                    if isinstance(maybe_await, Awaitable):
                        await maybe_await

                if event.type == "message":
                    chunk = str(event.value)
                    assistant_chunks.append(chunk)
                elif event.type == "reasoning":
                    reasoning_chunks.append(str(event.value))
                elif event.type == "tool_call":
                    tool_invoked = True
                    tool_call = event.value
                    tool_result = await self._tools.execute(tool_call)
                    tool_results.append(tool_result)
                    self._session.add_tool_result(tool_result)
                    break

            if tool_invoked:
                # Tool outputs are already appended to the conversation history.
                # Continue the loop to send them back to the model.
                continue

            assistant_text = "".join(assistant_chunks).strip() or None
            if assistant_text:
                self._session.add_assistant_message(assistant_text)
            return TurnResult(
                assistant_response=assistant_text,
                tool_results=tool_results,
                reasoning=reasoning_chunks,
            )

