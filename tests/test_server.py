"""Tests for the Guidewire MCP server (GW-008).

Validates that:
- The GuidewireServer can be created without errors.
- All tool stubs are registered and discoverable via list_tools.
- Each tool has the correct name, description, and input schema.
- The server metadata (name, instructions) is correct.
- Tool stubs return static placeholder responses when invoked.
"""

import pytest

from guidewire.server import GuidewireServer


@pytest.fixture()
def server():
    """Return a fresh Guidewire MCP server instance with tools registered."""
    srv = GuidewireServer()
    srv.register_tools()
    return srv


# -- Server creation ---------------------------------------------------------


class TestServerCreation:
    """Tests for server instantiation."""

    async def test_server_creates_without_error(self, server):
        """GuidewireServer() should return a GuidewireServer instance."""
        assert isinstance(server, GuidewireServer)

    async def test_server_has_fastmcp(self, server):
        """Server should expose a FastMCP instance."""
        from mcp.server.fastmcp import FastMCP

        assert isinstance(server.mcp, FastMCP)

    async def test_server_name(self, server):
        """Server name should be 'guidewire'."""
        assert server.mcp.name == "guidewire"

    async def test_server_has_instructions(self, server):
        """Server should have non-empty instructions."""
        assert server.mcp.instructions is not None
        assert len(server.mcp.instructions) > 0
        assert "accessibility" in server.mcp.instructions.lower()


# -- Tool registration -------------------------------------------------------


EXPECTED_TOOLS = [
    {
        "name": "desktop.list_windows",
        "description_pattern": "List all visible top-level desktop windows",
        "required_params": [],
        "optional_params": [],
    },
    {
        "name": "desktop.focus_window",
        "description_pattern": "Bring a window to the foreground",
        "required_params": ["window_ref"],
        "optional_params": [],
    },
    {
        "name": "desktop.manage_window",
        "description_pattern": "window state",
        "required_params": ["window_ref", "action"],
        "optional_params": ["x", "y", "width", "height"],
    },
    {
        "name": "desktop.snapshot",
        "description_pattern": "accessibility snapshot",
        "required_params": ["window_ref"],
        "optional_params": ["max_depth", "max_nodes"],
    },
    {
        "name": "desktop.find",
        "description_pattern": "Find accessibility elements",
        "required_params": ["window_ref"],
        "optional_params": ["role", "name"],
    },
    {
        "name": "desktop.click",
        "description_pattern": "Click a desktop element",
        "required_params": ["element_ref"],
        "optional_params": [],
    },
    {
        "name": "desktop.type_text",
        "description_pattern": "Type text into a desktop element",
        "required_params": ["element_ref", "text"],
        "optional_params": [],
    },
    {
        "name": "desktop.press_key",
        "description_pattern": "Press a keyboard key",
        "required_params": ["keys"],
        "optional_params": [],
    },
    {
        "name": "desktop.get_text",
        "description_pattern": "Get the text value",
        "required_params": ["element_ref"],
        "optional_params": [],
    },
    {
        "name": "desktop.get_tree_info",
        "description_pattern": "tree",
        "required_params": ["element_ref"],
        "optional_params": [],
    },
    {
        "name": "desktop.clipboard_read",
        "description_pattern": "clipboard",
        "required_params": [],
        "optional_params": [],
    },
    {
        "name": "desktop.clipboard_write",
        "description_pattern": "Write text to the system clipboard",
        "required_params": ["text"],
        "optional_params": [],
    },
    {
        "name": "desktop.get_table_info",
        "description_pattern": "table/grid data",
        "required_params": ["element_ref"],
        "optional_params": ["max_rows", "max_columns"],
    },
    {
        "name": "desktop.launch_app",
        "description_pattern": "Launch a desktop application",
        "required_params": ["app"],
        "optional_params": ["args", "timeout"],
    },
    {
        "name": "desktop.scroll_to_item",
        "description_pattern": "virtualized list",
        "required_params": ["container_ref"],
        "optional_params": ["item_name", "item_index", "max_retries"],
    },
    {
        "name": "desktop.multi_action",
        "description_pattern": "batch",
        "required_params": ["actions"],
        "optional_params": [],
    },
    {
        "name": "desktop.wait_for",
        "description_pattern": "wait until a condition",
        "required_params": ["condition"],
        "optional_params": ["timeout_ms", "poll_interval_ms"],
    },
]


class TestToolRegistration:
    """Tests for tool stub registration and discovery."""

    async def test_all_nine_tools_registered(self, server):
        """All canonical tool stubs should be discoverable via list_tools."""
        tools = await server.mcp.list_tools()
        names = {t.name for t in tools}
        expected = {spec["name"] for spec in EXPECTED_TOOLS}
        assert names == expected

    async def test_tool_count(self, server):
        """Exactly the expected number of tools should be registered."""
        tools = await server.mcp.list_tools()
        assert len(tools) == len(EXPECTED_TOOLS)

    @pytest.mark.parametrize("spec", EXPECTED_TOOLS, ids=lambda s: s["name"])
    async def test_tool_has_description(self, server, spec):
        """Each tool should have a non-empty description."""
        tools = await server.mcp.list_tools()
        tool_map = {t.name: t for t in tools}
        tool = tool_map[spec["name"]]
        assert tool.description is not None
        assert len(tool.description) > 0
        assert spec["description_pattern"].lower() in tool.description.lower()

    @pytest.mark.parametrize("spec", EXPECTED_TOOLS, ids=lambda s: s["name"])
    async def test_tool_has_input_schema(self, server, spec):
        """Each tool should have a valid JSON Schema input schema."""
        tools = await server.mcp.list_tools()
        tool_map = {t.name: t for t in tools}
        tool = tool_map[spec["name"]]

        schema = tool.inputSchema
        assert schema["type"] == "object"
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        for param in spec["required_params"]:
            assert param in properties, f"Missing required param '{param}'"
            assert param in required, f"Param '{param}' not in required list"

        for param in spec["optional_params"]:
            assert param in properties, f"Missing optional param '{param}'"
            assert param not in required, f"Param '{param}' should be optional"

    @pytest.mark.parametrize("spec", EXPECTED_TOOLS, ids=lambda s: s["name"])
    async def test_tool_schema_has_title(self, server, spec):
        """Each tool's input schema should have a title matching the tool name."""
        tools = await server.mcp.list_tools()
        tool_map = {t.name: t for t in tools}
        tool = tool_map[spec["name"]]
        # Title uses the last segment of the dotted name
        short_name = spec["name"].split(".")[-1]
        assert tool.inputSchema.get("title") == f"{short_name}Arguments"


# -- Tool stub behaviour (static placeholders) --------------------------------


class TestToolStubBehaviour:
    """Tests for tool stub invocation — stubs return static placeholder responses."""

    async def test_list_windows_returns_empty_array(self, server):
        """desktop.list_windows should return wrapped dict with empty windows."""
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        import json

        data = json.loads(result[0].text)
        assert isinstance(data, dict)
        assert data["windows"] == []
        assert data["count"] == 0

    async def test_focus_window_returns_confirmation(self, server):
        """desktop.focus_window should return a confirmation message."""
        result, _meta = await server.mcp.call_tool(
            "desktop.focus_window", arguments={"window_ref": "w1"}
        )
        assert "w1" in result[0].text
        assert "Focused" in result[0].text

    async def test_snapshot_returns_json_tree(self, server):
        """desktop.snapshot should return a JSON tree string."""
        result, _meta = await server.mcp.call_tool(
            "desktop.snapshot", arguments={"window_ref": "w1"}
        )
        import json

        tree = json.loads(result[0].text)
        assert "ref" in tree
        assert "children" in tree

    async def test_find_returns_empty_array(self, server):
        """desktop.find should return '[]'."""
        result, _meta = await server.mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        assert result[0].text == "[]"

    async def test_click_returns_confirmation(self, server):
        """desktop.click should return a confirmation message."""
        result, _meta = await server.mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        assert "e1" in result[0].text
        assert "Clicked" in result[0].text

    async def test_type_text_returns_confirmation(self, server):
        """desktop.type_text should return a confirmation message."""
        result, _meta = await server.mcp.call_tool(
            "desktop.type_text", arguments={"element_ref": "e1", "text": "hello"}
        )
        assert "e1" in result[0].text
        assert "hello" in result[0].text

    async def test_press_key_returns_confirmation(self, server):
        """desktop.press_key should return a confirmation message."""
        result, _meta = await server.mcp.call_tool("desktop.press_key", arguments={"keys": "Enter"})
        assert "Enter" in result[0].text
        assert "Pressed" in result[0].text

    async def test_get_text_returns_stub_message(self, server):
        """desktop.get_text should return a stub message when no backend is wired."""
        result, _meta = await server.mcp.call_tool(
            "desktop.get_text", arguments={"element_ref": "e1"}
        )
        assert "e1" in result[0].text
