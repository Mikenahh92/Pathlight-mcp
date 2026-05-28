"""desktop.focus_window — bring a window to the foreground.

Resolves a ``w``-prefixed short reference to a native handle via the
:class:`~pathlight_mcp.refs.ElementRefStore`, validates it through the backend,
calls :meth:`~pathlight_mcp.backends.base.DesktopBackend.focus_window`, and
returns a structured success or error response (PRD R3, §6.2).
"""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.errors import StaleElementReferenceError, WindowNotFoundError
from pathlight_mcp.hints import hints_for
from pathlight_mcp.models import ElementStates, NormalizedElement
from pathlight_mcp.safety import classify

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.focus_window tool on *mcp*.

    When *backend* is provided the tool resolves *window_ref* through
    *ref_store*, validates the handle, and delegates to
    ``backend.focus_window()``.  Without a backend it returns a static
    stub response.
    """

    @mcp.tool(name="desktop.focus_window")
    def focus_window(window_ref: str) -> str:
        """Bring a window to the foreground.

        Args:
            window_ref: Short reference handle for the target window
                (e.g. ``"w1"``).

        Returns:
            A JSON object with ``success``, ``ref``, ``title``, ``risk``,
            and ``target_summary`` on success, or a structured error payload
            on failure.
        """
        if backend is None or ref_store is None:
            return f"Focused window {window_ref}"

        # --- Input validation (AC-6) ---
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

        # --- Staleness check (AC-2) ---
        if not backend.is_valid(handle):
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Window reference '{window_ref}' is no longer valid"),
                    "ref": window_ref,
                    "hints": hints_for("stale_element_reference"),
                }
            )

        # --- Focus window with structured error handling (AC-4) ---
        title: str | None = None
        try:
            info = backend.get_window_info(handle)
            title = info.get("title")
            backend.focus_window(handle)
        except WindowNotFoundError as exc:
            return json.dumps(
                {
                    "error": "stale_element_reference",
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

        # --- Safety metadata (AC-5, epic §9.4) ---
        element = NormalizedElement(
            ref=window_ref,
            backend_id=str(handle),
            role="window",
            name=title,
            states=ElementStates(enabled=True),
        )
        assessment = classify(element, "focus")

        return json.dumps(
            {
                "success": True,
                "ref": window_ref,
                "title": title,
                "risk": assessment.risk_level.lower(),
                "target_summary": "window focus",
            }
        )
