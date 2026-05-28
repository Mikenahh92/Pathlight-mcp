"""Windows agent integration test — cross-app workflow (GW-039).

Prompt-driven agent integration test validating the full Windows stack
(MCP → tool dispatch → UIA backend) by simulating Claude reading a
value from Calculator, switching to Notepad, and typing the value
into the text editor.

This is the Windows MVP proof point: a multi-turn agent interaction that
exercises semantic accessibility actions across two distinct applications,
demonstrating window switching via ``desktop.focus_window``.

Uses the AgentClient replay_script mode to bypass the real Anthropic API.
This test boots the Pathlight MCP server with ``--backend auto`` and replays
a multi-turn agent interaction:

1. ``desktop.list_windows`` — discover all windows (Calculator + Notepad)
2. ``desktop.snapshot`` — capture the Calculator accessibility tree
3. ``desktop.find`` — locate the calculator display element
4. ``desktop.get_text`` — read the displayed value
5. ``desktop.focus_window`` — switch focus to Notepad
6. ``desktop.snapshot`` — capture the Notepad accessibility tree
7. ``desktop.find`` — locate the text editor element
8. ``desktop.type_text`` — type the calculator value into Notepad
9. ``desktop.get_text`` — read back to verify

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
from tests.harness.server import PathlightMCPServerProcess

skip_not_windows = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows agent test requires Windows platform (UIA backend)",
)

# -- Replay script: simulated Claude cross-app interaction -----------------
# This replay script models the full agent loop that Claude would execute
# when asked to "read the value from Calculator and type it into Notepad".
# Each frame is one LLM response containing tool_use blocks.

CROSS_APP_AGENT_REPLAY = [
    # Turn 1: List windows to discover running applications
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Let me start by discovering what windows are open.",
            },
            {
                "type": "tool_use",
                "id": "tu_001",
                "name": "desktop.list_windows",
                "input": {},
            },
        ],
    },
    # Turn 2: Snapshot Calculator to understand its structure
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see Calculator and Notepad are running. "
                "Let me snapshot the Calculator first.",
            },
            {
                "type": "tool_use",
                "id": "tu_002",
                "name": "desktop.snapshot",
                "input": {"window_ref": "w1"},
            },
        ],
    },
    # Turn 3: Find the calculator display element
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see the Calculator window structure. "
                "Let me find the display that shows the current value.",
            },
            {
                "type": "tool_use",
                "id": "tu_003",
                "name": "desktop.find",
                "input": {"window_ref": "w1", "role": "text"},
            },
        ],
    },
    # Turn 4: Read the value from the calculator display
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Found the calculator display. Let me read its value.",
            },
            {
                "type": "tool_use",
                "id": "tu_004",
                "name": "desktop.get_text",
                "input": {"element_ref": "e1"},
            },
        ],
    },
    # Turn 5: Switch focus to Notepad
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "The calculator shows '42'. Now I need to switch to Notepad "
                "and type that value.",
            },
            {
                "type": "tool_use",
                "id": "tu_005",
                "name": "desktop.focus_window",
                "input": {"window_ref": "w2"},
            },
        ],
    },
    # Turn 6: Snapshot Notepad to understand its structure
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Switched to Notepad. Let me snapshot it to find the text editor.",
            },
            {
                "type": "tool_use",
                "id": "tu_006",
                "name": "desktop.snapshot",
                "input": {"window_ref": "w2"},
            },
        ],
    },
    # Turn 7: Find the text editor element in Notepad
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see the Notepad window structure. Let me find the text editor.",
            },
            {
                "type": "tool_use",
                "id": "tu_007",
                "name": "desktop.find",
                "input": {"window_ref": "w2", "role": "text_input"},
            },
        ],
    },
    # Turn 8: Type the calculator value into Notepad
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Found the text editor. Now typing the value '42' from the calculator.",
            },
            {
                "type": "tool_use",
                "id": "tu_008",
                "name": "desktop.type_text",
                "input": {"element_ref": "e2", "text": "42"},
            },
        ],
    },
    # Turn 9: Read the text back from Notepad to verify
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Typed '42' into Notepad. Let me read it back to verify.",
            },
            {
                "type": "tool_use",
                "id": "tu_009",
                "name": "desktop.get_text",
                "input": {"element_ref": "e2"},
            },
        ],
    },
    # Turn 10: Final summary
    {
        "stop_reason": "end_turn",
        "content_blocks": [
            {
                "type": "text",
                "text": "Successfully read the value '42' from Calculator, "
                "switched to Notepad, typed the value into the text editor, "
                "and verified it was entered correctly.",
            }
        ],
    },
]


@skip_not_windows
@pytest.mark.integration
class TestWindowsAgentCrossApp:
    """GW-039: Prompt-driven agent integration test for cross-app Windows workflow.

    Validates the full path from MCP tool dispatch through the Windows
    UI Automation backend by simulating a Claude agent interaction that
    reads a value from Calculator and types it into Notepad.
    """

    async def test_cross_app_workflow_reads_calculator_types_notepad(self) -> None:
        """Agent replay should execute the full cross-app workflow."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=CROSS_APP_AGENT_REPLAY, max_turns=10)
            result = await agent.send_prompt(
                "Read the value from Calculator and type it into Notepad."
            )

        # Verify all 9 tool calls were made
        assert_tool_called(result, "desktop.list_windows", count=1)
        assert_tool_called(result, "desktop.snapshot", count=2)
        assert_tool_called(result, "desktop.find", count=2)
        assert_tool_called(result, "desktop.get_text", count=2)
        assert_tool_called(result, "desktop.focus_window", count=1)
        assert_tool_called(result, "desktop.type_text", count=1)

        # Verify the call order matches the cross-app workflow
        assert_call_order(
            result,
            [
                "desktop.list_windows",
                "desktop.snapshot",
                "desktop.find",
                "desktop.get_text",
                "desktop.focus_window",
                "desktop.snapshot",
                "desktop.find",
                "desktop.type_text",
                "desktop.get_text",
            ],
        )

        # Verify the agent reached end_turn
        assert result.stop_reason == "end_turn"

    async def test_focus_window_switches_to_notepad(self) -> None:
        """The focus_window call should target the Notepad window."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=CROSS_APP_AGENT_REPLAY, max_turns=10)
            result = await agent.send_prompt(
                "Read the value from Calculator and type it into Notepad."
            )

        focus_calls = assert_tool_called(result, "desktop.focus_window")
        assert len(focus_calls) == 1
        assert focus_calls[0].input["window_ref"] == "w2"

    async def test_calculator_value_read_before_type(self) -> None:
        """get_text on calculator (e1) should precede type_text on Notepad (e2)."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=CROSS_APP_AGENT_REPLAY, max_turns=10)
            result = await agent.send_prompt(
                "Read the value from Calculator and type it into Notepad."
            )

        # First get_text reads calculator (e1), second reads Notepad (e2)
        get_text_calls = assert_tool_called(result, "desktop.get_text")
        assert len(get_text_calls) == 2
        assert get_text_calls[0].input["element_ref"] == "e1"
        assert get_text_calls[1].input["element_ref"] == "e2"

        # type_text targets Notepad (e2)
        type_text_calls = assert_tool_called(result, "desktop.type_text")
        assert len(type_text_calls) == 1
        assert type_text_calls[0].input["element_ref"] == "e2"
        assert type_text_calls[0].input["text"] == "42"

    async def test_total_tool_call_count(self) -> None:
        """Exactly 9 tool calls across the full cross-app agent loop."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=CROSS_APP_AGENT_REPLAY, max_turns=10)
            result = await agent.send_prompt(
                "Read the value from Calculator and type it into Notepad."
            )

        assert len(result.tool_calls) == 9

    async def test_snapshots_target_different_windows(self) -> None:
        """Two snapshots should target different windows (Calculator then Notepad)."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=CROSS_APP_AGENT_REPLAY, max_turns=10)
            result = await agent.send_prompt(
                "Read the value from Calculator and type it into Notepad."
            )

        snapshot_calls = assert_tool_called(result, "desktop.snapshot")
        assert len(snapshot_calls) == 2
        assert snapshot_calls[0].input["window_ref"] == "w1"
        assert snapshot_calls[1].input["window_ref"] == "w2"

    async def test_finds_target_different_roles_per_app(self) -> None:
        """Two find calls should target different roles per application."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=CROSS_APP_AGENT_REPLAY, max_turns=10)
            result = await agent.send_prompt(
                "Read the value from Calculator and type it into Notepad."
            )

        find_calls = assert_tool_called(result, "desktop.find")
        assert len(find_calls) == 2
        # First find targets calculator display (text role)
        assert find_calls[0].input["window_ref"] == "w1"
        assert find_calls[0].input["role"] == "text"
        # Second find targets Notepad text editor (text_input role)
        assert find_calls[1].input["window_ref"] == "w2"
        assert find_calls[1].input["role"] == "text_input"

    async def test_server_discovers_eight_tools(self) -> None:
        """Server should expose all 8 tools on the Windows backend."""
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

    async def test_tool_schemas_valid_on_windows(self) -> None:
        """Each tool should have a valid JSON Schema input on Windows."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            tools = await server.list_tools()
            for tool in tools:
                schema = tool.inputSchema
                assert schema is not None
                assert schema.get("type") == "object"

    async def test_no_click_or_press_key_used(self) -> None:
        """Cross-app workflow should not need click or press_key tools."""
        async with PathlightMCPServerProcess(backend="auto") as server:
            agent = AgentClient(server, replay_script=CROSS_APP_AGENT_REPLAY, max_turns=10)
            result = await agent.send_prompt(
                "Read the value from Calculator and type it into Notepad."
            )

        assert_tool_not_called(result, "desktop.click")
        assert_tool_not_called(result, "desktop.press_key")
