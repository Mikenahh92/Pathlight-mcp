"""desktop.click_xy — click at absolute screen coordinates (coordinate-based fallback).

Clicks at the given ``x``, ``y`` pixel coordinates without resolving an
element reference.  This is the coordinate-based fallback when accessibility
actions fail — classified as SENSITIVE because arbitrary coordinate clicks
can activate any UI element regardless of the caller's intent.

Routes through the existing ``perform_action`` infrastructure with the
``DesktopAction.CLICK_XY`` action variant.  No ABC changes required.
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
    """Register the desktop.click_xy tool on *mcp*.

    When *backend* is provided the tool validates coordinates, resolves the
    target window, delegates to ``backend.perform_action(CLICK_XY, x=..., y=...)``,
    and returns a structured JSON response with safety metadata.  Without a
    backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.click_xy")
    def click_xy(
        x: int,
        y: int,
        button: str = "left",
        click_count: int = 1,
    ) -> str:
        """Click at absolute screen coordinates.

        This is a coordinate-based fallback when accessibility-based clicks
        fail.  Use desktop.snapshot first to identify element positions.

        Args:
            x: X coordinate in screen pixels.
            y: Y coordinate in screen pixels.
            button: Mouse button to click — ``"left"``, ``"right"``, or
                ``"middle"`` (default ``"left"``).
            click_count: Number of clicks — ``1`` for single, ``2`` for
                double-click (default ``1``).

        Returns:
            A JSON object with ``success``, ``x``, ``y``, ``risk``,
            ``target_summary``, ``fallback_used``, and
            ``confirmation_required`` on success, or a structured error
            payload on failure.
        """
        # --- Parameter validation ---
        valid_buttons = {"left", "right", "middle"}
        if button not in valid_buttons:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": f"button must be one of {valid_buttons!r}, got {button!r}",
                    "x": x,
                    "y": y,
                    "hints": [],
                }
            )
        if click_count not in {1, 2}:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": f"click_count must be 1 or 2, got {click_count}",
                    "x": x,
                    "y": y,
                    "hints": [],
                }
            )

        if backend is None:
            return f"Clicked at ({x}, {y})"

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
                    "message": "No windows available for coordinate click",
                    "x": x,
                    "y": y,
                    "hints": hints_for("backend_unavailable"),
                }
            )
        target = windows[0]

        # --- Delegate to backend with structured error handling ---
        try:
            backend.perform_action(
                target,
                DesktopAction.CLICK_XY,
                x=x,
                y=y,
                button=button,
                click_count=click_count,
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
                    "message": f"Coordinate click is not supported at ({x}, {y})",
                    "x": x,
                    "y": y,
                    "hints": exc.hints,
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("desktop_click_xy")

        return json.dumps(
            {
                "success": True,
                "x": x,
                "y": y,
                "risk": assessment.risk_level.lower(),
                "target_summary": f"coordinate click at ({x}, {y})",
                "fallback_used": True,
                "confirmation_required": False,
            }
        )
