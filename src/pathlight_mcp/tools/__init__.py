"""Tool handlers for the Pathlight MCP server.

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
    desktop.wait_for       — async polling-based condition blocking
    desktop.web_connect    — connect to a browser's CDP debug port (GW-098)
    desktop.web_navigate   — navigate a browser page to a URL (GW-098)
    desktop.web_evaluate   — execute JavaScript in a browser page context (GW-099)
    desktop.web_click      — click a web page element by CSS selector (GW-122)
    desktop.web_type       — type text into a web page element by CSS selector (GW-122)
    desktop.web_hover      — hover over a web page element by CSS selector (GW-122)
    desktop.web_select_option — select an option in a web dropdown (GW-123)
    desktop.web_upload_files  — upload files to a web file input element (GW-123)
    desktop.web_list_tabs  — list browser tabs via CDP Target domain (GW-124)
    desktop.web_tab_action — perform actions on browser tabs (GW-124)
    desktop.web_frame_tree — inspect iframe hierarchy (GW-124)
    desktop.web_wait_for   — async auto-wait for web page conditions (GW-125)
    desktop.web_screenshot — capture web page screenshots (GW-125)
    desktop.click_xy     — click at absolute screen coordinates (GW-151)
    desktop.mouse_move   — move cursor to absolute screen coordinates (GW-151)
    desktop.screenshot   — capture a screenshot of a native window (GW-149)
"""

import importlib
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.refs import ElementRefStore

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend

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
    ".launch_app",
    ".scroll_to_item",
    ".multi_action",
    ".wait_for",
    ".web_connect",
    ".web_navigate",
    ".web_evaluate",
    ".web_click",
    ".web_type",
    ".web_hover",
    ".web_select_option",
    ".web_upload_files",
    ".web_list_tabs",
    ".web_tab_action",
    ".web_frame_tree",
    ".web_wait_for",
    ".web_screenshot",
    ".click_xy",
    ".mouse_move",
    ".screenshot",
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
        ".launch_app",
        ".scroll_to_item",
        ".multi_action",
        ".wait_for",
        ".click_xy",
        ".mouse_move",
        ".screenshot",
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
        mod = importlib.import_module(module_name, package="pathlight_mcp.tools")
        mod.register(mcp, **deps)
