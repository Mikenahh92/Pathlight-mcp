"""E2E smoke test — boots server, mocks Anthropic API, verifies tool call (TC-H20).

Uses the AgentClient replay_script mode to bypass the real Anthropic API.
This test boots the Pathlight MCP server, creates an AgentClient with a
pre-scripted response that calls desktop.list_windows, sends a prompt,
and verifies the tool was called via the MCP server.
"""

import pytest

from tests.harness.agent import AgentClient
from tests.harness.assertions import assert_tool_called
from tests.harness.server import PathlightMCPServerProcess


@pytest.mark.integration
class TestE2ESmoke:
    """E2E smoke test: full round-trip with mocked Anthropic response."""

    async def test_mocked_prompt_calls_list_windows(self) -> None:
        """AgentClient with replay_script should call desktop.list_windows."""
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
                    {
                        "type": "text",
                        "text": "Found 0 windows.",
                    }
                ],
            },
        ]

        async with PathlightMCPServerProcess() as server:
            agent = AgentClient(server, replay_script=replay)
            result = await agent.send_prompt("List all windows")
            assert_tool_called(result, "desktop.list_windows")
            assert result.stop_reason == "end_turn"
