"""desktop.web_hover — hover over a web page element via CSS selector or ref.

Resolves a target element using either a CSS selector string or an existing
element reference, scrolls it into view if needed, and dispatches a hover
(mouse move) via the CDP Input domain.  Supports implicit auto-wait with
configurable timeout for selector resolution.

The tool operates at the web-session layer — it bypasses the DesktopBackend
ABC and directly uses CDP sessions, similar to ``web_evaluate`` and
``web_navigate``.

Safety classification: INTERACTION — hovering over a web element is a standard
user interaction with bounded side-effects.

Tool-layer only — no ABC changes.
"""

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


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.web_hover tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool resolves the target element and dispatches
    a hover (mouse move).  Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_hover")
    def web_hover(
        window_ref: str,
        selector: str | None = None,
        element_ref: str | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> str:
        """Hover over a web page element by CSS selector or element reference.

        Resolves the target element using a CSS selector (preferred) or an
        existing element reference from a prior snapshot.  If the element is
        not immediately found via selector, polls automatically until the
        timeout expires (implicit auto-wait).

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect`` identifying the page.
            selector: CSS selector to resolve the target element
                (e.g. ``"#menu-item"``, ``".dropdown-trigger"``).
            element_ref: Existing element reference (``e``-prefixed)
                from a prior snapshot.  Ignored when *selector* is set.
            timeout_ms: Maximum milliseconds to wait for the selector
                to resolve (default 5000).  Set to 0 for no wait.

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

        if timeout_ms < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "timeout_ms must be non-negative",
                    "hints": [],
                }
            )

        # --- Safety metadata ---
        assessment = classify_system_action("web_hover", target=selector or element_ref)

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

        # --- Scroll into view and hover ---
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
            from guidewire.cdp.domains.dom import DOMDomain
            from guidewire.cdp.domains.input import InputDomain

            dom = DOMDomain(session)
            inp = InputDomain(session)

            # Scroll into view
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
                        "message": "Cannot determine element bounds for hover",
                        "hints": hints_for("web_element_error"),
                    }
                )

            x = float(bounds["x"]) + float(bounds["width"]) / 2
            y = float(bounds["y"]) + float(bounds["height"]) / 2

            # Dispatch mouseMoved to hover
            inp.dispatch_mouse_event("mouseMoved", x, y, button="none")
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_element_error",
                    "message": f"Hover dispatch failed: {exc}",
                    "hints": hints_for("web_element_error"),
                }
            )

        return json.dumps(
            {
                "success": True,
                "selector": selector,
                "risk": assessment.risk_level.lower(),
                "confirmation_required": assessment.confirmation_required,
                "target_summary": f"hover {selector or element_ref}",
            }
        )
