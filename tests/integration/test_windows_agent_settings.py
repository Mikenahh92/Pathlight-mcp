"""Windows agent integration test — Windows Settings (GW-042).

Prompt-driven agent integration test validating the full Windows stack
(MCP -> tool dispatch -> UIA backend) by simulating Claude navigating
the Windows Settings application.

This test exercises navigation within a complex, modern Windows app
that uses XAML Islands and has a rich accessibility tree with a
navigation pane, search bar, and scrollable content area.

Uses the AgentClient replay_script mode to bypass the real Anthropic API.
This test boots the Guidewire server with ``--backend auto`` and replays
a multi-turn agent interaction:

1. ``desktop.list_windows`` — discover the Settings window
2. ``desktop.snapshot`` — capture the Settings accessibility tree
3. ``desktop.find`` — locate the search box element
4. ``desktop.type_text`` — type a search query into the search box
5. ``desktop.get_text`` — read the search box content back

Tests are gated by:
- ``@pytest.mark.integration`` (requires ``GUIDEWARE_RUN_INTEGRATION=1``)
- ``pytest.mark.skipif`` on non-Windows platforms
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

skip_not_windows = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows agent test requires Windows platform (UIA backend)",
)

# -- Replay script: simulated Claude interaction with Windows Settings -----
# This replay script models the full agent loop that Claude would execute
# when asked to "open Settings and search for 'Display'". Each frame is
# one LLM response containing tool_use blocks.

SETTINGS_AGENT_REPLAY = [
    # Turn 1: List windows to find Settings
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
    # Turn 2: Snapshot the Settings window to understand its structure
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see Windows Settings is running. Let me take a snapshot.",
            },
            {
                "type": "tool_use",
                "id": "tu_002",
                "name": "desktop.snapshot",
                "input": {"window_ref": "w1"},
            },
        ],
    },
    # Turn 3: Find the search box element within Settings
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see the Settings window with a navigation pane "
                "and content area. Let me find the search box.",
            },
            {
                "type": "tool_use",
                "id": "tu_003",
                "name": "desktop.find",
                "input": {"window_ref": "w1", "role": "text_input"},
            },
        ],
    },
    # Turn 4: Type a search query into the search box
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Found the search box. Now typing 'Display'.",
            },
            {
                "type": "tool_use",
                "id": "tu_004",
                "name": "desktop.type_text",
                "input": {"element_ref": "e1", "text": "Display"},
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
            },
        ],
    },
    # Turn 6: Final summary
    {
        "stop_reason": "end_turn",
        "content_blocks": [
            {
                "type": "text",
                "text": "Successfully opened Windows Settings, found the search box, "
                "typed 'Display' and verified the search query was entered.",
            },
        ],
    },
]


@skip_not_windows
@pytest.mark.integration
class TestWindowsAgentSettings:
    """GW-042: Prompt-driven agent integration test for Windows Settings.

    Validates the full path from MCP tool dispatch through the Windows
    UI Automation backend by simulating a Claude agent interaction
    with the Windows Settings application.
    """

    async def test_agent_searches_settings(self) -> None:
        """Agent replay should call list_windows, snapshot, find, type_text, get_text."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=SETTINGS_AGENT_REPLAY, max_turns=6)
            result = await agent.send_prompt("Open Settings and search for 'Display'.")

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

    async def test_type_text_arguments(self) -> None:
        """The type_text tool call should include the correct text argument."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=SETTINGS_AGENT_REPLAY, max_turns=6)
            result = await agent.send_prompt("Open Settings and search for 'Display'.")

        type_text_calls = assert_tool_called(result, "desktop.type_text")
        assert len(type_text_calls) == 1
        assert type_text_calls[0].input["text"] == "Display"
        assert type_text_calls[0].input["element_ref"] == "e1"

    async def test_get_text_arguments(self) -> None:
        """The get_text tool call should reference the same element as type_text."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=SETTINGS_AGENT_REPLAY, max_turns=6)
            result = await agent.send_prompt("Open Settings and search for 'Display'.")

        get_text_calls = assert_tool_called(result, "desktop.get_text")
        assert len(get_text_calls) == 1
        assert get_text_calls[0].input["element_ref"] == "e1"

    async def test_total_tool_call_count(self) -> None:
        """Exactly 5 tool calls should be made across the full agent loop."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=SETTINGS_AGENT_REPLAY, max_turns=6)
            result = await agent.send_prompt("Open Settings and search for 'Display'.")

        assert len(result.tool_calls) == 5

    async def test_no_click_or_press_key_used(self) -> None:
        """Settings search workflow should not need click or press_key tools."""
        async with GuidewireServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=SETTINGS_AGENT_REPLAY, max_turns=6)
            result = await agent.send_prompt("Open Settings and search for 'Display'.")

        assert_tool_not_called(result, "desktop.click")
        assert_tool_not_called(result, "desktop.press_key")
