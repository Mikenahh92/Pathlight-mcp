"""desktop.get_tree_info — query tree view structure and node expand/collapse state.

Resolves an ``e``-prefixed short reference to a native handle via the
:class:`~guidewire.refs.ElementRefStore`, validates it through the backend,
queries the element's info to extract expand/collapse state, traverses the
tree hierarchy via ``backend.snapshot()``, and returns structured JSON with
children array, node_count, tree_level, max_depth fields (architecture v2 §6).

Additive tool — no ABC changes needed.  Expand/collapse actions flow through
the existing ``perform_action`` dispatch with ``EXPAND`` / ``COLLAPSE``
DesktopAction variants.

Handler flow (element-action tier):

    resolve → validate → staleness → backend info → role check →
    snapshot traverse → privacy redact → safety → JSON
"""

import json
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from guidewire.errors import (
    ElementNotFoundError,
    StaleElementReferenceError,
)
from guidewire.hints import hints_for
from guidewire.models import ElementStates, NormalizedElement
from guidewire.privacy import redact_element
from guidewire.safety import classify

if TYPE_CHECKING:
    from guidewire.backends.base import DesktopBackend
    from guidewire.refs import ElementRefStore

# Roles that represent tree-structured UI elements.
_VALID_TREE_ROLES: frozenset[str] = frozenset({"tree", "tree_item", "outline"})


def _count_nodes(node: dict[str, Any]) -> int:
    """Recursively count nodes in a tree dict."""
    count = 1
    for child in node.get("children", []):
        count += _count_nodes(child)
    return count


def _extract_tree_subtree(
    snapshot_tree: dict[str, Any],
    target_handle: Any,
) -> dict[str, Any] | None:
    """Walk the snapshot tree to find the sub-tree rooted at *target_handle*.

    Searches the snapshot tree (which has ``ref`` fields set to native handles
    from the backend) for a node whose ``ref`` equals *target_handle*, then
    returns that entire sub-tree dict.  Returns ``None`` if not found.
    """
    if str(snapshot_tree.get("ref")) == str(target_handle):
        return snapshot_tree
    for child in snapshot_tree.get("children", []):
        result = _extract_tree_subtree(child, target_handle)
        if result is not None:
            return result
    return None


def _build_node_entry(
    node: dict[str, Any],
    ref_store: "ElementRefStore",
    depth: int = 0,
    max_depth: int = 4,
) -> dict[str, Any] | None:
    """Build a serializable tree-node dict from a snapshot sub-tree node.

    Each node includes: ``ref``, ``role``, ``name``, ``expanded``,
    ``expandable``, ``tree_level``, and optionally ``children``.

    Returns ``None`` when *max_depth* is exceeded.
    """
    raw_ref = node.get("ref")
    ref = ref_store.store(raw_ref, prefix="e") if raw_ref is not None else ""

    role = node.get("role", "unknown")
    name = node.get("name")
    states_raw = node.get("states", {})
    if isinstance(states_raw, list):
        states_raw = {}
    expanded = states_raw.get("expanded", False)
    actions_raw = node.get("actions", [])
    has_expand = "expand" in actions_raw
    has_collapse = "collapse" in actions_raw
    expandable = has_expand or has_collapse

    # Build NormalizedElement for privacy redaction
    element = NormalizedElement(
        ref=ref,
        backend_id=str(raw_ref) if raw_ref is not None else "",
        role=role,
        name=name,
        states=ElementStates(
            enabled=states_raw.get("enabled"),
            expanded=expanded,
        ),
    )
    redacted = redact_element(element)

    entry: dict[str, Any] = {
        "ref": ref,
        "role": role,
        "name": redacted.name,
        "expanded": bool(expanded),
        "expandable": expandable,
        "tree_level": depth,
    }

    if depth < max_depth:
        children_raw = node.get("children", [])
        if children_raw:
            children = []
            for child in children_raw:
                child_entry = _build_node_entry(child, ref_store, depth + 1, max_depth)
                if child_entry is not None:
                    children.append(child_entry)
            entry["children"] = children

    return entry


def register(
    mcp: FastMCP,
    *,
    backend: "DesktopBackend | None" = None,
    ref_store: "ElementRefStore | None" = None,
) -> None:
    """Register the desktop.get_tree_info tool on *mcp*.

    When *backend* is provided the tool resolves *element_ref* through
    *ref_store*, validates the handle, retrieves element info to extract
    expand/collapse state and tree metadata, traverses the tree hierarchy
    via snapshot, and returns structured JSON.  Without a backend it returns
    a static stub response.
    """

    @mcp.tool(name="desktop.get_tree_info")
    def get_tree_info(
        element_ref: str,
        window_ref: str | None = None,
        max_depth: int = 4,
    ) -> str:
        """Query tree view structure and node expand/collapse state.

        Given a reference to a tree or tree-item element, returns the
        node's name, role, expanded state, and full child hierarchy up
        to *max_depth* levels.

        Args:
            element_ref: Short reference handle for the target tree element
                (e.g. ``"e1"``).  Must be an ``e``-prefixed reference.
            window_ref: Optional short reference handle for the parent window
                (e.g. ``"w1"``).  Used to scope the tree traversal when the
                element's window is known.
            max_depth: Maximum depth of child hierarchy to traverse
                (default 4).

        Returns:
            A JSON object with ``success``, ``ref``, ``role``, ``name``,
            ``expanded``, ``expandable``, ``children``, ``node_count``,
            ``tree_level``, ``max_depth``, ``risk``, and ``target_summary``
            on success, or a structured error payload on failure.
        """
        if backend is None or ref_store is None:
            return f"Tree info for {element_ref}"

        # --- Input validation ---
        if not element_ref or not element_ref.strip():
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": "element_ref must be a non-empty string",
                    "ref": element_ref,
                    "hints": [],
                }
            )

        # --- Resolve reference ---
        handle = ref_store.resolve(element_ref)

        if handle is None:
            return json.dumps(
                {
                    "error": "element_not_found",
                    "message": (
                        f"Element reference '{element_ref}' not found in reference store"
                    ),
                    "ref": element_ref,
                    "hints": hints_for("element_not_found"),
                }
            )

        # --- Staleness check ---
        if not backend.is_valid(handle):
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (
                        f"Element reference '{element_ref}' is no longer valid"
                    ),
                    "ref": element_ref,
                    "hints": hints_for("stale_element_reference"),
                }
            )

        # --- Retrieve element info with structured error handling ---
        try:
            info = backend.get_element_info(handle)
        except ElementNotFoundError as exc:
            return json.dumps(
                {
                    "error": "element_not_found",
                    "message": (
                        f"Element reference '{element_ref}' not found in accessibility tree"
                    ),
                    "ref": element_ref,
                    "hints": exc.hints,
                }
            )
        except StaleElementReferenceError as exc:
            return json.dumps(
                {
                    "error": "stale_element_reference",
                    "message": (
                        f"Element reference '{element_ref}' is stale"
                    ),
                    "ref": element_ref,
                    "hints": exc.hints,
                }
            )

        # --- Extract tree-relevant metadata ---
        element_role = info.get("role", "unknown")
        element_name = info.get("name")
        states_raw = info.get("states", {})
        if isinstance(states_raw, list):
            states_raw = {}
        expanded = states_raw.get("expanded", False)

        # Determine expandability from available actions
        actions_raw = info.get("actions", [])
        has_expand = "expand" in actions_raw
        has_collapse = "collapse" in actions_raw
        expandable = has_expand or has_collapse

        # --- Tree role validation ---
        if element_role not in _VALID_TREE_ROLES:
            return json.dumps(
                {
                    "error": "validation_error",
                    "message": (
                        f"Element role '{element_role}' is not a valid tree element. "
                        f"Expected one of: {', '.join(sorted(_VALID_TREE_ROLES))}"
                    ),
                    "ref": element_ref,
                    "role": element_role,
                    "hints": [],
                }
            )

        # --- Determine window handle for snapshot traversal ---
        window_handle = None
        if window_ref is not None:
            window_handle = ref_store.resolve(window_ref)
        # Fallback: if no window_ref, scan registered refs for a w-prefixed one
        if window_handle is None:
            for candidate_ref, candidate_handle in ref_store._ref_to_handle.items():  # type: ignore[attr-defined]
                if candidate_ref.startswith("w") and backend.is_valid(candidate_handle):
                    window_handle = candidate_handle
                    break

        # --- Build tree hierarchy via snapshot ---
        children: list[dict[str, Any]] = []
        node_count = 1
        actual_max_depth = 0

        if window_handle is not None:
            try:
                snapshot_tree = backend.snapshot(
                    window_handle, max_depth=max_depth + 1, max_nodes=500,
                )
                subtree = _extract_tree_subtree(snapshot_tree, handle)
                if subtree is not None:
                    # Build children from the subtree
                    raw_children = subtree.get("children", [])
                    for child_node in raw_children:
                        child_entry = _build_node_entry(
                            child_node, ref_store, depth=1, max_depth=max_depth,
                        )
                        if child_entry is not None:
                            children.append(child_entry)
                    node_count = _count_nodes(subtree)

                    # Determine actual max depth
                    actual_max_depth = _compute_max_depth(subtree, 0)
            except Exception:
                pass  # Fall back to flat info if snapshot fails

        # --- Compute tree_level from depth in hierarchy ---
        tree_level = 0
        if window_handle is not None:
            try:
                snapshot_tree = backend.snapshot(window_handle, max_depth=10, max_nodes=500)
                level = _find_element_level(snapshot_tree, handle, 0)
                if level is not None:
                    tree_level = level
            except Exception:
                pass

        # --- Safety metadata ---
        element = NormalizedElement(
            ref=element_ref,
            backend_id=str(handle),
            role=element_role,
            name=element_name,
            states=ElementStates(
                enabled=states_raw.get("enabled"),
                expanded=expanded,
                selected=states_raw.get("selected"),
            ),
        )
        assessment = classify(element, "get_tree_info")

        # --- Privacy redaction on top-level name ---
        redacted_element = redact_element(element)

        return json.dumps(
            {
                "success": True,
                "ref": element_ref,
                "role": element_role,
                "name": redacted_element.name,
                "expanded": bool(expanded),
                "expandable": expandable,
                "children": children,
                "node_count": node_count,
                "tree_level": tree_level,
                "max_depth": actual_max_depth,
                "risk": assessment.risk_level.lower(),
                "target_summary": f"tree info for {element_role} element",
            }
        )


def _compute_max_depth(node: dict[str, Any], current_depth: int) -> int:
    """Compute the maximum depth of a tree dict."""
    children = node.get("children", [])
    if not children:
        return current_depth
    return max(_compute_max_depth(child, current_depth + 1) for child in children)


def _find_element_level(
    node: dict[str, Any],
    target_handle: Any,
    current_level: int,
) -> int | None:
    """Find the tree_level (depth from window root) of a target handle."""
    if str(node.get("ref")) == str(target_handle):
        return current_level
    for child in node.get("children", []):
        result = _find_element_level(child, target_handle, current_level + 1)
        if result is not None:
            return result
    return None
