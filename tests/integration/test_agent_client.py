"""Agent client integration tests (GW-037).

Validates that the AgentClient can connect to the MCP server, discover
tools, and convert them to Anthropic format.

These tests do NOT require an Anthropic API key.
"""

import pytest

from tests.harness.agent import AgentClient
from tests.harness.server import PathlightMCPServerProcess


@pytest.mark.integration
class TestAgentClient:
    """Tests for AgentClient tool schema conversion and recording."""

    async def test_available_tools_after_init(self) -> None:
        """AgentClient should discover all 8 tools from the server."""
        async with PathlightMCPServerProcess() as server:
            agent = AgentClient(server)
            await agent._ensure_client()
            assert len(agent.available_tools) == 8
            assert "desktop.list_windows" in agent.available_tools

    async def test_anthropic_tool_format(self) -> None:
        """Converted tools should have Anthropic-compatible schema."""
        async with PathlightMCPServerProcess() as server:
            agent = AgentClient(server)
            await agent._ensure_client()
            tools = agent._to_anthropic_tools()
            assert len(tools) == 8
            for tool in tools:
                assert "name" in tool
                assert "description" in tool
                assert "input_schema" in tool
                assert isinstance(tool["input_schema"], dict)
