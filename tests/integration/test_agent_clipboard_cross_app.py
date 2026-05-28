"""Agent integration test — clipboard-based cross-app workflow (GW-047).

Prompt-driven agent integration test validating clipboard data transfer
between two applications using the agent-test-harness.  Simulates Claude
reading a value from one application, copying it to the clipboard, switching
to a second application, and pasting the value.

Uses the AgentClient replay_script mode to bypass the real Anthropic API.
This test boots the Pathlight MCP server with ``--backend mock`` and replays a
multi-turn agent interaction:

1. ``desktop.list_windows`` — discover running applications
2. ``desktop.snapshot`` — capture source app accessibility tree
3. ``desktop.find`` — locate the value element in the source app
4. ``desktop.get_text`` — read the value from the source app
5. ``desktop.clipboard_write`` — copy the value to the clipboard
6. ``desktop.focus_window`` — switch focus to the target application
7. ``desktop.snapshot`` — capture target app accessibility tree
8. ``desktop.find`` — locate the text input element in the target app
9. ``desktop.type_text`` — type the value into the target app
10. ``desktop.clipboard_read`` — verify clipboard content

Tests are gated by:
- ``@pytest.mark.integration`` (requires ``GUIDEWARE_RUN_INTEGRATION=1``)
"""

import pytest

from tests.harness.agent import AgentClient
from tests.harness.assertions import (
    assert_call_order,
    assert_tool_called,
    assert_tool_not_called,
)
from tests.harness.server import PathlightMCPServerProcess

# -- Replay script: simulated Claude clipboard cross-app interaction --------
# This replay script models the full agent loop that Claude would execute
# when asked to "read a value from the Calculator and paste it into the
# TextEditor using the clipboard".  Each frame is one LLM response
# containing tool_use blocks.

CLIPBOARD_CROSS_APP_REPLAY = [
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
    # Turn 2: Snapshot source app to find the value
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see Calculator and TextEditor are running. "
                "Let me snapshot the Calculator to find the display value.",
            },
            {
                "type": "tool_use",
                "id": "tu_002",
                "name": "desktop.snapshot",
                "input": {"window_ref": "w1"},
            },
        ],
    },
    # Turn 3: Find the value element in source app
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see the Calculator structure. Let me find the display element.",
            },
            {
                "type": "tool_use",
                "id": "tu_003",
                "name": "desktop.find",
                "input": {"window_ref": "w1", "role": "text"},
            },
        ],
    },
    # Turn 4: Read the value from the source app
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Found the display element. Let me read its value.",
            },
            {
                "type": "tool_use",
                "id": "tu_004",
                "name": "desktop.get_text",
                "input": {"element_ref": "e1"},
            },
        ],
    },
    # Turn 5: Write the value to the clipboard
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "The calculator shows '42'. Let me copy it to the clipboard.",
            },
            {
                "type": "tool_use",
                "id": "tu_005",
                "name": "desktop.clipboard_write",
                "input": {"text": "42"},
            },
        ],
    },
    # Turn 6: Switch focus to the target application
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Copied '42' to clipboard. Now switching to the TextEditor.",
            },
            {
                "type": "tool_use",
                "id": "tu_006",
                "name": "desktop.focus_window",
                "input": {"window_ref": "w2"},
            },
        ],
    },
    # Turn 7: Snapshot the target app
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Switched to TextEditor. Let me snapshot it to find the editor.",
            },
            {
                "type": "tool_use",
                "id": "tu_007",
                "name": "desktop.snapshot",
                "input": {"window_ref": "w2"},
            },
        ],
    },
    # Turn 8: Find the text input in the target app
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "I can see the TextEditor structure. Let me find the text input.",
            },
            {
                "type": "tool_use",
                "id": "tu_008",
                "name": "desktop.find",
                "input": {"window_ref": "w2", "role": "text_input"},
            },
        ],
    },
    # Turn 9: Type the value into the target app
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Found the text input. Now typing '42' from the clipboard.",
            },
            {
                "type": "tool_use",
                "id": "tu_009",
                "name": "desktop.type_text",
                "input": {"element_ref": "e2", "text": "42"},
            },
        ],
    },
    # Turn 10: Read clipboard to verify content
    {
        "stop_reason": "tool_use",
        "content_blocks": [
            {
                "type": "text",
                "text": "Typed the value. Let me verify the clipboard content.",
            },
            {
                "type": "tool_use",
                "id": "tu_010",
                "name": "desktop.clipboard_read",
                "input": {},
            },
        ],
    },
    # Turn 11: Final summary
    {
        "stop_reason": "end_turn",
        "content_blocks": [
            {
                "type": "text",
                "text": "Successfully read '42' from Calculator, copied it to the "
                "clipboard, switched to TextEditor, typed the value, and "
                "verified the clipboard content is intact.",
            }
        ],
    },
]


@pytest.mark.integration
class TestAgentClipboardCrossApp:
    """GW-047: Agent replay test for clipboard-based cross-app workflow.

    Validates the complete clipboard data transfer pipeline: read value from
    source app → write to clipboard → switch apps → type value → verify
    clipboard content.
    """

    async def test_clipboard_workflow_completes(self) -> None:
        """Agent replay should execute the full clipboard cross-app workflow."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        # Verify all 10 tool calls were made
        assert_tool_called(result, "desktop.list_windows", count=1)
        assert_tool_called(result, "desktop.snapshot", count=2)
        assert_tool_called(result, "desktop.find", count=2)
        assert_tool_called(result, "desktop.get_text", count=1)
        assert_tool_called(result, "desktop.clipboard_write", count=1)
        assert_tool_called(result, "desktop.focus_window", count=1)
        assert_tool_called(result, "desktop.type_text", count=1)
        assert_tool_called(result, "desktop.clipboard_read", count=1)

        # Verify the agent reached end_turn
        assert result.stop_reason == "end_turn"

    async def test_tool_call_order(self) -> None:
        """Tool calls should follow the expected clipboard workflow sequence."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        assert_call_order(
            result,
            [
                "desktop.list_windows",
                "desktop.snapshot",
                "desktop.find",
                "desktop.get_text",
                "desktop.clipboard_write",
                "desktop.focus_window",
                "desktop.snapshot",
                "desktop.find",
                "desktop.type_text",
                "desktop.clipboard_read",
            ],
        )

    async def test_total_tool_call_count(self) -> None:
        """Exactly 10 tool calls across the full clipboard agent loop."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        assert len(result.tool_calls) == 10

    async def test_clipboard_write_before_focus_switch(self) -> None:
        """clipboard_write should be called before focus_window."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        assert_call_order(
            result,
            ["desktop.clipboard_write", "desktop.focus_window"],
        )

    async def test_clipboard_write_content_matches_read_value(
        self,
    ) -> None:
        """clipboard_write should contain the value read from the source app."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        write_calls = assert_tool_called(result, "desktop.clipboard_write")
        assert len(write_calls) == 1
        assert write_calls[0].input["text"] == "42"

    async def test_type_text_targets_second_app_element(self) -> None:
        """type_text should target the text input in the second app."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        type_calls = assert_tool_called(result, "desktop.type_text")
        assert len(type_calls) == 1
        assert type_calls[0].input["element_ref"] == "e2"
        assert type_calls[0].input["text"] == "42"

    async def test_focus_window_switches_to_target(self) -> None:
        """focus_window should target the TextEditor window (w2)."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        focus_calls = assert_tool_called(result, "desktop.focus_window")
        assert len(focus_calls) == 1
        assert focus_calls[0].input["window_ref"] == "w2"

    async def test_snapshots_target_different_windows(self) -> None:
        """Two snapshots should target different windows (Calculator then TextEditor)."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        snapshot_calls = assert_tool_called(result, "desktop.snapshot")
        assert len(snapshot_calls) == 2
        assert snapshot_calls[0].input["window_ref"] == "w1"
        assert snapshot_calls[1].input["window_ref"] == "w2"

    async def test_get_text_before_clipboard_write(self) -> None:
        """get_text on source app should precede clipboard_write."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        assert_call_order(
            result,
            ["desktop.get_text", "desktop.clipboard_write"],
        )

    async def test_clipboard_read_is_final_tool_call(self) -> None:
        """clipboard_read should be the last tool call before end_turn."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        assert_call_order(
            result,
            ["desktop.type_text", "desktop.clipboard_read"],
        )

    async def test_server_exposes_clipboard_tools(self) -> None:
        """Server should expose both clipboard_read and clipboard_write tools."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            tools = await server.list_tools()
            names = {t.name for t in tools}
            assert "desktop.clipboard_read" in names
            assert "desktop.clipboard_write" in names

    async def test_no_unnecessary_tools_used(self) -> None:
        """Clipboard workflow should not need click, press_key, or manage_window."""
        async with PathlightMCPServerProcess(backend="mock") as server:
            agent = AgentClient(server, replay_script=CLIPBOARD_CROSS_APP_REPLAY, max_turns=12)
            result = await agent.send_prompt(
                "Read the value from Calculator and paste it into TextEditor using the clipboard."
            )

        assert_tool_not_called(result, "desktop.click")
        assert_tool_not_called(result, "desktop.press_key")
        assert_tool_not_called(result, "desktop.manage_window")
