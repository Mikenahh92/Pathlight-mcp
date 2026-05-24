"""CDP DOM domain wrapper.

Provides :class:`DOMDomain` — typed methods for the CDP ``DOM`` domain
that query and manipulate the browser's DOM tree, returning raw CDP data
as typed :class:`~guidewire.cdp._types.DOMNode` and
:class:`~guidewire.cdp._types.BoxModel` dataclasses.

Key methods:
    - :meth:`get_document` — root document node
    - :meth:`describe_node` — node metadata and children
    - :meth:`query_selector` / :meth:`query_selector_all` — CSS selector queries
    - :meth:`get_box_model` — element box model
    - :meth:`focus` / :meth:`scroll_into_view` — interaction helpers
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from guidewire.cdp._types import BoxModel, DOMNode
from guidewire.cdp.domains._base import CDPDomain

if TYPE_CHECKING:
    from guidewire.cdp.session import CDPSession

__all__ = ["DOMDomain"]

logger = logging.getLogger(__name__)


class DOMDomain(CDPDomain):
    """Typed wrapper for the CDP ``DOM`` domain.

    Queries and manipulates the browser's DOM tree, returning CDP data as
    :class:`~guidewire.cdp._types.DOMNode` and
    :class:`~guidewire.cdp._types.BoxModel` instances.

    Args:
        session: The active CDP session to send commands through.
    """

    domain = "DOM"

    def __init__(self, session: CDPSession) -> None:
        super().__init__(session)

    def get_document(self, *, depth: int = -1) -> DOMNode:
        """Fetch the root document node.

        Sends ``DOM.getDocument`` and converts the result to a
        :class:`~guidewire.cdp._types.DOMNode`.

        Args:
            depth: Maximum traversal depth (``-1`` for full tree).

        Returns:
            The root document as a :class:`DOMNode`.
        """
        result = self._send(
            self._method("getDocument"),
            {"depth": depth},
        )
        root = result.get("root", {})
        return DOMNode.from_cdp(root)

    def describe_node(
        self,
        node_id: int | None = None,
        backend_node_id: int | None = None,
        object_id: str | None = None,
        *,
        depth: int = -1,
    ) -> DOMNode:
        """Describe a DOM node and its children.

        Sends ``DOM.describeNode`` and converts the result.

        Args:
            node_id: CDP node ID.
            backend_node_id: Backend DOM node ID.
            object_id: Remote object ID.
            depth: Maximum child traversal depth.

        Returns:
            The described node as a :class:`DOMNode`.

        Raises:
            ValueError: If no identifier is provided.
        """
        params: dict[str, Any] = {"depth": depth}
        if node_id is not None:
            params["nodeId"] = node_id
        elif backend_node_id is not None:
            params["backendNodeId"] = backend_node_id
        elif object_id is not None:
            params["objectId"] = object_id
        else:
            raise ValueError("Must provide node_id, backend_node_id, or object_id")

        result = self._send(self._method("describeNode"), params)
        node = result.get("node", {})
        return DOMNode.from_cdp(node)

    def query_selector(self, node_id: int, selector: str) -> int | None:
        """Find the first element matching *selector* under *node_id*.

        Sends ``DOM.querySelector``.

        Args:
            node_id: Parent CDP node ID.
            selector: CSS selector string.

        Returns:
            The matching node's ID, or ``None`` if no match.
        """
        result = self._send(
            self._method("querySelector"),
            {"nodeId": node_id, "selector": selector},
        )
        found_id = result.get("nodeId", 0)
        return found_id if found_id else None

    def query_selector_all(self, node_id: int, selector: str) -> list[int]:
        """Find all elements matching *selector* under *node_id*.

        Sends ``DOM.querySelectorAll``.

        Args:
            node_id: Parent CDP node ID.
            selector: CSS selector string.

        Returns:
            List of matching node IDs.
        """
        result = self._send(
            self._method("querySelectorAll"),
            {"nodeId": node_id, "selector": selector},
        )
        return result.get("nodeIds", [])

    def get_box_model(
        self,
        node_id: int | None = None,
        backend_node_id: int | None = None,
        object_id: str | None = None,
    ) -> BoxModel | None:
        """Get the box model of a DOM element.

        Sends ``DOM.getBoxModel`` and returns a typed
        :class:`~guidewire.cdp._types.BoxModel`.

        Args:
            node_id: CDP node ID.
            backend_node_id: Backend DOM node ID.
            object_id: Remote object ID.

        Returns:
            The element's :class:`BoxModel`, or ``None`` if not available.

        Raises:
            ValueError: If no identifier is provided.
        """
        params: dict[str, Any] = {}
        if node_id is not None:
            params["nodeId"] = node_id
        elif backend_node_id is not None:
            params["backendNodeId"] = backend_node_id
        elif object_id is not None:
            params["objectId"] = object_id
        else:
            raise ValueError("Must provide node_id, backend_node_id, or object_id")

        result = self._send(self._method("getBoxModel"), params)
        model = result.get("model")
        if not model:
            return None
        return BoxModel.from_cdp(model)

    def focus(
        self,
        node_id: int | None = None,
        backend_node_id: int | None = None,
        object_id: str | None = None,
    ) -> None:
        """Focus a DOM element.

        Sends ``DOM.focus``.

        Args:
            node_id: CDP node ID.
            backend_node_id: Backend DOM node ID.
            object_id: Remote object ID.

        Raises:
            ValueError: If no identifier is provided.
        """
        params: dict[str, Any] = {}
        if node_id is not None:
            params["nodeId"] = node_id
        elif backend_node_id is not None:
            params["backendNodeId"] = backend_node_id
        elif object_id is not None:
            params["objectId"] = object_id
        else:
            raise ValueError("Must provide node_id, backend_node_id, or object_id")

        self._send(self._method("focus"), params)

    def scroll_into_view_if_needed(
        self,
        node_id: int | None = None,
        backend_node_id: int | None = None,
        object_id: str | None = None,
        rect: dict[str, float] | None = None,
    ) -> None:
        """Scroll the element into view if needed.

        Sends ``DOM.scrollIntoViewIfNeeded``.

        Args:
            node_id: CDP node ID.
            backend_node_id: Backend DOM node ID.
            object_id: Remote object ID.
            rect: Optional rect to scroll into view within the element.
        """
        params: dict[str, Any] = {}
        if node_id is not None:
            params["nodeId"] = node_id
        elif backend_node_id is not None:
            params["backendNodeId"] = backend_node_id
        elif object_id is not None:
            params["objectId"] = object_id
        else:
            raise ValueError("Must provide node_id, backend_node_id, or object_id")

        if rect is not None:
            params["rect"] = rect

        self._send(self._method("scrollIntoViewIfNeeded"), params)

    def resolve_node(
        self,
        node_id: int | None = None,
        backend_node_id: int | None = None,
        object_group: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a DOM node to a remote object.

        Sends ``DOM.resolveNode``.

        Args:
            node_id: CDP node ID.
            backend_node_id: Backend DOM node ID.
            object_group: Optional group name for the remote object.

        Returns:
            The remote object descriptor.
        """
        params: dict[str, Any] = {}
        if node_id is not None:
            params["nodeId"] = node_id
        elif backend_node_id is not None:
            params["backendNodeId"] = backend_node_id
        else:
            raise ValueError("Must provide node_id or backend_node_id")

        if object_group is not None:
            params["objectGroup"] = object_group

        result = self._send(self._method("resolveNode"), params)
        return result.get("object", {})
