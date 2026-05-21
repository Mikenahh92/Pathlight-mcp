"""desktop.snapshot — capture an accessibility snapshot of a window's UI tree.

Resolves a ``w``-prefixed short reference to a native handle via the
:class:`~guidewire.refs.ElementRefStore`, validates it through the backend,
calls :meth:`~guidewire.backends.base.DesktopBackend.snapshot`, normalizes
the raw tree dict into :class:`~guidewire.models.NormalizedElement` instances,
assigns ``e``-prefixed refs to all elements, applies privacy redaction,
and returns a nested tree with safety metadata (PRD R4, §6.3).

Handler flow (architecture v2 §6.2):

    resolve → validate → delegate → normalize → ref-assign → redact → respond
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from guidewire.errors import StaleElementReferenceError, WindowNotFoundError
from guidewire.hints import hints_for
from guidewire.models import Bounds, DesktopAction, ElementStates, NormalizedElement
from guidewire.privacy import PrivacyConfig, redact_snapshot
from guidewire.safety import classify

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

logger = logging.getLogger(__name__)


def _dict_to_element(data: dict[str, Any]) -> NormalizedElement:
    """Convert a raw backend tree dict into a NormalizedElement.

    Handles the schema mismatch between backend types (int bounds) and
    NormalizedElement (float bounds), and maps raw state dicts to
    ElementStates.
    """
    states_data = data.get("states", {})
    if isinstance(states_data, list):
        states_data = {}

    # Normalize boolean states from ElementState (9-field) format
    states = ElementStates(
        enabled=states_data.get("enabled"),
        focused=states_data.get("focused"),
        selected=states_data.get("selected"),
        checked=states_data.get("checked"),
        expanded=states_data.get("expanded"),
        visible=states_data.get("visible"),
        offscreen=states_data.get("offscreen"),
        read_only=states_data.get("read_only"),
        required=states_data.get("required"),
        is_password=states_data.get("is_password"),
    )

    bounds_data = data.get("bounds")
    bounds: Bounds | None = None
    if bounds_data and isinstance(bounds_data, dict):
        bounds = Bounds(
            x=float(bounds_data.get("x", 0)),
            y=float(bounds_data.get("y", 0)),
            width=float(bounds_data.get("width", 0)),
            height=float(bounds_data.get("height", 0)),
        )

    actions_raw = data.get("actions", [])
    actions: list[DesktopAction] = []
    for a in actions_raw:
        if isinstance(a, str):
            actions.append(a)
        else:
            actions.append(str(a))

    children_raw = data.get("children", [])
    children: list[NormalizedElement] | None = None
    if children_raw:
        children = [_dict_to_element(c) for c in children_raw]

    return NormalizedElement(
        ref=data.get("ref", ""),
        backend_id=data.get("backend_id", data.get("ref", "")),
        role=data.get("role", "unknown"),
        name=data.get("name"),
        description=data.get("description"),
        value=data.get("value"),
        text=data.get("text"),
        states=states,
        bounds=bounds,
        actions=actions,
        children=children,
    )


def _assign_refs(element: NormalizedElement, ref_store: "ElementRefStore") -> None:
    """Walk the element tree and assign e-prefixed refs via ref_store.

    The root window element keeps its existing ref (w-prefixed). All
    descendants get new e-prefixed refs, and their backend_id values
    are preserved for later resolution.
    """
    for child in element.children or []:
        _assign_refs_recursive(child, ref_store)


def _assign_refs_recursive(element: NormalizedElement, ref_store: "ElementRefStore") -> None:
    """Recursively assign refs to an element and its children."""
    ref = ref_store.store(element.backend_id, prefix="e")
    element.ref = ref
    for child in element.children or []:
        _assign_refs_recursive(child, ref_store)


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.snapshot tool on *mcp*.

    When *backend* is provided the tool resolves *window_ref* through
    *ref_store*, validates the handle, calls ``backend.snapshot()``,
    normalizes to NormalizedElement tree, assigns e-prefixed refs,
    applies privacy redaction, and returns structured JSON.  Without
    a backend it returns a static stub response.
    """

    @mcp.tool(name="desktop.snapshot")
    def snapshot(
        window_ref: str,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> str:
        """Capture an accessibility snapshot of a window's UI tree.

        Returns a depth-limited tree of accessibility elements suitable for
        LLM consumption (PRD §5.3).

        Args:
            window_ref: Short reference handle for the target window
                (e.g. ``"w1"``).
            max_depth: Maximum tree depth to traverse (default 4).
            max_nodes: Maximum number of nodes to include (default 500).

        Returns:
            A JSON object with ``tree`` (the root element dict),
            ``risk``, ``target_summary``, ``element_count``, and
            ``max_depth_reached`` on success, or a structured error
            payload on failure.
        """
        if backend is None or ref_store is None:
            return (
                '{"ref":"w1","role":"window","name":"","states":[],'
                '"bounds":{"x":0,"y":0,"width":0,"height":0},'
                '"actions":[],"children":[]}'
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

        # --- Staleness check ---
        if not backend.is_valid(handle):
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (f"Window reference '{window_ref}' is no longer valid"),
                    "ref": window_ref,
                    "hints": hints_for("stale_element_reference"),
                }
            )

        # --- Call backend snapshot with structured error handling ---
        raw_tree: dict[str, Any]
        try:
            raw_tree = backend.snapshot(handle, max_depth=max_depth, max_nodes=max_nodes)
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

        # --- Normalize to NormalizedElement tree ---
        root = _dict_to_element(raw_tree)
        root.ref = window_ref

        # --- Assign e-prefixed refs to all descendants ---
        # Clear previous element refs but re-register the window ref
        # so it remains resolvable for subsequent calls.
        ref_store.clear()
        ref_store.store(handle, prefix="w")
        _assign_refs(root, ref_store)

        # --- Apply privacy redaction ---
        elements = [root]
        redacted = redact_snapshot(elements, config=PrivacyConfig())

        # --- Safety metadata ---
        assessment = classify(root, "snapshot")

        # --- Build response ---
        tree_dict = redacted[0].to_dict()
        all_elements = redacted[0].walk()
        element_count = len(all_elements)

        return json.dumps(
            {
                "tree": tree_dict,
                "risk": assessment.risk_level.lower(),
                "target_summary": f"window snapshot ({element_count} elements)",
                "element_count": element_count,
                "max_depth_reached": max_depth,
            }
        )
