"""desktop.scroll_to_item — scroll a virtualized list to bring a target item into view.

Resolves an ``e``-prefixed short reference to a native handle via the
:class:`~pathlight_mcp.refs.ElementRefStore`, validates it through the backend,
and delegates to ``backend.scroll_to_item(container, ...)`` to navigate
virtualized lists where not all items are materialized in the accessibility
tree at once.

On Windows, uses UIA ItemContainerPattern / VirtualizedItemPattern for
efficient item lookup.  On Linux, uses a best-effort scroll-and-retry
approach via AT-SPI scroll actions.

This is a navigation operation — it does not modify data (GW-052).
"""

import json
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from pathlight_mcp.errors import (
    ActionNotSupportedError,
    ElementNotFoundError,
    StaleElementReferenceError,
)
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
    """Register the desktop.scroll_to_item tool on *mcp*.

    When *backend* is provided the tool resolves *container_ref* through
    *ref_store*, validates the handle, and delegates to
    ``backend.scroll_to_item(container, ...)`` to scroll virtualized
    lists.
    Without a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.scroll_to_item")
    def scroll_to_item(
        container_ref: str,
        item_name: str | None = None,
        item_index: int | None = None,
        max_retries: int = 10,
    ) -> str:
        """Scroll a virtualized list to bring a target item into view.

        For lists with many items (virtualized/lazy-loaded), not all items
        exist in the accessibility tree simultaneously.  This tool scrolls
        the container to find and materialize a specific item so it can be
        interacted with.

        Provide either *item_name* (case-insensitive substring match) or
        *item_index* (zero-based position) to identify the target item.

        Args:
            container_ref: Short reference handle for the list/container
                element (e.g. ``"e1"``).
            item_name: Accessible name of the target item to scroll to.
                Case-insensitive substring match.
            item_index: Zero-based index of the target item within the
                container.
            max_retries: Maximum scroll iterations for best-effort platforms
                (default 10).

        Returns:
            A JSON object with ``success``, ``ref`` of the found item,
            and metadata, or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return json.dumps(
                {
                    "success": False,
                    "container_ref": container_ref,
                    "message": "scroll_to_item not available (no backend)",
                }
            )

        # --- Input validation ---
        if not container_ref or not container_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "container_ref must be a non-empty string",
                    "ref": container_ref,
                    "hints": [],
                }
            )

        if item_name is None and item_index is None:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "Either item_name or item_index must be provided",
                    "ref": container_ref,
                    "hints": [],
                }
            )

        if item_index is not None and item_index < 0:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "item_index must be a non-negative integer",
                    "ref": container_ref,
                    "hints": [],
                }
            )

        if max_retries < 1:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "max_retries must be at least 1",
                    "ref": container_ref,
                    "hints": [],
                }
            )

        # --- Resolve reference ---
        handle = ref_store.resolve(container_ref)

        if handle is None:
            return json.dumps(
                {
                    "error": "element_not_found",
                    "message": (
                        f"Element reference '{container_ref}' not found in reference store"
                    ),
                    "ref": container_ref,
                    "hints": hints_for("element_not_found"),
                }
            )

        # --- Staleness check ---
        if not backend.is_valid(handle):
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Element reference '{container_ref}' is no longer valid"),
                    "ref": container_ref,
                    "hints": hints_for("stale_element_reference"),
                }
            )

        # --- Dispatch through scroll_to_item ---
        try:
            found_handle = backend.scroll_to_item(
                handle,
                item_name=item_name,
                item_index=item_index,
                max_retries=max_retries,
            )
        except ElementNotFoundError as exc:
            return json.dumps(
                {
                    "error": "element_not_found",
                    "message": (
                        f"Container reference '{container_ref}' not found in accessibility tree"
                    ),
                    "ref": container_ref,
                    "hints": exc.hints,
                }
            )
        except StaleElementReferenceError as exc:
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": f"Container reference '{container_ref}' is stale",
                    "ref": container_ref,
                    "hints": exc.hints,
                }
            )
        except ActionNotSupportedError as exc:
            return json.dumps(
                {
                    "error": "action_not_supported",
                    "message": str(exc),
                    "ref": container_ref,
                    "hints": exc.hints,
                }
            )

        if found_handle is None:
            return json.dumps(
                {
                    "success": False,
                    "container_ref": container_ref,
                    "message": "Target item not found after scrolling",
                }
            )

        # --- Register the found item in the ref store ---
        found_ref = ref_store.store(found_handle)

        # --- Retrieve element metadata for safety classification ---
        element_role = "list_item"
        element_name = None
        try:
            info = backend.get_element_info(found_handle)
            element_role = info["role"]
            element_name = info.get("name")
        except Exception:
            pass

        element = NormalizedElement(
            ref=found_ref,
            backend_id=str(found_handle),
            role=element_role,
            name=element_name,
            states=ElementStates(enabled=True),
        )

        # --- Safety metadata ---
        assessment = classify(element, "scroll_to_item")

        # --- Build response ---
        response: dict[str, object] = {
            "success": True,
            "container_ref": container_ref,
            "item_ref": found_ref,
            "risk": assessment.risk_level.lower(),
            "target_summary": "virtual list scroll-to-item",
        }

        if element_name is not None:
            response["item_name"] = element_name

        return json.dumps(response)
