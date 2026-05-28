"""Tool usage guide resource.

Registers the ``pathlight-mcp://tool-usage`` resource that provides a comprehensive
guide for using all Pathlight MCP desktop tools, organized by category.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_TOOL_USAGE = """\
# Pathlight MCP Tool Usage Guide

## Quick Start

1. **Discover windows** — call ``desktop.list_windows`` to find available windows
2. **Capture the tree** — call ``desktop.snapshot`` with a window reference
3. **Find elements** — use ``desktop.find`` to locate specific UI elements
4. **Interact** — use ``desktop.click``, ``desktop.type_text``, or other action tools

## Element References

Most tools accept an ``element_ref`` or ``window_ref`` parameter. These are
short identifiers (e.g. ``"e1"``, ``"w1"``) returned by ``desktop.snapshot``
or ``desktop.find``. They are **ephemeral** — they become stale when the
application UI changes. If you get a ``stale_element_reference`` error,
take a new snapshot and retry.

## Window Management

| Tool | Purpose |
|------|---------|
| ``desktop.list_windows`` | List all visible top-level windows |
| ``desktop.focus_window`` | Bring a window to the foreground |
| ``desktop.manage_window`` | Minimize, maximize, restore, move, or resize a window |

### Tips
- Always call ``list_windows`` first to discover window references.
- ``manage_window`` supports actions: ``minimize``, ``maximize``, ``restore``,
  ``move`` (requires ``x``, ``y``), ``resize`` (requires ``width``, ``height``).

## Tree Inspection

| Tool | Purpose |
|------|---------|
| ``desktop.snapshot`` | Capture the full accessibility tree of a window |
| ``desktop.find`` | Find elements by role, name, or other criteria |
| ``desktop.get_text`` | Read the text content of a specific element |
| ``desktop.get_tree_info`` | Query tree view structure (expand/collapse state) |

### Tips
- ``snapshot`` returns a nested tree. Use ``max_depth`` and ``max_nodes`` to
  limit output size for large applications.
- ``find`` supports filtering by ``role`` (e.g. ``"button"``, ``"text_input"``)
  and ``name`` (substring match).
- ``get_tree_info`` is specifically for tree/list views — it returns expansion
  state and child counts.

## Element Interaction

| Tool | Purpose |
|------|---------|
| ``desktop.click`` | Click an element (buttons, links, checkboxes) |
| ``desktop.type_text`` | Type text into an editable element (text inputs, text areas) |
| ``desktop.press_key`` | Press a keyboard key or key combination |
| ``desktop.get_table_info`` | Read table/grid data (dimensions, headers, cells) |
| ``desktop.scroll_to_item`` | Scroll a virtualized list to bring a target into view |

### Tips
- **Click**: Returns a risk level (``read_only``, ``interaction``, or ``sensitive``).
  Elements with ``sensitive`` risk may require user confirmation.
- **Type text**: Clears existing content before typing. For appending, position
  the cursor first.
- **Press key**: Supports key names like ``"Enter"``, ``"Tab"``, ``"Escape"``,
  and combinations like ``"Ctrl+C"``.
- **Get table info**: Supports sub-commands: ``info`` (dimensions+headers),
  ``read_cell``, ``read_row``, ``read_column``.
- **Scroll to item**: For virtualized lists where items are loaded on-demand.
  Specify ``item_name`` or ``item_index`` to scroll to.

## Clipboard

| Tool | Purpose |
|------|---------|
| ``desktop.clipboard_read`` | Read the current text content of the system clipboard |
| ``desktop.clipboard_write`` | Write text to the system clipboard |

### Tips
- Clipboard tools interact with the **system** clipboard, not an application's
  internal clipboard.
- Sensitive content (passwords, tokens) in clipboard data is automatically
  redacted in the response.

## Batch Operations

| Tool | Purpose |
|------|---------|
| ``desktop.multi_action`` | Execute a batch of desktop actions in a single call |
| ``desktop.wait_for`` | Async polling — wait until a condition is met |

### Tips
- ``multi_action`` accepts 2-20 actions. All actions are pre-validated before
  any execution begins. Sensitive actions (e.g. ``launch_app``) are rejected
  in batch mode.
- ``wait_for`` accepts a condition DSL (a dict with ``type`` and parameters)
  and polls until the condition is met or the timeout expires. Use it to
  wait for an element to appear, a window to open, or a value to change.

## Application Launch

| Tool | Purpose |
|------|---------|
| ``desktop.launch_app`` | Launch a desktop application by name or path |

### Tips
- This is a **SENSITIVE** action — it is flagged for user confirmation.
- On Linux, ensure ``DISPLAY`` is set and the display server is accessible.
- On Windows, use the application executable name or full path.
- Electron/Chromium apps may need ``--no-sandbox`` when not running as root.

## Web Tools (Browser Automation)

| Tool | Purpose |
|------|---------|
| ``desktop.web_connect`` | Connect to a browser's CDP debug port |
| ``desktop.web_navigate`` | Navigate a browser tab to a URL |
| ``desktop.web_evaluate`` | Execute JavaScript in a browser page context |

### Tips
- See the ``pathlight-mcp://browser-limitations`` resource for detailed caveats.
- Web tools bypass the DesktopBackend — they use CDP directly.
- ``web_evaluate`` is **SENSITIVE** and rate-limited (10 calls/60 seconds).
- Always call ``web_connect`` before ``web_navigate`` or ``web_evaluate``.
"""

__all__ = ["register"]


def register(mcp: "FastMCP") -> None:
    """Register the tool-usage resource on *mcp*."""

    @mcp.resource(
        "pathlight-mcp://tool-usage",
        name="tool-usage",
        title="Pathlight MCP Tool Usage Guide",
        description=(
            "Comprehensive guide for using all Pathlight MCP desktop tools, "
            "organized by category with tips and best practices"
        ),
        mime_type="text/markdown",
    )
    def tool_usage() -> str:
        """Return the tool usage documentation."""
        return _TOOL_USAGE
