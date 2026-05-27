"""desktop.web_frame_tree — inspect iframe hierarchy via CDP Page domain.

Returns the nested frame hierarchy for a browser page, showing all iframes
and their children with URLs, names, and IDs.  Useful for understanding
multi-frame page structures before interaction.

Requires an active web connection established via
:func:`~guidewire.tools.web_connect`.

Safety classification: INFORMATIONAL — read-only frame tree inspection.

Tool-layer only — no ABC changes.  Relies on the existing
:class:`~guidewire.backends.web.WebBackend` and CDP Page domain.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from guidewire.backends.router import BackendRouter, _untag
from guidewire.cdp._types import CDPTarget
from guidewire.hints import hints_for

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
    """Register the desktop.web_frame_tree tool on *mcp*.

    When *backend* is provided and is a :class:`BackendRouter` with an
    active web backend, the tool returns the iframe hierarchy.
    Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.web_frame_tree")
    def web_frame_tree(
        window_ref: str | None = None,
        target_id: str | None = None,
    ) -> str:
        """Inspect the iframe hierarchy of a browser page.

        Returns the nested frame tree showing all iframes and their
        children.  Provide either a ``window_ref`` (from web_connect)
        or a ``target_id`` (from web_list_tabs).

        Args:
            window_ref: Window reference (``w``-prefixed) from
                ``desktop.web_connect``.
            target_id: CDP target identifier for the page.  Used as
                fallback when ``window_ref`` is not provided.

        Returns:
            A JSON object with ``success``, ``tree`` (nested dict with
            ``children``), and ``frame_count`` on success, or a
            structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps({"success": True, "tree": {}, "frame_count": 0})

        # --- Resolve the BackendRouter ---
        if not isinstance(backend, BackendRouter):
            return json.dumps(
                {
                    "error": "web_frame_tree_error",
                    "message": (
                        "web_frame_tree requires a BackendRouter backend — "
                        "the server is not configured for web support"
                    ),
                    "hints": hints_for("web_frame_tree_error"),
                }
            )

        web = backend.web
        if web is None:
            return json.dumps(
                {
                    "error": "web_frame_tree_error",
                    "message": "No web connection — call desktop.web_connect first",
                    "hints": hints_for("web_frame_tree_error"),
                }
            )

        # --- Resolve target ID ---
        resolved_tid = _resolve_target_id(
            web, window_ref, target_id, ref_store
        )
        if isinstance(resolved_tid, dict):
            # It's an error response
            return json.dumps(resolved_tid)

        # --- Fetch frame tree ---
        try:
            session = web._get_or_create_session(resolved_tid)
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_frame_tree_error",
                    "message": f"Failed to create session for target: {exc}",
                    "hints": hints_for("web_frame_tree_error"),
                }
            )

        try:
            from guidewire.cdp.domains.page import PageDomain

            page = PageDomain(session)
            raw_tree = page.get_frame_tree_raw()
        except Exception as exc:
            return json.dumps(
                {
                    "error": "web_frame_tree_error",
                    "message": f"Failed to fetch frame tree: {exc}",
                    "hints": hints_for("web_frame_tree_error"),
                }
            )

        # --- Build structured frame tree ---
        tree = _build_frame_tree(raw_tree)
        flat_count = _count_frames(raw_tree)

        return json.dumps(
            {
                "success": True,
                "tree": tree,
                "frame_count": flat_count,
                "target_id": resolved_tid,
            }
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_target_id(
    web: Any,
    window_ref: str | None,
    target_id: str | None,
    ref_store: "ElementRefStore",
) -> str | dict[str, Any]:
    """Resolve either a window_ref or target_id to a CDP target ID.

    Returns:
        The target ID string, or a dict error payload.
    """
    # Try window_ref first
    if window_ref and window_ref.strip():
        tagged_handle = ref_store.resolve(window_ref)
        if tagged_handle is None:
            return {
                "error": "web_frame_tree_error",
                "message": f"Window reference '{window_ref}' not found in ref store",
                "hints": hints_for("web_frame_tree_error"),
            }

        inner, backend_id = _untag(tagged_handle)
        if backend_id != "web":
            return {
                "error": "web_frame_tree_error",
                "message": (
                    f"Window reference '{window_ref}' is not a web window "
                    f"(backend_id={backend_id!r})"
                ),
                "hints": hints_for("web_frame_tree_error"),
            }

        target = _extract_target(inner)
        if target is not None:
            return target.id

        return {
            "error": "web_frame_tree_error",
            "message": f"Could not resolve window reference '{window_ref}' to a CDP target",
            "hints": hints_for("web_frame_tree_error"),
        }

    # Fall back to target_id
    if target_id and target_id.strip():
        return target_id.strip()

    # Neither provided
    return {
        "error": "validation_error",
        "message": "Either window_ref or target_id must be provided",
        "hints": [],
    }


def _extract_target(handle: object) -> CDPTarget | None:
    """Extract a CDPTarget from a possibly-wrapped handle."""
    if isinstance(handle, CDPTarget):
        return handle
    return None


def _build_frame_tree(frame_tree: dict[str, Any]) -> dict[str, Any]:
    """Build a nested frame tree from a CDP ``Page.getFrameTree`` response.

    The CDP ``Page.getFrameTree`` response has the structure::

        {
            "frameTree": {
                "frame": { "id": ..., "url": ..., ... },
                "childFrames": [ ... ]
            }
        }

    Args:
        frame_tree: The raw ``frameTree`` dict from CDP.

    Returns:
        Nested dict with ``id``, ``url``, ``name``, ``is_main``, and
        ``children`` keys.  Each child follows the same structure.
    """
    frame = frame_tree.get("frame", {})
    result: dict[str, Any] = {
        "id": frame.get("id", ""),
        "url": frame.get("url", ""),
        "name": frame.get("name", ""),
        "is_main": True,
    }

    children: list[dict[str, Any]] = []
    for child_node in frame_tree.get("childFrames", []):
        child_tree = _build_frame_tree_nested(child_node)
        if child_tree is not None:
            children.append(child_tree)

    if children:
        result["children"] = children

    return result


def _build_frame_tree_nested(node: dict[str, Any]) -> dict[str, Any] | None:
    """Recursively build a nested frame descriptor from a CDP child frame."""
    frame = node.get("frame", {})
    frame_id = frame.get("id", "")
    if not frame_id:
        return None

    result: dict[str, Any] = {
        "id": frame_id,
        "url": frame.get("url", ""),
        "name": frame.get("name", ""),
        "is_main": False,
    }

    children: list[dict[str, Any]] = []
    for child_node in node.get("childFrames", []):
        child_tree = _build_frame_tree_nested(child_node)
        if child_tree is not None:
            children.append(child_tree)

    if children:
        result["children"] = children

    return result


def _count_frames(frame_tree: dict[str, Any]) -> int:
    """Count total frames in a CDP frame tree (including root)."""
    count = 1
    for child in frame_tree.get("childFrames", []):
        count += _count_frames(child)
    return count
