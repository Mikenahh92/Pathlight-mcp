"""Linux agent integration test — Nautilus (GW-042).

Prompt-driven agent integration test validating the full Linux stack
(MCP -> tool dispatch -> AT-SPI backend) by simulating Claude navigating
the Nautilus file manager.

This test exercises navigation within a GTK4 application that uses a
sidebar-based layout with location bookmarks and an icon grid for the
content area. Nautilus has distinct accessibility characteristics compared
to gedit (text editor), including a read-only path bar, navigation buttons,
and a hierarchical sidebar.

Uses the AgentClient replay_script mode to bypass the real Anthropic API.
This test boots the Guidewire server with ``--backend auto`` and replays
a multi-turn agent interaction:

1. ``desktop.list_windows`` — discover the Nautilus window
2. ``desktop.snapshot`` — capture the Nautilus accessibility tree
3. ``desktop.find`` — locate the path bar element
4. ``desktop.get_text`` — read the current path from the path bar

Tests are gated by:
- ``@pytest.mark.integration`` (requires ``GUIDEWARE_RUN_INTEGRATION=1``)
- ``pytest.mark.skipif`` on non-Linux platforms
"""

import sys

import pytest

from tests.harness.agent import AgentClient
from tests.harness.assertions import (
    assert_call_order,
    assert_tool_called,
    assert_tool_not_called,
)
from tests.harness.server import GuidewireServerProcess

skip_not_linux = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Linux agent test requires Linux platform (AT-SPI2 backend)",
)

# -- Replay script: simulated Claude interaction with Nautilus ----------
# This replay script models the full agent loop that Claude would execute
# when asked to "open Files and read the current path". Each frame is
# one LLM response containing tool_use blocks.

NAUTILUS_AGENT_REPLAY = [
    # Turn 1: List windows to find Nautilus (Files)
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "tool_use",
                "id": "tu_001",
                "name": "desktop.list_windows",
                "input": {},
            },
        ],
    },
    # Turn 2: Snapshot the Nautilus window to understand its structure
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see Nautilus (Files) is running. Let me take a snapshot.",
            },
            {
                "type": "tool_use",
                "id": "tu_002",
                "name": "desktop.snapshot",
                "input": {"window_ref": "w1"},
            },
        ],
    },
    # Turn 3: Find the path bar element
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see the Nautilus window with a menu bar, toolbar, "
                "sidebar with location bookmarks, and a content grid. "
                "Let me find the path bar to read the current location.",
            },
            {
                "type": "tool_use",
                "id": "tu_003",
                "name": "desktop.find",
                "input": {"window_ref": "w1", "name": "Home"},
            },
        ],
    },
    # Turn 4: Read the current path from the path bar
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Found the path bar. Let me read its value.",
            },
            {
                "type": "tool_use",
                "id": "tu_004",
                "name": "desktop.get_text",
                "input": {"element_ref": "e1"},
            },
        ],
    },
    # Turn 5: Final summary
    {
        "stop_reason": "end_turn",
        "content_blocks": [
            {
                "type": "text",
                "text": "Successfully opened Nautilus and read the current path from the path bar.",
            },
        ],
    },
]


@skip_not_linux
@pytest.mark.integration
class TestLinuxAgentNautilus:
    """GW-042: Prompt-driven agent integration test for Nautilus (Files).

    Validates the full path from MCP tool dispatch through the AT-SPI2
    backend by simulating a Claude agent interaction with the Nautilus
    file manager.
    """

    async def test_agent_reads_nautilus_path(self) -> None:
        """Agent replay should call list_windows, snapshot, find, get_text."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=NAUTILUS_AGENT_REPLAY, max_turns=5)
            result = await agent.send_prompt("Open Files and read the current path.")

        # Verify all 4 tool calls were made
        assert_tool_called(result, "desktop.list_windows", count=1)
        assert_tool_called(result, "desktop.snapshot", count=1)
        assert_tool_called(result, "desktop.find", count=1)
        assert_tool_called(result, "desktop.get_text", count=1)

        # Verify the call order matches the expected agent workflow
        assert_call_order(
            result,
            [
                "desktop.list_windows",
                "desktop.snapshot",
                "desktop.find",
                "desktop.get_text",
            ],
        )

        # Verify the agent reached end_turn
        assert result.stop_reason == "end_turn"

    async def test_find_targets_path_bar(self) -> None:
        """The find call should target the path bar by name."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=NAUTILUS_AGENT_REPLAY, max_turns=5)
            result = await agent.send_prompt("Open Files and read the current path.")

        find_calls = assert_tool_called(result, "desktop.find")
        assert len(find_calls) == 1
        assert find_calls[0].input["name"] == "Home"
        assert find_calls[0].input["window_ref"] == "w1"

    async def test_get_text_references_found_element(self) -> None:
        """The get_text call should reference the element found by find."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=NAUTILUS_AGENT_REPLAY, max_turns=5)
            result = await agent.send_prompt("Open Files and read the current path.")

        get_text_calls = assert_tool_called(result, "desktop.get_text")
        assert len(get_text_calls) == 1
        assert get_text_calls[0].input["element_ref"] == "e1"

    async def test_total_tool_call_count(self) -> None:
        """Exactly 4 tool calls should be made across the full agent loop."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=NAUTILUS_AGENT_REPLAY, max_turns=5)
            result = await agent.send_prompt("Open Files and read the current path.")

        assert len(result.tool_calls) == 4

    async def test_no_type_text_or_click_used(self) -> None:
        """Read-only path workflow should not need type_text or click tools."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=NAUTILUS_AGENT_REPLAY, max_turns=5)
            result = await agent.send_prompt("Open Files and read the current path.")

        assert_tool_not_called(result, "desktop.type_text")
        assert_tool_not_called(result, "desktop.click")
        assert_tool_not_called(result, "desktop.press_key")
