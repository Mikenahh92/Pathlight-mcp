"""desktop.web_click — click a web page element via CSS selector or ref.

Resolves a target element using either a CSS selector string or an existing
element reference, scrolls it into view if needed, and dispatches a click
via the CDP Input domain.  Supports implicit auto-wait with configurable
timeout for selector resolution, and multi-button click (left/right/middle).

The tool operates at the web-session layer — it bypasses the DesktopBackend
ABC and directly uses CDP sessions, similar to ``web_evaluate`` and
``web_navigate``.  This gives precise DOM-level element targeting without
requiring a full AX tree snapshot.

Safety classification: INTERACTION — clicking a web element is a standard
user interaction with bounded side-effects.

Tool-layer only — no ABC changes.
"""

import json
import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.hints import hints_for
from pathlight_mcp.safety import classify_system_action
from pathlight_mcp.tools._web_selector import (
    DEFAULT_TIMEOUT_MS,
    resolve_element,
    resolve_web_session,
)

if TYPE_CHECKING:
    from pathlight_mcp.backends.base import DesktopBackend
    from pathlight_mcp.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Valid mouse button values.
_VALID_BUTTONS = frozenset({"left", "right", "middle"})


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_click tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool resolves the target element and dispatches
    a click.  Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_click")
    def web_click(
        window_ref: str,
        selector: str | None = None,
        element_ref: str | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        click_count: int = 1,
        button: str = "left",
    ) -> str:
        """Click a web page element by CSS selector or element reference.

        Resolves the target element using a CSS selector (preferred) or an
        existing element reference from a prior snapshot.  If the element is
        not immediately found via selector, polls automatically until the
        timeout expires (implicit auto-wait).

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect`` identifying the page.
            selector: CSS selector to resolve the target element
                (e.g. ``"#submit-btn"``, ``"button.login"``).
            element_ref: Existing element reference (``e``-prefixed)
                from a prior snapshot.  Ignored when *selector* is set.
            timeout_ms: Maximum milliseconds to wait for the selector
                to resolve (default 5000).  Set to 0 for no wait.
            click_count: Number of clicks to dispatch (default 1,
                set to 2 for double-click).
            button: Mouse button to use (``"left"``, ``"right"``,
                ``"middle"``).  Default is ``"left"``.

        Returns:
            A JSON object with ``success``, ``selector``, ``risk``,
            ``confirmation_required``, and ``target_summary`` on success,
            or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": True,
                    "selector": selector,
                    "click_count": click_count,
                    "button": button,
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

        if selector is None and element_ref is None:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "Either selector or element_ref must be provided",
                    "hints": [],
                }
            )

        if selector is not None and not selector.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "selector must be a non-empty string when provided",
                    "hints": [],
                }
            )

        if click_count < 1 or click_count > 3:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "click_count must be between 1 and 3",
                    "hints": [],
                }
            )

        if button not in _VALID_BUTTONS:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": f"button must be one of {sorted(_VALID_BUTTONS)}",
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
        assessment = classify_system_action("web_click", target=selector or element_ref)

        # --- Resolve the web session ---
        web, target = resolve_web_session(backend, ref_store, window_ref)
        if isinstance(web, str):
            return web  # error JSON
        if isinstance(target, str):
            return target  # error JSON

        # --- Resolve the element ---
        result = resolve_element(web, target, ref_store, selector, element_ref, timeout_ms)
        if isinstance(result, str):
            return result  # error JSON

        node_id, bounds = result

        # --- Scroll into view and click ---
        try:
            session = web._get_or_create_session(target.id)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_element_error",
                    "message": f"Failed to create session for target: {exc}",
                    "hints": hints_for("web_element_error"),
                }
            )

        try:
            from pathlight_mcp.cdp.domains.dom import DOMDomain
            from pathlight_mcp.cdp.domains.input import InputDomain

            dom = DOMDomain(session)
            inp = InputDomain(session)

            # Scroll into view if we have a node_id
            if node_id is not None:
                try:
                    dom.scroll_into_view_if_needed(node_id=node_id)
                except Exception:
                    logger.debug(
                        "scrollIntoViewIfNeeded failed for node %s",
                        node_id,
                        exc_info=True,
                    )

            # Get fresh bounds after scroll
            if node_id is not None:
                try:
                    box = dom.get_box_model(node_id=node_id)
                    if box is not None and box.bounds is not None:
                        bx, by, bw, bh = box.bounds
                        bounds = {
                            "x": bx,
                            "y": by,
                            "width": bw,
                            "height": bh,
                        }
                except Exception:
                    logger.debug(
                        "getBoxModel after scroll failed for node %s",
                        node_id,
                        exc_info=True,
                    )

            if bounds is None:
                return json.dumps(
                    {
                        "error": "web_element_error",
                        "message": "Cannot determine element bounds for click",
                        "hints": hints_for("web_element_error"),
                    }
                )

            x = float(bounds["x"]) + float(bounds["width"]) / 2
            y = float(bounds["y"]) + float(bounds["height"]) / 2

            inp.dispatch_mouse_event("mousePressed", x, y, button=button, click_count=click_count)
            inp.dispatch_mouse_event("mouseReleased", x, y, button=button, click_count=click_count)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_element_error",
                    "message": f"Click dispatch failed: {exc}",
                    "hints": hints_for("web_element_error"),
                }
            )

        return json.dumps(
            {
                "success": True,
                "selector": selector,
                "click_count": click_count,
                "button": button,
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
                "target_summary": f"click {selector or element_ref}",
            }
        )
