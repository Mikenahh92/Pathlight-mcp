"""Tests for guidewire.tools registry (GW-008).

Validates that:
- All tool sub-modules exist and export a ``register`` function.
- ``register_all`` registers every tool on a FastMCP instance.
- No ``from __future__ import annotations`` is used anywhere in the tools package.
"""

import importlib

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.tools import _TOOL_MODULES, register_all

EXPECTED_TOOL_NAMES = [
    "desktop.list_windows",
    "desktop.focus_window",
    "desktop.manage_window",
    "desktop.snapshot",
    "desktop.find",
    "desktop.click",
    "desktop.type_text",
    "desktop.press_key",
    "desktop.get_text",
    "desktop.get_tree_info",
    "desktop.clipboard_read",
    "desktop.clipboard_write",
    "desktop.get_table_info",
    "desktop.scroll_to_item",
    "desktop.multi_action",
]


class TestToolModuleRegistry:
    """Tests for the per-module register(mcp) pattern."""

    @pytest.mark.parametrize("module_name", _TOOL_MODULES)
    def test_module_has_register_function(self, module_name):
        """Each tool module should export a callable ``register``."""
        mod = importlib.import_module(module_name, package="guidewire.tools")
        assert callable(getattr(mod, "register", None)), (
            f"{module_name} is missing a 'register' function"
        )

    async def test_register_all_registers_all_tools(self):
        """register_all should register every expected tool on a FastMCP instance."""
        mcp = FastMCP(name="test")
        register_all(mcp)
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == set(EXPECTED_TOOL_NAMES)

    def test_tool_module_count(self):
        """There should be exactly the expected number of tool modules."""
        assert len(_TOOL_MODULES) == len(EXPECTED_TOOL_NAMES)


class TestNoFutureAnnotations:
    """D-4: No tool file should use ``from __future__ import annotations``."""

    @pytest.mark.parametrize("module_name", _TOOL_MODULES)
    def test_no_future_annotations_in_tool_module(self, module_name):
        """Tool modules must not contain ``from __future__ import annotations``."""
        source = importlib.import_module(module_name, package="guidewire.tools")
        filepath = source.__file__
        assert filepath is not None
        with open(filepath) as f:
            content = f.read()
        assert "from __future__ import annotations" not in content, (
            f"{module_name} uses 'from __future__ import annotations'"
        )

    def test_no_future_annotations_in_tools_init(self):
        """tools/__init__.py must not use ``from __future__ import annotations``."""
        from guidewire.tools import __file__ as init_path

        with open(init_path) as f:
            content = f.read()
        assert "from __future__ import annotations" not in content
