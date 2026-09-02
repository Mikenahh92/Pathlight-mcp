"""desktop.screenshot — cross-platform native screenshot capture (GW-149).

Captures a screenshot of the specified native desktop window and returns the
image data as a base64-encoded PNG string.  Classified as READ_ONLY because
it only reads pixel data without modifying any UI state.

Privacy gating is applied via :func:`~pathlight_mcp.privacy.should_allow_screenshot`
to deny captures of applications on the configured denylist.

Routes through the ``DesktopBackend.screenshot()`` ABC method, which uses the
``mss`` library for cross-platform screen capture.  For web page screenshots,
use ``desktop.web_screenshot`` instead.
"""

import base64
import json
import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
    WindowNotFoundError,
)
from pathlight_mcp.hints import hints_for
from pathlight_mcp.privacy import should_allow_screenshot
from pathlight_mcp.safety import classify_system_action

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Default maximum output size in kilobytes (safety cap).
_DEFAULT_MAX_SIZE_KB = 2048


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
    **kwargs: object,
) -> None:
    """Register the desktop.screenshot tool on *mcp*.

    When *backend* and *ref_store* are provided the tool resolves
    *window_ref* through *ref_store*, validates the handle, applies the
    privacy gate, calls ``backend.screenshot()``, and returns a structured
    JSON response with safety metadata.  Without a backend it returns a
    static stub response.
    """

    @mcp.tool(name="desktop.screenshot")
    def screenshot(
        window_ref: str,
        max_size_kb: int = _DEFAULT_MAX_SIZE_KB,
    ) -> str:
        """Capture a screenshot of a native desktop window.

        Returns a base64-encoded PNG image of the window's content.

        Args:
            window_ref: Short reference handle for the target window
                (e.g. ``"w1"``).  Obtain from ``desktop.list_windows``.
            max_size_kb: Maximum allowed screenshot size in kilobytes
                (default 2048).  Exceeding this cap returns an error.

        Returns:
            A JSON object with ``success``, ``image_base64``, ``size_bytes``,
            ``format``, ``risk``, ``target_summary``, and
            ``confirmation_required`` on success, or a structured error
            payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": True,
                    "image_base64": "",
                    "size_bytes": 0,
                    "format": "png",
                    "risk": "read_only",
                    "target_summary": "screenshot (stub)",
                    "confirmation_required": False,
                }
            )

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
                    "message": f"window_ref must start with 'w', got '{window_ref}'",
                    "ref": window_ref,
                    "hints": [],
                }
            )

        if max_size_kb <= 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": f"max_size_kb must be positive, got {max_size_kb}",
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
                    "message": f"Window reference '{window_ref}' not found in reference store",
                    "ref": window_ref,
                    "hints": hints_for("window_not_found"),
                }
            )

        # --- Staleness check ---
        if not backend.is_valid(handle):
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": f"Window reference '{window_ref}' is no longer valid",
                    "ref": window_ref,
                    "hints": hints_for("stale_element_reference"),
                }
            )

        # --- Privacy gate ---
        try:
            window_info = backend.get_window_info(handle)
        except WindowNotFoundError as exc:
            return json.dumps(
                {
                    "error": "window_not_found",
                    "message": f"Window reference '{window_ref}' not found",
                    "ref": window_ref,
                    "hints": exc.hints,
                }
            )

        app_name = window_info.get("app_name")
        if not should_allow_screenshot(app_name=app_name):
            return json.dumps(
                {
                    "error": "privacy_denied",
                    "message": f"Screenshot denied for application '{app_name}' by privacy policy",
                    "ref": window_ref,
                    "hints": hints_for("desktop_screenshot_error"),
                }
            )

        # --- Capture screenshot ---
        try:
            png_bytes = backend.screenshot(handle)
        except BackendUnavailableError as exc:
            return json.dumps(
                {
                    "error": "backend_unavailable",
                    "message": f"Screenshot capture failed: {exc.message}",
                    "ref": window_ref,
                    "hints": exc.hints,
                }
            )
        except ActionNotSupportedError as exc:
            return json.dumps(
                {
                    "error": "action_not_supported",
                    "message": exc.message,
                    "ref": window_ref,
                    "hints": exc.hints,
                }
            )
        except WindowNotFoundError as exc:
            return json.dumps(
                {
                    "error": "window_not_found",
                    "message": f"Window reference '{window_ref}' not found during capture",
                    "ref": window_ref,
                    "hints": exc.hints,
                }
            )
        except Exception as exc:
            logger.debug("Unexpected screenshot error", exc_info=True)
            return json.dumps(
                {
                    "error": "desktop_screenshot_error",
                    "message": f"Screenshot capture failed: {exc}",
                    "ref": window_ref,
                    "hints": hints_for("desktop_screenshot_error"),
                }
            )

        # --- Size cap check ---
        size_bytes = len(png_bytes)
        size_kb = size_bytes / 1024

        if size_kb > max_size_kb:
            return json.dumps(
                {
                    "error": "screenshot_too_large",
                    "message": (
                        f"Screenshot size ({size_kb:.1f} KB) exceeds "
                        f"max_size_kb limit ({max_size_kb} KB)"
                    ),
                    "ref": window_ref,
                    "size_bytes": size_bytes,
                    "hints": hints_for("screenshot_too_large"),
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("desktop_screenshot")

        image_b64 = base64.b64encode(png_bytes).decode("ascii")
        window_title = window_info.get("title", "")

        return json.dumps(
            {
                "success": True,
                "image_base64": image_b64,
                "size_bytes": size_bytes,
                "format": "png",
                "risk": assessment.risk_level.lower(),
                "target_summary": f"screenshot of '{window_title}'",
                "confirmation_required": False,
            }
        )
