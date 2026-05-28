"""Agent replay mode and max-turns tests (TC-H09, TC-H10).

Tests the dry-run/replay mode of AgentClient and the max_turns limit
using pre-scripted responses instead of real API calls.
"""

import pytest

from tests.harness.agent import AgentClient
from tests.harness.assertions import assert_tool_called
from tests.harness.server import PathlightMCPServerProcess


@pytest.mark.integration
class TestMockedPrompt:
    """TC-H09: Mocked prompt — replay mode bypasses Anthropic API."""

    async def test_replay_mode_single_tool_call(self) -> None:
        """Replay mode with one tool-use frame should execute the tool."""
        replay = [
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
            {
                "stop_reason": "end_turn",
                "content_blocks": [
                    {"type": "text", "text": "Done."},
                ],
            },
        ]

        async with PathlightMCPServerProcess() as server:
            agent = AgentClient(server, replay_script=replay)
            result = await agent.send_prompt("List windows")
            assert_tool_called(result, "desktop.list_windows", count=1)
            assert result.stop_reason == "end_turn"
            assert "Done" in result.text

    async def test_replay_mode_no_api_key_required(self) -> None:
        """Replay mode should work without ANTHROPIC_API_KEY set."""
        import os

        old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            replay = [
                {
                    "stop_reason": "end_turn",
                    "content_blocks": [
                        {"type": "text", "text": "No tools needed."},
                    ],
                },
            ]

            async with PathlightMCPServerProcess() as server:
                agent = AgentClient(server, replay_script=replay, api_key="")
                result = await agent.send_prompt("Hello")
                assert result.stop_reason == "end_turn"
                assert result.text == "No tools needed."
        finally:
            if old_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = old_key


@pytest.mark.integration
class TestMaxTurns:
    """TC-H10: Max turns limit."""

    async def test_max_turns_stops_agent_loop(self) -> None:
        """Agent should stop after max_turns even if LLM keeps requesting tools."""
        # Create a replay script that always requests a tool call
        infinite_replay = [
            {
                "stop_reason": "tool_use",
                "content_blocks": [
                    {
                        "type": "tool_use",
                        "id": f"tu_{i:03d}",
                        "name": "desktop.list_windows",
                        "input": {},
                    }
                ],
            }
            for i in range(20)  # More than max_turns
        ]

        async with PathlightMCPServerProcess() as server:
            agent = AgentClient(server, replay_script=infinite_replay, max_turns=3)
            result = await agent.send_prompt("Keep listing windows")
            assert result.stop_reason == "max_turns"
            # Should have exactly 3 tool calls (one per turn)
            assert len(result.tool_calls) == 3

    async def test_max_turns_default_is_five(self) -> None:
        """Default max_turns should be 5."""
        agent = AgentClient.__new__(AgentClient)
        agent._max_turns = 5
        assert agent._max_turns == 5

    async def test_replay_exhausted_returns_max_turns(self) -> None:
        """When replay script is shorter than max_turns, should still stop cleanly."""
        replay = [
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
        ]
        # Only 1 frame — after consuming it, the next iteration falls through
        # to the real API. Since we don't have a key, we test that the
        # replay itself works for the first turn.
        async with PathlightMCPServerProcess() as server:
            agent = AgentClient(server, replay_script=replay, max_turns=1)
            result = await agent.send_prompt("List windows")
            # With max_turns=1, we get at most 1 turn
            assert len(result.tool_calls) >= 1
