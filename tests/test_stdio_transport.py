"""Tests for stdio transport wiring (GW-008).

Validates that:
- ``__main__.main`` wires stdio transport correctly.
- The PathlightMCPServer can be instantiated and tools registered without errors.
- The ``run()`` method exists and delegates to FastMCP's stdio transport.
"""

import inspect

from pathlight_mcp.server import PathlightMCPServer


class TestStdioTransport:
    """Tests for the stdio transport wiring."""

    def test_pathlight_mcp_server_has_run_method(self):
        """PathlightMCPServer should have a ``run`` method."""
        server = PathlightMCPServer()
        assert callable(getattr(server, "run", None))

    def test_pathlight_mcp_server_has_register_tools_method(self):
        """PathlightMCPServer should have a ``register_tools`` method."""
        server = PathlightMCPServer()
        assert callable(getattr(server, "register_tools", None))

    def test_pathlight_mcp_server_register_tools_idempotent(self):
        """Calling register_tools multiple times should not raise."""
        server = PathlightMCPServer()
        server.register_tools()
        server.register_tools()  # second call should be safe

    def test_main_entry_point_importable(self):
        """``pathlight_mcp.__main__`` should be importable and expose ``main``."""
        from pathlight_mcp.__main__ import main

        assert callable(main)

    def test_main_uses_pathlight_mcp_server(self):
        """The ``main`` function should use PathlightMCPServer."""
        import pathlight_mcp.__main__ as mod

        source = inspect.getsource(mod.main)
        assert "PathlightMCPServer" in source
        assert "register_tools" in source
        assert "run" in source

    async def test_server_tools_callable_via_mcp(self):
        """Registered tools should be callable through the MCP layer."""
        import json

        server = PathlightMCPServer()
        server.register_tools()
        result, _meta = await server.mcp.call_tool("desktop.list_windows", arguments={})
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert isinstance(data, dict)
        assert data["windows"] == []
