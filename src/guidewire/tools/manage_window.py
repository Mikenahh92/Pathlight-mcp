"""desktop.manage_window — window state management (minimize, maximize, restore, move, resize).

Resolves a ``w``-prefixed short reference to a native handle via the
:class:`~guidewire.refs.ElementRefStore`, validates it through the backend,
dispatches to the appropriate :class:`~guidewire.backends.base.DesktopBackend`
window management method, and returns a structured success or error response.

Supported actions:
    minimize — minimize the window to the taskbar / dock
    maximize — maximize the window to fill the screen
    restore  — restore the window from minimized/maximized state
    move     — move the window to (x, y) screen coordinates
    resize   — resize the window to (width x height) pixels
"""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from guidewire.errors import (
    ActionNotSupportedError,
    StaleElementReferenceError,
    WindowNotFoundError,
)
from guidewire.hints import hints_for
from guidewire.safety import classify_system_action

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

_VALID_ACTIONS = frozenset({"minimize", "maximize", "restore", "move", "resize"})


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.manage_window tool on *mcp*.

    When *backend* is provided the tool resolves *window_ref* through
    *ref_store*, validates the handle, and delegates to the appropriate
    backend method.  Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.manage_window")
    def manage_window(
        window_ref: str,
        action: str,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> str:
        """Manage window state: minimize, maximize, restore, move, or resize.

        Args:
            window_ref: Short reference handle for the target window
                (e.g. ``"w1"``).
            action: Window management action to perform.  One of:
                ``"minimize"``, ``"maximize"``, ``"restore"``, ``"move"``,
                ``"resize"``.
            x: Target X coordinate (required for ``"move"``).
            y: Target Y coordinate (required for ``"move"``).
            width: Target width in pixels (required for ``"resize"``).
            height: Target height in pixels (required for ``"resize"``).

        Returns:
            A JSON object with ``success``, ``ref``, ``title``, ``action``,
            ``risk``, and ``target_summary`` on success, or a structured
            error payload on failure.
        """
        if backend is None or ref_store is None:
            return f"Managed window {window_ref}: {action}"

        # --- Input validation ---
        if not window_ref or not window_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "window_ref must be a non-empty string",
                    "ref": window_ref,
                    "hints": [],
                }
            )

        if not window_ref.startswith("w"):
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (f"window_ref must start with 'w', got '{window_ref}'"),
                    "ref": window_ref,
                    "hints": [],
                }
            )

        if action not in _VALID_ACTIONS:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (f"action must be one of {sorted(_VALID_ACTIONS)}, got '{action}'"),
                    "ref": window_ref,
                    "hints": [],
                }
            )

        if action == "move" and (x is None or y is None):
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "move action requires both x and y parameters",
                    "ref": window_ref,
                    "hints": [],
                }
            )

        if action == "resize" and (width is None or height is None):
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "resize action requires both width and height parameters",
                    "ref": window_ref,
                    "hints": [],
                }
            )

        # --- Resolve reference ---
        handle = ref_store.resolve(window_ref)

        if handle is None:
            return json.dumps(
                {
                    "error": "window_not_found",
                    "message": (f"Window reference '{window_ref}' not found in reference store"),
                    "ref": window_ref,
                    "hints": hints_for("window_not_found"),
                }
            )

        # --- Staleness check ---
        if not backend.is_valid(handle):
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Window reference '{window_ref}' is no longer valid"),
                    "ref": window_ref,
                    "hints": hints_for("stale_element_reference"),
                }
            )

        # --- Execute action ---
        title: str | None = None
        try:
            info = backend.get_window_info(handle)
            title = info.get("title")

            if action == "minimize":
                backend.minimize_window(handle)
            elif action == "maximize":
                backend.maximize_window(handle)
            elif action == "restore":
                backend.restore_window(handle)
            elif action == "move":
                backend.move_window(handle, x, y)  # type: ignore[arg-type]
            elif action == "resize":
                backend.resize_window(handle, width, height)  # type: ignore[arg-type]
        except WindowNotFoundError as exc:
            return json.dumps(
                {
                    "error": "window_not_found",
                    "message": (f"Window reference '{window_ref}' is no longer valid"),
                    "ref": window_ref,
                    "hints": exc.hints,
                }
            )
        except StaleElementReferenceError as exc:
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Window reference '{window_ref}' is stale"),
                    "ref": window_ref,
                    "hints": exc.hints,
                }
            )
        except ActionNotSupportedError as exc:
            return json.dumps(
                {
                    "error": "action_not_supported",
                    "message": str(exc),
                    "ref": window_ref,
                    "hints": exc.hints,
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("window_manage", target=title or window_ref)

        # Build action-specific summary
        if action == "move":
            details = f"to ({x}, {y})"
        elif action == "resize":
            details = f"to {width}x{height}"
        else:
            details = ""

        return json.dumps(
            {
                "success": True,
                "ref": window_ref,
                "title": title,
                "action": action,
                "risk": assessment.risk_level.lower(),
                "target_summary": f"window {action} {details}".strip(),
            }
        )
