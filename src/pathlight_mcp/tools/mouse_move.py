"""desktop.mouse_move — move the mouse cursor to absolute screen coordinates.

Moves the mouse cursor to the given ``x``, ``y`` pixel coordinates without
clicking.  Classified as INTERACTION because it repositions the cursor but
does not activate any element.

Routes through the existing ``perform_action`` infrastructure with the
``DesktopAction.MOUSE_MOVE`` action variant.  No ABC changes required.
"""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends.types import DesktopAction
from pathlight_mcp.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
)
from pathlight_mcp.hints import hints_for
from pathlight_mcp.safety import classify_system_action

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    **kwargs: object,
) -> None:
    """Register the desktop.mouse_move tool on *mcp*.

    When *backend* is provided the tool validates coordinates, resolves the
    target window, delegates to ``backend.perform_action(MOUSE_MOVE, x=..., y=...)``,
    and returns a structured JSON response with safety metadata.  Without a
    backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.mouse_move")
    def mouse_move(x: int, y: int, duration: float | None = None) -> str:
        """Move the mouse cursor to absolute screen coordinates.

        Repositions the cursor without clicking.  Use before
        desktop.click_xy for precise hover-then-click sequences.

        Args:
            x: X coordinate in screen pixels.
            y: Y coordinate in screen pixels.
            duration: Optional movement duration in seconds.  When
                provided, backends may animate the cursor transition
                over this time.  ``None`` means instant teleport.

        Returns:
            A JSON object with ``success``, ``x``, ``y``, ``risk``,
            ``target_summary``, and ``confirmation_required`` on success,
            or a structured error payload on failure.
        """
        if backend is None:
            return f"Moved cursor to ({x}, {y})"

        # --- Coordinate validation ---
        if x < 0 or y < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "Coordinates must be non-negative integers",
                    "x": x,
                    "y": y,
                    "hints": [],
                }
            )

        # --- Resolve target window ---
        windows = backend.list_windows()
        if not windows:
            return json.dumps(
                {
                    "error": "backend_unavailable",
                    "message": "No windows available for mouse move",
                    "x": x,
                    "y": y,
                    "hints": hints_for("backend_unavailable"),
                }
            )
        target = windows[0]

        # --- Delegate to backend with structured error handling ---
        try:
            move_kwargs: dict = {"x": x, "y": y}
            if duration is not None:
                move_kwargs["duration"] = duration
            backend.perform_action(
                target,
                DesktopAction.MOUSE_MOVE,
                **move_kwargs,
            )
        except BackendUnavailableError as exc:
            return json.dumps(
                {
                    "error": "backend_unavailable",
                    "message": "Accessibility backend is not available",
                    "x": x,
                    "y": y,
                    "hints": exc.hints,
                }
            )
        except ActionNotSupportedError as exc:
            return json.dumps(
                {
                    "error": "action_not_supported",
                    "message": f"Mouse move is not supported at ({x}, {y})",
                    "x": x,
                    "y": y,
                    "hints": exc.hints,
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("desktop_mouse_move")

        return json.dumps(
            {
                "success": True,
                "x": x,
                "y": y,
                "risk": assessment.risk_level.lower(),
                "target_summary": f"mouse move to ({x}, {y})",
                "confirmation_required": False,
            }
        )
