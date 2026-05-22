"""Anthropic agent client for integration testing.

Connects to the Guidewire MCP server, converts MCP tools to Anthropic
tool definitions, sends prompts via the Anthropic Messages API, and
records tool calls for verification.

Usage::

    async with GuidewireServerProcess() as server:
        agent = AgentClient(server)
        result = await agent.send_prompt("List all windows")
        assert_tool_called(result, "desktop.list_windows")
"""

import os
from dataclasses import dataclass, field
from typing import Any

import anthropic

__all__ = ["AgentClient", "ToolCallRecord"]


@dataclass
class ToolCallRecord:
    """Captured tool invocation from an agent response.

    Attributes:
        name: Tool name as called by the agent (e.g. ``"desktop.list_windows"``).
        input: Raw input dict sent to the tool.
    """

    name: str
    input: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"ToolCallRecord(name={self.name!r}, input={self.input!r})"


@dataclass
class AgentResult:
    """Result of an agent interaction.

    Attributes:
        text: Concatenated text content blocks from the final response.
        tool_calls: All tool invocations recorded across the conversation.
        stop_reason: The Anthropic stop reason (``"end_turn"``, ``"max_turns"``,
            ``"loop_detected"``).
        loop_detected: True if the agent was stopped because the loop detector
            fired (same tool called repeatedly with identical input).
    """

    text: str
    tool_calls: list[ToolCallRecord]
    stop_reason: str | None = None
    loop_detected: bool = False


class AgentClient:
    """Anthropic agent that uses Guidewire MCP tools.

    Wraps an MCP ``ClientSession`` to convert MCP tool schemas into
    Anthropic tool definitions, executes prompts, and records tool calls.

    Args:
        server: A running :class:`GuidewireServerProcess`.
        model: Anthropic model to use (default from ``ANTHROPIC_MODEL`` env
            or ``"claude-sonnet-4-20250514"``).
        base_url: Anthropic API base URL (default from ``ANTHROPIC_BASE_URL``
            env or standard endpoint).
        api_key: Anthropic API key (default from ``ANTHROPIC_API_KEY`` env).
        max_turns: Maximum tool-use rounds before stopping (default 5).
        replay_script: Optional pre-scripted responses for dry-run/replay
            mode.  When set, bypasses the Anthropic API entirely.
        loop_threshold: Number of consecutive identical tool calls before
            the loop detector breaks the agent loop (default 3).
    """

    def __init__(
        self,
        server: Any,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        max_turns: int = 5,
        replay_script: list[dict[str, Any]] | None = None,
        loop_threshold: int = 3,
    ) -> None:
        self._server = server
        self._model = model or os.environ.get(
            "ANTHROPIC_MODEL",
            "claude-sonnet-4-20250514",
        )
        self._base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._max_turns = max_turns
        self._replay_script = replay_script
        self._replay_index = 0
        self._loop_threshold = loop_threshold
        self._anthropic_client: anthropic.AsyncAnthropic | None = None
        self._mcp_tools: list[dict[str, Any]] = []
        self._tool_handlers: dict[str, Any] = {}

    async def _ensure_client(self) -> None:
        """Initialize the Anthropic client and load MCP tool schemas."""
        if self._anthropic_client is not None:
            return

        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url

        self._anthropic_client = anthropic.AsyncAnthropic(**kwargs)

        # Fetch tool schemas from the MCP server
        mcp_tools = await self._server.list_tools()
        self._mcp_tools = []
        self._tool_handlers = {}
        for tool in mcp_tools:
            self._mcp_tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                }
            )
            self._tool_handlers[tool.name] = tool

    def _to_anthropic_tools(self) -> list[dict[str, Any]]:
        """Convert MCP tool schemas to Anthropic tool definitions."""
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in self._mcp_tools
        ]

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Execute a tool call against the MCP server and return the result text."""
        result = await self._server.call_tool(tool_name, arguments=tool_input)
        # CallToolResult.content is a list of TextContent / ImageContent items.
        parts: list[str] = []
        for item in result.content:
            if hasattr(item, "text"):
                parts.append(item.text)
        return "\n".join(parts)

    async def _replay_next(self) -> dict[str, Any] | None:
        """Return the next replay frame or ``None`` if exhausted."""
        if self._replay_script is None or self._replay_index >= len(self._replay_script):
            return None
        frame = self._replay_script[self._replay_index]
        self._replay_index += 1
        return frame

    async def send_prompt(self, prompt: str) -> AgentResult:
        """Send a prompt to the agent and return the result.

        Runs the agent loop: prompt → LLM → tool calls → execute → repeat
        until the model stops or the turn limit is reached.

        When ``replay_script`` was provided at init, the Anthropic API is
        bypassed and pre-scripted responses are consumed instead.

        Args:
            prompt: User prompt to send to the agent.

        Returns:
            An :class:`AgentResult` with text, tool calls, and stop reason.
        """
        await self._ensure_client()

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": prompt},
        ]
        tool_calls: list[ToolCallRecord] = []

        for _ in range(self._max_turns):
            # Replay mode — consume next scripted frame
            frame = await self._replay_next()
            if frame is not None:
                text_parts, tool_use_blocks = self._parse_replay_frame(frame)
            else:
                response = await self._anthropic_client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    tools=self._to_anthropic_tools(),
                    messages=messages,
                )
                content_blocks = response.content
                text_parts: list[str] = []
                tool_use_blocks: list[dict[str, Any]] = []

                for block in content_blocks:
                    if block.type == "text":
                        text_parts.append(block.text)
                    elif block.type == "tool_use":
                        tool_use_blocks.append(
                            {
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )

            # Record tool calls
            for tu in tool_use_blocks:
                tool_calls.append(ToolCallRecord(name=tu["name"], input=tu["input"]))

            # Loop detection: if the same tool is called repeatedly with
            # identical input, inject a break-hint into the conversation and
            # stop the agent if the pattern persists beyond the threshold.
            stop_reason = frame["stop_reason"] if frame is not None else response.stop_reason
            if (
                tool_use_blocks
                and len(tool_calls) >= self._loop_threshold
                and stop_reason != "end_turn"
            ):
                recent_names = [tc.name for tc in tool_calls[-self._loop_threshold :]]
                if len(set(recent_names)) == 1 and tool_calls[-1].input == tool_calls[-2].input:
                    # Inject a hint telling the model to move on, then stop
                    messages.append({"role": "assistant", "content": content_blocks})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"You have called {recent_names[0]} {self._loop_threshold} "
                                f"times in a row with the same input. This is a loop. "
                                f"STOP repeating this tool call and move on to the next step "
                                f"in the task. If you are stuck, report what you have "
                                f"accomplished so far and end your turn."
                            ),
                        }
                    )
                    return AgentResult(
                        text="\n".join(text_parts),
                        tool_calls=tool_calls,
                        stop_reason="loop_detected",
                        loop_detected=True,
                    )
            if stop_reason == "end_turn" or not tool_use_blocks:
                return AgentResult(
                    text="\n".join(text_parts),
                    tool_calls=tool_calls,
                    stop_reason=stop_reason,
                )

            # Execute tools and build assistant message with results
            tool_results = []
            for tu in tool_use_blocks:
                result_text = await self._execute_tool(tu["name"], tu["input"])
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": result_text,
                    }
                )

            # Append assistant message with tool_use blocks
            if frame is not None:
                content_blocks = frame.get("content_blocks", [])
            messages.append({"role": "assistant", "content": content_blocks})
            # Append tool results as user message
            messages.append({"role": "user", "content": tool_results})

        return AgentResult(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason="max_turns",
        )

    @staticmethod
    def _parse_replay_frame(frame: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
        """Parse a replay frame into text parts and tool-use blocks."""
        text_parts: list[str] = []
        tool_use_blocks: list[dict[str, Any]] = []
        for block in frame.get("content_blocks", []):
            if block["type"] == "text":
                text_parts.append(block["text"])
            elif block["type"] == "tool_use":
                tool_use_blocks.append(
                    {
                        "id": block["id"],
                        "name": block["name"],
                        "input": block["input"],
                    }
                )
        return text_parts, tool_use_blocks

    @property
    def available_tools(self) -> list[str]:
        """Names of tools available to the agent."""
        return [t["name"] for t in self._mcp_tools]
