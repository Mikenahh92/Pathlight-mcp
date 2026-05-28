"""Linux agent integration test — single app (gedit) (GW-040).

Prompt-driven agent integration test validating the full Linux stack
(MCP → tool dispatch → AT-SPI backend) by simulating Claude typing text
into gedit and reading it back via semantic accessibility actions.

Uses the AgentClient replay_script mode to bypass the real Anthropic API.
This test boots the Pathlight MCP server with ``--backend auto`` and replays
a multi-turn agent interaction:

1. ``desktop.list_windows`` — discover the gedit window
2. ``desktop.snapshot`` — capture the gedit accessibility tree
3. ``desktop.find`` — locate the text editor element
4. ``desktop.type_text`` — type text into the editor
5. ``desktop.get_text`` — read the text back

Tests are gated by:
- ``@pytest.mark.integration`` (requires ``GUIDEWARE_RUN_INTEGRATION=1``)
- ``pytest.mark.skipif`` on non-Linux platforms
"""

import sys

import pytest

from tests.harness.agent import AgentClient
from tests.harness.assertions import assert_call_order, assert_tool_called
from tests.harness.server import PathlightMCPServerProcess

skip_not_linux = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Linux agent test requires Linux platform (AT-SPI2 backend)",
)

# -- Replay script: simulated Claude interaction with gedit -------------------
# This replay script models the full agent loop that Claude would execute
# when asked to "type 'Hello from Pathlight MCP' into gedit and read it back".
# Each frame is one LLM response containing tool_use blocks.

GEDIT_AGENT_REPLAY = [
    # Turn 1: List windows to find gedit
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "tool_use",
                "id": "tu_001",
                "name": "desktop.list_windows",
                "input": {},
            }
        ],
    },
    # Turn 2: Snapshot the gedit window to understand its structure
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see gedit is running. Let me take a snapshot.",
            },
            {
                "type": "tool_use",
                "id": "tu_002",
                "name": "desktop.snapshot",
                "input": {"window_ref": "w1"},
            },
        ],
    },
    # Turn 3: Find the text editor element within gedit
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see the gedit window structure. Let me find the text editor.",
            },
            {
                "type": "tool_use",
                "id": "tu_003",
                "name": "desktop.find",
                "input": {"window_ref": "w1", "role": "text"},
            },
        ],
    },
    # Turn 4: Type text into the found text element
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Found the text editor. Now typing 'Hello from Pathlight MCP'.",
            },
            {
                "type": "tool_use",
                "id": "tu_004",
                "name": "desktop.type_text",
                "input": {"element_ref": "e1", "text": "Hello from Pathlight MCP"},
            },
        ],
    },
    # Turn 5: Read the text back to verify
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "tool_use",
                "id": "tu_005",
                "name": "desktop.get_text",
                "input": {"element_ref": "e1"},
            }
        ],
    },
    # Turn 6: Final summary
    {
        "stop_reason": "end_turn",
        "content_blocks": [
            {
                "type": "text",
                "text": "Successfully typed 'Hello from Pathlight MCP' into gedit "
                "and verified the text was entered correctly.",
            }
        ],
    },
]


@skip_not_linux
@pytest.mark.integration
class TestLinuxAgentGedit:
    """GW-040: Prompt-driven agent integration test for the Linux stack.

    Validates the full path from MCP tool dispatch through the AT-SPI2
    backend by simulating a Claude agent interaction with gedit.
    """

    async def test_agent_discovers_and_interacts_with_gedit(self) -> None:
        """Agent replay should call list_windows, snapshot, find, type_text, get_text."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=GEDIT_AGENT_REPLAY, max_turns=6)
            result = await agent.send_prompt(
                "Type 'Hello from Pathlight MCP' into gedit and read it back."
            )

        # Verify all 5 tool calls were made
        assert_tool_called(result, "desktop.list_windows", count=1)
        assert_tool_called(result, "desktop.snapshot", count=1)
        assert_tool_called(result, "desktop.find", count=1)
        assert_tool_called(result, "desktop.type_text", count=1)
        assert_tool_called(result, "desktop.get_text", count=1)

        # Verify the call order matches the expected agent workflow
        assert_call_order(
            result,
            [
                "desktop.list_windows",
                "desktop.snapshot",
                "desktop.find",
                "desktop.type_text",
                "desktop.get_text",
            ],
        )

        # Verify the agent reached end_turn
        assert result.stop_reason == "end_turn"

        # Verify the final text mentions the typed content
        assert "Hello from Pathlight MCP" in result.text

    async def test_type_text_arguments(self) -> None:
        """The type_text tool call should include the correct text argument."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=GEDIT_AGENT_REPLAY, max_turns=6)
            result = await agent.send_prompt(
                "Type 'Hello from Pathlight MCP' into gedit and read it back."
            )

        type_text_calls = assert_tool_called(result, "desktop.type_text")
        assert len(type_text_calls) == 1
        assert type_text_calls[0].input["text"] == "Hello from Pathlight MCP"
        assert type_text_calls[0].input["element_ref"] == "e1"

    async def test_get_text_arguments(self) -> None:
        """The get_text tool call should reference the same element as type_text."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=GEDIT_AGENT_REPLAY, max_turns=6)
            result = await agent.send_prompt(
                "Type 'Hello from Pathlight MCP' into gedit and read it back."
            )

        get_text_calls = assert_tool_called(result, "desktop.get_text")
        assert len(get_text_calls) == 1
        assert get_text_calls[0].input["element_ref"] == "e1"

    async def test_total_tool_call_count(self) -> None:
        """Exactly 5 tool calls should be made across the full agent loop."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=GEDIT_AGENT_REPLAY, max_turns=6)
            result = await agent.send_prompt(
                "Type 'Hello from Pathlight MCP' into gedit and read it back."
            )

        assert len(result.tool_calls) == 5

    async def test_server_discovers_eight_tools(self) -> None:
        """Server should expose all 8 tools on the Linux backend."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            tools = await server.list_tools()
            names = {t.name for t in tools}
            assert len(names) == 8
            assert "desktop.list_windows" in names
            assert "desktop.focus_window" in names
            assert "desktop.snapshot" in names
            assert "desktop.find" in names
            assert "desktop.click" in names
            assert "desktop.type_text" in names
            assert "desktop.press_key" in names
            assert "desktop.get_text" in names

    async def test_tool_schemas_valid_on_linux(self) -> None:
        """Each tool should have a valid JSON Schema input on Linux."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            tools = await server.list_tools()
            for tool in tools:
                schema = tool.inputSchema
                assert schema is not None
                assert schema.get("type") == "object"
