import asyncio

from .agent import LightweightCodexAgent
from .model import MockModelClient
from .tools import ShellTool, ToolRegistry


async def run_demo() -> None:
    tools = ToolRegistry([ShellTool()])
    agent = LightweightCodexAgent(
        MockModelClient(),
        tools,
        system_prompt="You are a focused coding assistant.",
    )

    result = await agent.run_turn("run echo hello")
    print("Assistant:", result.assistant_response)
    for tool in result.tool_results:
        print("Tool Output:", tool.content)


if __name__ == "__main__":
    asyncio.run(run_demo())

