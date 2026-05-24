"""Web-specific AX normalization helpers.

Extracts CDP Accessibility tree normalization logic from
:class:`~guidewire.backends.web.WebBackend` into reusable, testable
functions.  These helpers convert raw CDP AX node data into
:class:`~guidewire.models.NormalizedElement` trees using the ``"web"``
platform mapping tables.

Usage::

    from guidewire.backends.web_normalize import (
        build_normalized_tree,
        find_root_ax_node,
        infer_ax_actions,
        fetch_bounds_from_dom,
    )
"""

import logging
from typing import Any

from guidewire.backends.normalize import normalize_element
from guidewire.cdp._types import AXNode
from guidewire.cdp.domains.dom import DOMDomain
from guidewire.models import NormalizedElement

__all__ = [
    "build_normalized_tree",
    "fetch_bounds_from_dom",
    "find_root_ax_node",
    "infer_ax_actions",
    "is_virtualized_container",
]

logger = logging.getLogger(__name__)


def find_root_ax_node(ax_nodes: list[AXNode]) -> AXNode | None:
    """Find the root AX node (webArea or first node without a parent).

    The root is the node that is not listed as a child of any other node.

    Args:
        ax_nodes: Flat list of AX nodes from ``getFullAXTree``.

    Returns:
        The root :class:`AXNode`, or ``None``.
    """
    # Collect all child IDs to find the node that is nobody's child
    child_ids: set[str] = set()
    for node in ax_nodes:
        child_ids.update(node.child_ids)

    # Find a root node — prefer webArea role
    web_area: AXNode | None = None
    first_no_parent: AXNode | None = None

    for node in ax_nodes:
        if node.node_id not in child_ids:
            if node.role == "webArea":
                web_area = node
                break
            if first_no_parent is None:
                first_no_parent = node

    return web_area or first_no_parent


def fetch_bounds_from_dom(dom: DOMDomain, backend_dom_node_id: int) -> dict[str, float] | None:
    """Fetch bounds for an AX node via the DOM domain.

    Args:
        dom: The :class:`DOMDomain` instance.
        backend_dom_node_id: The backend DOM node ID.

    Returns:
        Bounds dict ``{x, y, width, height}`` or ``None``.
    """
    try:
        box_model = dom.get_box_model(backend_node_id=backend_dom_node_id)
        if box_model is not None:
            bounds_tuple = box_model.bounds
            if bounds_tuple is not None:
                x, y, w, h = bounds_tuple
                return {"x": x, "y": y, "width": w, "height": h}
    except Exception:
        logger.debug(
            "Failed to fetch bounds for backendDOMNode %d",
            backend_dom_node_id,
            exc_info=True,
        )
    return None


def is_virtualized_container(node: AXNode) -> bool:
    """Detect whether an AX node represents a virtualized list or grid.

    Virtualized containers (React Window, AG Grid, etc.) expose ARIA
    heuristics that indicate more items exist than are currently rendered.
    Detection uses:
    - ``aria-rowcount`` (tables/grids with more rows than rendered children)
    - ``aria-setsize`` (listboxes with more items than rendered children)

    Args:
        node: An :class:`AXNode`.

    Returns:
        ``True`` if the node appears to be a virtualized container.
    """
    props = node.properties or {}
    role = (node.role or "").lower()

    # Table-like roles with aria-rowcount
    if role in ("table", "grid", "treegrid"):
        rowcount = props.get("rowcount")
        if rowcount is not None:
            try:
                total_rows = int(rowcount)
                rendered_children = len(node.child_ids)
                # Virtualized if reported total exceeds rendered children
                if total_rows > rendered_children:
                    return True
            except (TypeError, ValueError):
                pass

    # List-like roles with aria-setsize
    if role in ("listbox", "list", "tree"):
        setsize = props.get("setsize")
        if setsize is not None:
            try:
                total_items = int(setsize)
                rendered_children = len(node.child_ids)
                if total_items > rendered_children:
                    return True
            except (TypeError, ValueError):
                pass

    return False


def infer_ax_actions(node: AXNode) -> list[str]:
    """Infer supported actions for an AX node based on its role and properties.

    Web elements support actions based on ARIA role semantics.

    Args:
        node: An :class:`AXNode`.

    Returns:
        List of raw action strings.
    """
    actions: list[str] = []
    role = (node.role or "").lower()
    props = node.properties or {}

    # Interactive roles always support click
    interactive_roles = {
        "button",
        "link",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "tab",
        "option",
        "treeitem",
        "gridcell",
        "checkbox",
        "radio",
        "switch",
        "summary",
    }
    if role in interactive_roles:
        actions.append("click")

    # Focusable elements
    if props.get("focusable") is True:
        actions.append("focus")

    # Text input roles
    if role in ("textbox", "combobox", "searchbox"):
        actions.append("type")
        actions.append("set_value")

    # Expandable elements
    if "expanded" in props:
        actions.extend(["expand", "collapse"])

    # Checkable elements
    if "checked" in props:
        actions.append("toggle")

    # Selectable elements
    if props.get("selected") is not None:
        actions.append("select")

    # Scrollable elements
    scrollable_roles = {"scrollbar", "slider", "spinbutton"}
    if role in scrollable_roles:
        actions.append("scroll")
        actions.extend(["increment", "decrement"])

    # Range / value roles
    if role in ("slider", "spinbutton", "progressbar"):
        actions.extend(["increment", "decrement", "set_value"])

    # Virtualized containers support scroll_to_item
    if is_virtualized_container(node):
        if "scroll" not in actions:
            actions.append("scroll")
        actions.append("scroll_to_item")

    return actions


def build_normalized_tree(
    node: AXNode,
    depth: int,
    max_depth: int,
    counter: list[int],
    max_nodes: int,
    dom: DOMDomain,
    ax_cache: dict[str, AXNode],
) -> NormalizedElement | None:
    """Recursively build a NormalizedElement tree from AX nodes.

    Args:
        node: The current :class:`AXNode`.
        depth: Current tree depth.
        max_depth: Maximum allowed depth.
        counter: Mutable [count] for node tracking.
        max_nodes: Maximum total nodes.
        dom: :class:`DOMDomain` for lazy bounds fetching.
        ax_cache: AX node cache for looking up children.

    Returns:
        A :class:`NormalizedElement` tree, or ``None`` if limit exceeded.
    """
    if counter[0] >= max_nodes:
        return None

    counter[0] += 1

    # Extract properties for normalization
    raw_states: dict[str, Any] = {}
    if node.properties:
        raw_states.update(node.properties)

    # Bounds: prefer inline bounds from AX node, fall back to DOM
    bounds: Any = node.bounds
    if bounds is None and node.backend_dom_node_id is not None:
        bounds = fetch_bounds_from_dom(dom, node.backend_dom_node_id)

    # Raw actions: derive from CDP AX node properties
    raw_actions = infer_ax_actions(node)

    # Normalize the node
    normalized = normalize_element(
        platform="web",
        ref=node.node_id,
        backend_id=node.node_id,
        role=node.role or "generic",
        native_role=node.role,
        name=node.name,
        description=node.description,
        value=node.value,
        text=None,
        raw_states=raw_states,
        bounds=bounds,
        raw_actions=raw_actions,
    )

    # Mark virtualized containers (GW-101)
    if is_virtualized_container(node):
        normalized.is_virtualized = True

    # Recurse into children
    if depth < max_depth and node.child_ids:
        children: list[NormalizedElement] = []
        for child_id in node.child_ids:
            if counter[0] >= max_nodes:
                break
            child_node = ax_cache.get(child_id)
            if child_node is None:
                continue
            child_elem = build_normalized_tree(
                child_node, depth + 1, max_depth, counter, max_nodes, dom, ax_cache
            )
            if child_elem is not None:
                children.append(child_elem)
        normalized.children = children

    return normalized
