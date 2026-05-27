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
        "If auto_launch is disabled, set auto_launch=True or launch a browser manually",
        "Use web_connect(browser='chrome') to override the default browser discovery order",
        "If CDP is unavailable, use desktop automation fallback: launch_app + snapshot + find",
    ],
    "web_navigate_error": [
        "Ensure web_connect was called first to establish a browser connection",
        "Check that the URL is valid and properly formatted (include the scheme, e.g. https://)",
        "The browser page may have been closed — reconnect with web_connect",
    ],
    "web_evaluate_error": [
        "Ensure web_connect was called first to establish a browser connection",
        "Check the JavaScript expression for syntax errors",
        "The evaluation may have timed out — try a shorter expression or increase the timeout",
        "Avoid expressions that access or modify sensitive data (cookies, tokens, passwords)",
        "The browser page may have navigated away — reconnect with web_connect",
    ],
    "web_element_error": [
        "Ensure web_connect was called first to establish a browser connection",
        "Check that the CSS selector is valid and matches an element on the page",
        "The element may not be visible or interactable — try snapshot to inspect the page",
        "The page may have changed since the last snapshot — take a new snapshot",
        "Use a more specific selector if multiple elements match",
    ],
    "selector_timeout": [
        "The element was not found within the timeout — the page may still be loading",
        "Check the CSS selector for typos or incorrect specificity",
        "Try increasing the timeout_ms parameter",
        "The element may be inside an iframe — selectors only match in the main frame",
        "Use snapshot to inspect the current page structure and available elements",
    ],
    "web_select_option_error": [
        "Ensure web_connect was called first to establish a browser connection",
        "Verify the target element is a <select> dropdown",
        "Check that the value, label, or index matches an available option",
        "Use snapshot to inspect the page and find the correct select element",
        "The browser page may have navigated away — reconnect with web_connect",
    ],
    "web_upload_files_error": [
        "Ensure web_connect was called first to establish a browser connection",
        "Verify the target element is an <input type='file'> element",
        "Check that the file paths are absolute and the files exist on disk",
        "For multiple file uploads, ensure the input element has the 'multiple' attribute",
        "The browser page may have navigated away — reconnect with web_connect",
    ],
    "web_wait_for_error": [
        "Ensure web_connect was called first to establish a browser connection",
        "Check that the condition type and parameters are valid",
        "The page may be loading slowly — try increasing the timeout_ms",
        "For selector-based conditions, verify the CSS selector is correct",
        "The browser page may have navigated away — reconnect with web_connect",
    ],
    "web_screenshot_error": [
        "Ensure web_connect was called first to establish a browser connection",
        "The page may still be loading — try web_wait_for first",
        "For element mode, verify the CSS selector matches a visible element",
        "If the screenshot is too large, try jpeg format with lower quality",
        "The browser page may have navigated away — reconnect with web_connect",
    ],
    "screenshot_too_large": [
        "Use jpeg format with lower quality to reduce file size",
        "Use viewport mode instead of fullpage",
        "Capture a specific element instead of the full page",
        "Increase max_size_kb to allow larger screenshots",
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
