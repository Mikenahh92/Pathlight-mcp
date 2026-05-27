"""desktop.web_screenshot — capture web page screenshots via CDP.

Captures a screenshot of a connected browser page using the CDP
``Page.captureScreenshot`` command.  Supports three capture modes:

    viewport  → visible viewport only (default)
    fullpage  → full page content (scrolls to capture everything)
    element   → specific element identified by CSS selector or element_ref

Image formats: ``png`` (default) and ``jpeg`` (with optional quality).

Safety: a ``max_size_kb`` safety cap limits the returned data size.
If the screenshot exceeds the cap, it is rejected with a descriptive error.

Safety classification: READ_ONLY — passive observation, no page modification.

Tool-layer only — no ABC changes.  Depends on GW-122 selector resolver.
"""

import base64
import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from guidewire.hints import hints_for
from guidewire.safety import classify_system_action
from guidewire.tools._web_selector import (
    DEFAULT_TIMEOUT_MS,
    resolve_element,
    resolve_web_session,
)

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Valid capture modes.
_VALID_MODES = frozenset({"viewport", "fullpage", "element"})

# Valid image formats.
_VALID_FORMATS = frozenset({"png", "jpeg"})

# Default max size in KB — 2048 KB (2 MB).
_DEFAULT_MAX_SIZE_KB = 2048


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_screenshot tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool captures a screenshot via CDP.
    Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_screenshot")
    def web_screenshot(
        window_ref: str,
        mode: str = "viewport",
        format: str = "png",
        quality: int | None = None,
        selector: str | None = None,
        element_ref: str | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        max_size_kb: int = _DEFAULT_MAX_SIZE_KB,
    ) -> str:
        """Capture a screenshot of a web page.

        Takes a screenshot of the connected browser page using the
        ``Page.captureScreenshot`` CDP command.  Supports viewport,
        fullpage, and element capture modes.

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect`` identifying the page.
            mode: Capture mode — ``"viewport"`` (default),
                ``"fullpage"``, or ``"element"``.
            format: Image format — ``"png"`` (default) or ``"jpeg"``.
            quality: JPEG quality (0-100).  Ignored for PNG.
            selector: CSS selector for element mode capture.
            element_ref: Element reference for element mode capture.
                Ignored when *selector* is set.
            timeout_ms: Maximum milliseconds to wait for selector
                resolution in element mode (default 5000).
            max_size_kb: Maximum screenshot size in KB (default 2048).
                Screenshots exceeding this limit are rejected.

        Returns:
            A JSON object with ``success``, ``mode``, ``format``,
            ``data`` (base64-encoded image), ``size_kb``, ``risk``,
            and ``target_summary`` on success, or a structured error
            payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": True,
                    "mode": mode,
                    "format": format,
                    "data": "",
                    "size_kb": 0,
                    "message": "stub: web_screenshot succeeds without backend",
                }
            )

        # --- Input validation ---
        if not window_ref or not window_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "window_ref must be a non-empty string",
                    "hints": [],
                }
            )

        if mode not in _VALID_MODES:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        f"mode must be one of {sorted(_VALID_MODES)}, "
                        f"got '{mode}'"
                    ),
                    "hints": [],
                }
            )

        if format not in _VALID_FORMATS:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        f"format must be one of {sorted(_VALID_FORMATS)}, "
                        f"got '{format}'"
                    ),
                    "hints": [],
                }
            )

        if quality is not None and (
            not isinstance(quality, int) or quality < 0 or quality > 100
        ):
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "quality must be an integer between 0 and 100",
                    "hints": [],
                }
            )

        if max_size_kb < 1:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "max_size_kb must be at least 1",
                    "hints": [],
                }
            )

        if mode == "element" and selector is None and element_ref is None:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        "element mode requires either selector or element_ref"
                    ),
                    "hints": [],
                }
            )

        if timeout_ms < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "timeout_ms must be non-negative",
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("web_screenshot")

        # --- Resolve the web session ---
        web, target = resolve_web_session(backend, ref_store, window_ref)
        if isinstance(web, str):
            return web  # error JSON
        if isinstance(target, str):
            return target  # error JSON

        # --- Create session ---
        try:
            session = web._get_or_create_session(target.id)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_screenshot_error",
                    "message": f"Failed to create session for target: {exc}",
                    "hints": hints_for("web_screenshot_error"),
                }
            )

        # --- Capture screenshot ---
        try:
            from guidewire.cdp.domains.page import PageDomain

            page = PageDomain(session)
            cdp_format = format
            cdp_quality = quality

            if mode == "viewport":
                data = _capture_viewport(page, cdp_format, cdp_quality)
            elif mode == "fullpage":
                data = _capture_fullpage(page, cdp_format, cdp_quality)
            elif mode == "element":
                data = _capture_element(
                    page,
                    web,
                    target,
                    ref_store,
                    selector,
                    element_ref,
                    timeout_ms,
                    cdp_format,
                    cdp_quality,
                )
                if isinstance(data, str) and data.startswith('{"error"'):
                    return data  # error JSON from element resolution
            else:
                return json.dumps(
                    {
                        "error": "validation_error",
                        "message": f"Unsupported mode: {mode}",
                        "hints": [],
                    }
                )

        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_screenshot_error",
                    "message": f"Screenshot capture failed: {exc}",
                    "hints": hints_for("web_screenshot_error"),
                }
            )

        if data is None:
            return json.dumps(
                {
                    "error": "web_screenshot_error",
                    "message": "Screenshot capture returned no data",
                    "hints": hints_for("web_screenshot_error"),
                }
            )

        # --- Size safety check ---
        raw_bytes = base64.b64decode(data)
        size_kb = round(len(raw_bytes) / 1024, 1)

        if size_kb > max_size_kb:
            return json.dumps(
                {
                    "error": "screenshot_too_large",
                    "message": (
                        f"Screenshot size ({size_kb:.1f} KB) exceeds "
                        f"max_size_kb ({max_size_kb} KB). "
                        f"Use jpeg format with lower quality or reduce capture area."
                    ),
                    "size_kb": size_kb,
                    "max_size_kb": max_size_kb,
                    "hints": hints_for("web_screenshot_error"),
                }
            )

        return json.dumps(
            {
                "success": True,
                "mode": mode,
                "format": format,
                "data": data,
                "size_kb": size_kb,
                "risk": assessment.risk_level.lower(),
                "target_summary": f"screenshot {mode}",
            }
        )


# -- Capture helpers ---------------------------------------------------------


def _capture_viewport(
    page: Any,
    fmt: str,
    quality: int | None,
) -> str:
    """Capture the visible viewport.

    Args:
        page: PageDomain instance.
        fmt: Image format ("png" or "jpeg").
        quality: Optional JPEG quality.

    Returns:
        Base64-encoded screenshot data.
    """
    kwargs: dict[str, Any] = {"format": fmt}
    if fmt == "jpeg" and quality is not None:
        kwargs["quality"] = quality
    return page.capture_screenshot(**kwargs)


def _capture_fullpage(
    page: Any,
    fmt: str,
    quality: int | None,
) -> str:
    """Capture the full page content.

    Args:
        page: PageDomain instance.
        fmt: Image format.
        quality: Optional JPEG quality.

    Returns:
        Base64-encoded screenshot data.
    """
    kwargs: dict[str, Any] = {"format": fmt}
    if fmt == "jpeg" and quality is not None:
        kwargs["quality"] = quality

    # Get content dimensions for full-page capture
    metrics = page.get_layout_metrics()
    content_size = metrics.get("contentSize") or metrics.get("cssContentSize")

    if content_size:
        width = float(content_size.get("width", 0))
        height = float(content_size.get("height", 0))
        if width > 0 and height > 0:
            kwargs["clip"] = {
                "x": 0.0,
                "y": 0.0,
                "width": width,
                "height": height,
                "scale": 1.0,
            }

    return page.capture_screenshot(**kwargs)


def _capture_element(
    page: Any,
    web: Any,
    target: Any,
    ref_store: "ElementRefStore",
    selector: str | None,
    element_ref: str | None,
    timeout_ms: int,
    fmt: str,
    quality: int | None,
) -> str:
    """Capture a specific element.

    Args:
        page: PageDomain instance.
        web: WebBackend instance.
        target: CDPTarget instance.
        ref_store: ElementRefStore for resolving refs.
        selector: CSS selector.
        element_ref: Element reference.
        timeout_ms: Selector resolution timeout.
        fmt: Image format.
        quality: Optional JPEG quality.

    Returns:
        Base64-encoded screenshot data, or error JSON string.
    """
    result = resolve_element(web, target, ref_store, selector, element_ref, timeout_ms)
    if isinstance(result, str):
        return result  # error JSON

    _node_id, bounds = result

    if bounds is None:
        return json.dumps(
            {
                "error": "web_screenshot_error",
                "message": (
                    "Cannot determine element bounds for screenshot. "
                    "Ensure the element is visible and has dimensions."
                ),
                "hints": hints_for("web_screenshot_error"),
            }
        )

    # Use clip region for element capture
    clip = {
        "x": float(bounds["x"]),
        "y": float(bounds["y"]),
        "width": float(bounds["width"]),
        "height": float(bounds["height"]),
        "scale": 1.0,
    }

    kwargs: dict[str, Any] = {"format": fmt, "clip": clip}
    if fmt == "jpeg" and quality is not None:
        kwargs["quality"] = quality

    return page.capture_screenshot(**kwargs)
