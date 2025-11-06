# Lightweight Codex Agent

This directory contains a small, educational reimplementation of the Codex
agent loop that is easier to read and experiment with. It focuses on the three
key responsibilities of the full Rust agent:

1. Track the evolving conversation state.
2. Talk to a language model using a streaming API.
3. Execute tool calls (for example `shell`) and return the results back to the
   model on the next turn.

## Components

| File | Responsibility |
| --- | --- |
| `agent.py` | Orchestrates turns: forwards user input, streams model events, runs tools, and records the final assistant message. |
| `model.py` | Defines the `ModelClient` interface plus two implementations. `MockModelClient` is deterministic and runs offline; `OpenAIModelClient` uses the Chat Completions API. |
| `session.py` | Maintains message history similarly to Codex' `ConversationHistory`. |
| `tools.py` | Registers tools and implements a `ShellTool` compatible with the agent loop. |
| `types.py` | Dataclasses shared across the lightweight implementation. |

## Getting started

Run the mock setup without network access:

```python
import asyncio
from lightweight_codex import LightweightCodexAgent, MockModelClient, ShellTool, ToolRegistry


async def main():
    tools = ToolRegistry([ShellTool()])
    agent = LightweightCodexAgent(MockModelClient(), tools)

    result = await agent.run_turn("run echo hello from codex")
    print(result.assistant_response)
    for tool in result.tool_results:
        print(tool.content)


asyncio.run(main())
```

To use a real model, install the `openai` package and initialise an
`OpenAIModelClient` with your API key in the environment. The agent interface
remains the same.

## Why this structure?

The files mirror the responsibilities of the production Rust code:

- `agent.py` is a compact counterpart to `codex-rs/core/src/codex.rs`,
  surfacing the run loop that processes user turns.
- `session.py` reflects the role of `conversation_history.rs`.
- `tools.py` draws on `tools/router.rs`, `tools/mod.rs`, and the shell handler.

This makes it easier to map the ideas from the Rust implementation to a Python
prototype for experimentation or teaching.

