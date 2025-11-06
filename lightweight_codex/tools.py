from __future__ import annotations

import asyncio
import json
import shlex
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .types import ToolCall, ToolResult


class ToolError(RuntimeError):
    """Raised when a tool invocation fails."""


class Tool:
    """Base class for tools usable by the agent."""

    name: str = "tool"
    description: str = ""

    async def run(self, arguments: Dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def spec(self) -> Dict[str, Any]:
        """Return a JSON-serialisable tool description for prompting models."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {"type": "object"},
        }


@dataclass(slots=True)
class ShellTool(Tool):
    """Simple exec tool mirroring the behaviour of Codex' shell handler."""

    name: str = "shell"
    description: str = (
        "Execute a shell command. Accepts {'command': str | list[str], 'cwd': str | None}."
    )
    default_timeout: float = 30.0

    async def run(self, arguments: Dict[str, Any]) -> ToolResult:
        if "command" not in arguments:
            raise ToolError("shell tool requires a 'command' argument")

        command = arguments["command"]
        if isinstance(command, str):
            cmd_list = shlex.split(command)
        elif isinstance(command, Iterable):
            cmd_list = [str(part) for part in command]
        else:
            raise ToolError("command must be a string or iterable of strings")

        if not cmd_list:
            raise ToolError("command cannot be empty")

        cwd = arguments.get("cwd")
        timeout = float(arguments.get("timeout", self.default_timeout))

        process = await asyncio.create_subprocess_exec(
            *cmd_list,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            content = json.dumps(
                {
                    "exit_code": None,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout:.1f}s",
                }
            )
            return ToolResult(tool=self.name, call_id=None, content=content, success=False)

        exit_code = process.returncode
        payload = {
            "exit_code": exit_code,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }
        return ToolResult(
            tool=self.name,
            call_id=None,
            content=json.dumps(payload),
            success=exit_code == 0,
        )


class ToolRegistry:
    """Registers tool implementations and dispatches invocations."""

    def __init__(self, tools: Optional[List[Tool]] = None):
        self._tools: Dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.add(tool)

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> List[Dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values()]

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.tool)
        if tool is None:
            raise ToolError(f"unknown tool '{call.tool}'")
        result = await tool.run(call.arguments)
        result.call_id = call.call_id
        return result

