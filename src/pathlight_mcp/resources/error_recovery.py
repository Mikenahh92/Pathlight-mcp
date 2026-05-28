"""Error recovery guide resource.

Registers the ``pathlight-mcp://error-recovery`` resource that documents all
Pathlight MCP error codes, their meanings, and recommended recovery strategies.

The error codes and hints are sourced from :mod:`pathlight_mcp.errors` and
:mod:`pathlight_mcp.hints` to stay consistent with runtime error responses.
"""

from typing import TYPE_CHECKING

from pathlight_mcp.hints import _HINT_REGISTRY

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_ERROR_DESCRIPTIONS: dict[str, str] = {
    "backend_unavailable": (
        "The platform accessibility backend could not be initialized or is unreachable. "
        "This typically means the OS accessibility framework is not available or the "
        "MCP server process lacks the required permissions."
    ),
    "element_not_found": (
        "The requested UI element could not be located in the accessibility tree. "
        "The element may have been removed, the application may have navigated to a "
        "different view, or the element reference may be incorrect."
    ),
    "stale_element_reference": (
        "The referenced element no longer exists in the accessibility tree. "
        "This happens when the application UI has changed (page navigation, dialog "
        "closed, list refreshed) since the element reference was obtained."
    ),
    "action_not_supported": (
        "The requested action is not supported by the target element. "
        "For example, trying to click a read-only label or type into a non-editable "
        "element."
    ),
    "permission_required": (
        "OS-level accessibility permission is required but has not been granted to "
        "the MCP server process. This is required for the server to interact with "
        "application UIs."
    ),
    "ambiguous_selector": (
        "The selector matched multiple elements instead of a single target. "
        "This occurs when the search criteria (role, name) are too broad."
    ),
    "window_not_found": (
        "The specified window could not be found or no longer exists. "
        "The window may have been closed, minimized to the system tray, or renamed."
    ),
    "launch_error": (
        "The application failed to launch. This may be due to a missing binary, "
        "insufficient permissions, or a missing display server connection."
    ),
    "app_not_found": (
        "The specified application could not be found on the system. "
        "The application name or path may be incorrect, or the application may not "
        "be installed."
    ),
    "web_connect_error": (
        "Failed to connect to the browser's CDP debug port. The browser may not be "
        "running with remote debugging enabled, or the host/port may be incorrect."
    ),
    "web_navigate_error": (
        "Failed to navigate the browser tab. This may occur if there is no active "
        "web session, the URL is invalid, or the browser tab has been closed."
    ),
    "web_evaluate_error": (
        "Failed to execute JavaScript in the browser page. This may be due to a "
        "syntax error in the expression, a timeout, or the browser page having "
        "navigated away."
    ),
}


def _build_error_recovery_guide() -> str:
    """Build the error recovery guide from the hint registry and descriptions."""
    sections: list[str] = [
        "# Pathlight MCP Error Recovery Guide\n",
        "This document lists all Pathlight MCP error codes, their meanings, and "
        "recommended recovery strategies. Error responses from tools include a "
        "``hints`` array with actionable suggestions.\n",
        "## General Recovery Pattern\n",
        "1. **Read the error code** — it tells you the category of failure",
        "2. **Check the hints** — each error response includes specific recovery hints",
        "3. **Re-snapshot** — most errors are resolved by taking a new snapshot",
        "4. **Retry** — after recovering, retry the original action\n",
    ]

    for code in sorted(_HINT_REGISTRY):
        description = _ERROR_DESCRIPTIONS.get(code, "No description available.")
        hints = _HINT_REGISTRY[code]
        sections.append(f"## {code}\n")
        sections.append(f"{description}\n")
        if hints:
            sections.append("**Recovery hints:**\n")
            for hint in hints:
                sections.append(f"- {hint}")
            sections.append("")

    return "\n".join(sections)


__all__ = ["register"]


def register(mcp: "FastMCP") -> None:
    """Register the error-recovery resource on *mcp*."""

    @mcp.resource(
        "pathlight-mcp://error-recovery",
        name="error-recovery",
        title="Error Recovery Guide",
        description=(
            "All Pathlight MCP error codes, their meanings, and recommended "
            "recovery strategies for agent self-recovery"
        ),
        mime_type="text/markdown",
    )
    def error_recovery() -> str:
        """Return the error recovery documentation."""
        return _build_error_recovery_guide()
