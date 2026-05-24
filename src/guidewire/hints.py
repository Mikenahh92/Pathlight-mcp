"""Hint registry for Guidewire structured errors.

Provides default, actionable recovery hints per error code.  Tool handlers
can also register custom hints at runtime via :func:`register_hints`.

Architecture §1.2 — standalone module consumed by ``guidewire.errors``.
"""

_HINT_REGISTRY: dict[str, list[str]] = {
    "backend_unavailable": [
        "Verify the platform accessibility framework is running",
        "Check OS accessibility permissions for the MCP server process",
    ],
    "element_not_found": [
        "Use snapshot or find to discover currently available elements",
        "Verify the application window is focused and the element is visible",
    ],
    "stale_element_reference": [
        "Re-discover the element using snapshot or find",
        "The application may have refreshed — take a new snapshot",
    ],
    "action_not_supported": [
        "Use snapshot to inspect the element's available states and roles",
        "The element may not support the requested interaction type",
    ],
    "permission_required": [
        "Grant accessibility permissions to the MCP server in OS settings",
        "Restart the application after granting permissions",
    ],
    "ambiguous_selector": [
        "Narrow the selector by adding more specific criteria",
        "Use find with additional filters (role, name, state)",
    ],
    "window_not_found": [
        "Use list_windows to see currently available windows",
        "The window may have been closed or renamed",
    ],
    "launch_error": [
        "The application may be missing shared libraries — try running it from a terminal",
        "On Linux, verify DISPLAY is set and the display server is accessible",
        "If the app is a snap package, try running 'snap run <app>' directly",
        "For Electron/Chromium apps, ensure --no-sandbox is passed if not running as root",
    ],
    "app_not_found": [
        "Check the application name or path for typos",
        "Use 'which <app>' on Linux or 'where <app>' on Windows to locate the binary",
        "Verify the application is installed and in your PATH",
    ],
    "web_connect_error": [
        "Ensure the browser is running with --remote-debugging-port=<port>",
        "Verify the host and port are correct and accessible",
        "Check that no firewall is blocking the debug port",
        "Try connecting to localhost:9222 if using default Chrome debug settings",
    ],
    "web_navigate_error": [
        "Ensure web_connect was called first to establish a browser connection",
        "Check that the URL is valid and properly formatted (include the scheme, e.g. https://)",
        "The browser page may have been closed — reconnect with web_connect",
    ],
}


def register_hints(error_code: str, hints: list[str]) -> None:
    """Register default hints for an error code in the global hint registry.

    Args:
        error_code: The machine-readable error code to register hints for.
        hints: A list of actionable recovery suggestion strings.
    """
    _HINT_REGISTRY[error_code] = list(hints)


def hints_for(error_code: str) -> list[str]:
    """Return the registered default hints for *error_code*.

    Returns a copy of the registered hint list, or an empty list if the
    error code has no registered hints.

    Args:
        error_code: The machine-readable error code to look up.

    Returns:
        A list of hint strings (may be empty, never ``None``).
    """
    return list(_HINT_REGISTRY.get(error_code, []))


__all__ = [
    "hints_for",
    "register_hints",
]
