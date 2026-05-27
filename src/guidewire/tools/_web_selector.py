"""Shared selector resolution engine for web element tools (GW-122).

Architecture §1.2 — dedicated module for CSS selector resolution and element
reference lookup used by ``web_click``, ``web_type``, and ``web_hover``.
Keeps resolver logic decoupled from tool handlers to avoid tight coupling
between tool modules.

Key functions:
    - :func:`resolve_web_session` — resolve a window ref to a WebBackend + CDPTarget
    - :func:`resolve_element` — resolve a selector or element_ref to node_id + bounds
"""

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from guidewire.backends.router import BackendRouter, _untag
from guidewire.cdp._types import CDPTarget
from guidewire.hints import hints_for

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)

# Default auto-wait timeout in milliseconds.
DEFAULT_TIMEOUT_MS = 5000

# Polling interval for selector auto-wait in seconds.
POLL_INTERVAL = 0.2


# ---------------------------------------------------------------------------
# resolve_web_session
# ---------------------------------------------------------------------------


def resolve_web_session(
    backend: "DesktopBackend",
    ref_store: "ElementRefStore",
    window_ref: str,
) -> tuple[Any, Any]:
    """Resolve the BackendRouter and CDPTarget for a window reference.

    Returns:
        Tuple of (web_backend, cdp_target), or (error_json_str, error_json_str)
        on failure.
    """
    if not isinstance(backend, BackendRouter):
        return (
            json.dumps(
                {
                    "error": "web_element_error",
                    "message": (
                        "web_click requires a BackendRouter backend — "
                        "the server is not configured for web support"
                    ),
                    "hints": hints_for("web_element_error"),
                }
            ),
            "",
        )

    web = backend.web
    if web is None:
        return (
            json.dumps(
                {
                    "error": "web_element_error",
                    "message": "No web connection — call desktop.web_connect first",
                    "hints": hints_for("web_element_error"),
                }
            ),
            "",
        )

    # Resolve window reference to CDPTarget
    tagged_handle = ref_store.resolve(window_ref)
    if tagged_handle is None:
        return (
            json.dumps(
                {
                    "error": "web_element_error",
                    "message": f"Window reference '{window_ref}' not found in ref store",
                    "hints": hints_for("web_element_error"),
                }
            ),
            "",
        )

    inner, backend_id = _untag(tagged_handle)
    if backend_id != "web":
        return (
            json.dumps(
                {
                    "error": "web_element_error",
                    "message": (
                        f"Window reference '{window_ref}' is not a web window "
                        f"(backend_id={backend_id!r})"
                    ),
                    "hints": hints_for("web_element_error"),
                }
            ),
            "",
        )

    target = _extract_target(inner)
    if target is None:
        return (
            json.dumps(
                {
                    "error": "web_element_error",
                    "message": (
                        f"Could not resolve window reference '{window_ref}' to a CDP target"
                    ),
                    "hints": hints_for("web_element_error"),
                }
            ),
            "",
        )

    return web, target


# ---------------------------------------------------------------------------
# resolve_element
# ---------------------------------------------------------------------------


def resolve_element(
    web: Any,
    target: CDPTarget,
    ref_store: "ElementRefStore",
    selector: str | None,
    element_ref: str | None,
    timeout_ms: int,
) -> tuple[int | None, dict[str, float] | None] | str:
    """Resolve an element by selector (with auto-wait) or element_ref.

    Returns:
        Tuple of (node_id, bounds_dict) on success, or error JSON string.
    """
    from guidewire.cdp.domains.dom import DOMDomain

    try:
        session = web._get_or_create_session(target.id)
    except Exception as exc:
        return json.dumps(
            {
                "error": "web_element_error",
                "message": f"Failed to create session: {exc}",
                "hints": hints_for("web_element_error"),
            }
        )

    dom = DOMDomain(session)

    if selector is not None:
        # CSS selector path — with implicit auto-wait
        return _resolve_by_selector(dom, selector, timeout_ms)

    if element_ref is not None:
        # Element reference path — resolve from ref store
        tagged_handle = ref_store.resolve(element_ref)
        if tagged_handle is None:
            return json.dumps(
                {
                    "error": "element_not_found",
                    "message": (f"Element reference '{element_ref}' not found in ref store"),
                    "hints": hints_for("element_not_found"),
                }
            )

        inner, backend_id = _untag(tagged_handle)
        if backend_id != "web":
            return json.dumps(
                {
                    "error": "web_element_error",
                    "message": (f"Element reference '{element_ref}' is not a web element"),
                    "hints": hints_for("web_element_error"),
                }
            )

        # The inner handle is the AX node ID string
        node_id_str = str(inner)

        # Try to get bounds from the AX/bounds cache
        bounds = _get_bounds_from_ax_cache(web, node_id_str, dom)
        if bounds is not None:
            return None, bounds

        # Try querying the DOM directly
        return _try_get_bounds_from_dom(dom, node_id_str)

    return json.dumps(
        {
            "error": "validation_error",
            "message": "Either selector or element_ref must be provided",
            "hints": [],
        }
    )


# ---------------------------------------------------------------------------
# _resolve_by_selector
# ---------------------------------------------------------------------------


def _resolve_by_selector(
    dom: Any,
    selector: str,
    timeout_ms: int,
) -> tuple[int | None, dict[str, float] | None] | str:
    """Resolve an element by CSS selector with implicit auto-wait.

    Polls the DOM with ``DOM.querySelector`` until a match is found or
    the timeout expires.

    Returns:
        Tuple of (node_id, bounds_dict) on success, or error JSON string.
    """
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    last_error: str = ""

    while True:
        try:
            doc = dom.get_document(depth=0)
            root_id = doc.node_id
            node_id = dom.query_selector(root_id, selector)

            if node_id is not None and node_id != 0:
                # Check for ambiguous selector — querySelectorAll returns
                # multiple matches for the same selector (AC13).
                all_ids = dom.query_selector_all(root_id, selector)
                if len(all_ids) > 1:
                    return json.dumps(
                        {
                            "error": "ambiguous_selector",
                            "message": (
                                f"CSS selector '{selector}' matched {len(all_ids)} elements — "
                                f"use a more specific selector or an element reference"
                            ),
                            "selector": selector,
                            "match_count": len(all_ids),
                            "hints": hints_for("ambiguous_selector"),
                        }
                    )

                # Found exactly one — get bounds
                try:
                    box = dom.get_box_model(node_id=node_id)
                    if box is not None and box.bounds is not None:
                        bx, by, bw, bh = box.bounds
                        return node_id, {
                            "x": bx,
                            "y": by,
                            "width": bw,
                            "height": bh,
                        }
                except Exception:
                    pass
                # Element found but no bounds — return node_id anyway
                return node_id, None

        except Exception as exc:
            last_error = str(exc)

        # Check timeout
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_INTERVAL)

    return json.dumps(
        {
            "error": "selector_timeout",
            "message": (
                f"CSS selector '{selector}' did not match any element "
                f"within {timeout_ms}ms timeout" + (f": {last_error}" if last_error else "")
            ),
            "selector": selector,
            "hints": hints_for("selector_timeout"),
        }
    )


# ---------------------------------------------------------------------------
# AX cache helpers
# ---------------------------------------------------------------------------


def _get_bounds_from_ax_cache(
    web: Any,
    node_id_str: str,
    dom: Any,
) -> dict[str, float] | None:
    """Try to get bounds from the WebBackend AX/bounds cache."""
    # Check bounds cache
    cached = web._bounds_cache.get(node_id_str)
    if cached is not None:
        return cached

    # Check AX cache for inline bounds
    node = web._ax_cache.get(node_id_str)
    if node is not None and node.bounds is not None:
        return node.bounds

    return None


def _try_get_bounds_from_dom(
    dom: Any,
    node_id_str: str,
) -> tuple[int | None, dict[str, float] | None] | str:
    """Try to resolve an AX node ID to DOM bounds.

    Returns:
        Tuple of (node_id, bounds_dict), or error JSON string.
    """
    # For element_ref path, we don't have a DOM node_id directly.
    # Return with no bounds — the caller will try other strategies.
    return None, None


# ---------------------------------------------------------------------------
# _extract_target
# ---------------------------------------------------------------------------


def _extract_target(handle: object) -> CDPTarget | None:
    """Extract a CDPTarget from a possibly-wrapped handle.

    Args:
        handle: The handle to extract from.

    Returns:
        A :class:`CDPTarget` instance, or ``None``.
    """
    if isinstance(handle, CDPTarget):
        return handle
    return None
