"""Tool handlers for the Guidewire MCP server.

Each sub-module provides a ``register(mcp, **deps)`` function that registers
one tool on a :class:`~mcp.server.fastmcp.FastMCP` instance.  Tools that have
been wired to a backend receive a ``backend`` and ``ref_store`` dependency;
unwired tools continue to return static placeholder responses.

Tool set (architecture v2 §3.1):

    desktop.list_windows   — list visible windows
    desktop.focus_window   — bring a window to the foreground
    desktop.manage_window  — window state management (minimize, maximize, restore, move, resize)
    desktop.snapshot       — capture accessibility tree
    desktop.find           — find elements by role/name
    desktop.click          — click an element
    desktop.type_text      — type text into an element
    desktop.press_key      — press a keyboard key
    desktop.get_text       — get element text content
    desktop.get_tree_info  — query tree view structure and expand/collapse state
    desktop.clipboard_read — read text from system clipboard
    desktop.clipboard_write — write text to the system clipboard
    desktop.get_table_info — read table/grid data (dimensions, headers, cells)
    desktop.scroll_to_item — scroll a virtualized list to bring a target item into view
    desktop.multi_action   — execute a batch of desktop actions in a single call
"""

import importlib
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from guidewire.refs import ElementRefStore

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend

__all__ = ["register_all"]

# Each tool lives in its own module; add new tools here.
_TOOL_MODULES = [
    ".list_windows",
    ".focus_window",
    ".manage_window",
    ".snapshot",
    ".find",
    ".click",
    ".type_text",
    ".press_key",
    ".get_text",
    ".get_tree_info",
    ".clipboard_read",
    ".clipboard_write",
    ".get_table_info",
    ".scroll_to_item",
    ".multi_action",
]

# Modules whose ``register()`` accepts an optional backend argument.
_BACKEND_TOOL_MODULES: frozenset[str] = frozenset(
    {
        ".list_windows",
        ".snapshot",
        ".find",
        ".click",
        ".type_text",
        ".press_key",
        ".get_text",
        ".get_tree_info",
        ".clipboard_read",
        ".clipboard_write",
        ".get_table_info",
        ".scroll_to_item",
        ".multi_action",
    }
)


def register_all(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register every tool on *mcp*.

    Args:
        mcp: The FastMCP instance to register tools on.
        backend: Optional platform backend for wired tool handlers.
        ref_store: Optional element reference store for resolving refs.
    """
    deps: dict[str, Any] = {"backend": backend, "ref_store": ref_store}
    for module_name in _TOOL_MODULES:
        mod = importlib.import_module(module_name, package="guidewire.tools")
        mod.register(mcp, **deps)
