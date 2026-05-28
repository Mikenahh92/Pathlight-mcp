"""Server subprocess integration tests (GW-037).

Validates that the Pathlight MCP server can be booted as a subprocess,
tools are discoverable, and tool calls work end-to-end.

These tests do NOT require an Anthropic API key.
"""

import json

import pytest

from tests.harness.server import PathlightMCPServerProcess


@pytest.mark.integration
class TestServerProcess:
    """Tests for PathlightMCPServerProcess lifecycle."""

    async def test_server_starts_and_stops(self) -> None:
        """Server subprocess should start, initialize, and shut down cleanly."""
        async with PathlightMCPServerProcess() as server:
            assert server.session is not None

    async def test_server_lists_all_tools(self) -> None:
        """Server should expose all canonical desktop tools."""
        async with PathlightMCPServerProcess() as server:
            tools = await server.list_tools()
            names = {t.name for t in tools}
            expected = {
                "desktop.list_windows",
                "desktop.focus_window",
                "desktop.manage_window",
                "desktop.snapshot",
                "desktop.find",
                "desktop.click",
                "desktop.type_text",
                "desktop.press_key",
                "desktop.get_text",
                "desktop.get_table_info",
            }
            assert names == expected

    async def test_tool_count_is_correct(self) -> None:
        """Exactly 10 tools should be registered."""
        async with PathlightMCPServerProcess() as server:
            tools = await server.list_tools()
            assert len(tools) == 10

    async def test_tool_schemas_are_valid(self) -> None:
        """Each tool should have a valid input schema."""
        async with PathlightMCPServerProcess() as server:
            tools = await server.list_tools()
            for tool in tools:
                schema = tool.inputSchema
                assert schema is not None
                assert schema.get("type") == "object"

    async def test_call_tool_list_windows(self) -> None:
        """Calling desktop.list_windows should return a result."""
        async with PathlightMCPServerProcess() as server:
            result = await server.call_tool("desktop.list_windows")
            assert len(result.content) >= 1
            text = result.content[0].text
            data = json.loads(text)
            assert isinstance(data, dict)
            assert "windows" in data

    async def test_call_tool_snapshot_returns_tree(self) -> None:
        """Calling desktop.snapshot should return a tree structure."""
        async with PathlightMCPServerProcess() as server:
            result = await server.call_tool(
                "desktop.snapshot",
                arguments={"window_ref": "w1"},
            )
            data = json.loads(result.content[0].text)
            assert "ref" in data
            assert "children" in data

    async def test_server_not_accessible_outside_context(self) -> None:
        """Accessing session outside the async context should raise."""
        server = PathlightMCPServerProcess()
        with pytest.raises(RuntimeError, match="not running"):
            _ = server.session
