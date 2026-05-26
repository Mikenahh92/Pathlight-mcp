"""CDP Accessibility domain wrapper.

Provides :class:`AccessibilityDomain` — typed methods for the CDP
``Accessibility`` domain that query the browser's accessibility tree
and return raw CDP data as typed :class:`~guidewire.cdp._types.AXNode`
dataclasses.

Key methods:
    - :meth:`get_full_ax_tree` — full accessibility tree
    - :meth:`get_partial_ax_tree` — subtree rooted at a specific node
    - :meth:`query_ax_tree` — find accessible nodes matching criteria
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from guidewire.cdp._types import AXNode
from guidewire.cdp.domains._base import CDPDomain

if TYPE_CHECKING:
    from guidewire.cdp.session import CDPSession

__all__ = ["AccessibilityDomain"]

logger = logging.getLogger(__name__)


class AccessibilityDomain(CDPDomain):
    """Typed wrapper for the CDP ``Accessibility`` domain.

    Queries the browser's accessibility tree and returns CDP data as
    :class:`~guidewire.cdp._types.AXNode` instances.

    Args:
        session: The active CDP session to send commands through.
    """

    domain = "Accessibility"

    def __init__(self, session: CDPSession) -> None:
        super().__init__(session)

    def get_full_ax_tree(self, *, timeout: float | None = None) -> list[AXNode]:
        """Fetch the full accessibility tree from the browser.

        Sends ``Accessibility.getFullAXTree`` and converts each CDP AX
        node to an :class:`~guidewire.cdp._types.AXNode`.

        Args:
            timeout: Per-command timeout in seconds.  Defaults to the
                session / connection default.

        Returns:
            List of all AX nodes in the tree.
        """
        result = self._send(self._method("getFullAXTree"), timeout=timeout)
        nodes = result.get("nodes", [])
        return [AXNode.from_cdp(n) for n in nodes]

    def get_partial_ax_tree(self, node_id: int) -> list[AXNode]:
        """Fetch a partial accessibility tree rooted at *node_id*.

        Sends ``Accessibility.getPartialAXTree`` for the given backend
        DOM node ID.

        Args:
            node_id: The backend DOM node ID to root the subtree at.

        Returns:
            List of AX nodes in the subtree.
        """
        result = self._send(
            self._method("getPartialAXTree"),
            {"backendNodeId": node_id},
        )
        nodes = result.get("nodes", [])
        return [AXNode.from_cdp(n) for n in nodes]

    def query_ax_tree(
        self,
        *,
        node_id: int | None = None,
        accessible_name: str | None = None,
        role: str | None = None,
    ) -> list[AXNode]:
        """Query the accessibility tree for nodes matching criteria.

        Sends ``Accessibility.queryAXTree`` with optional filters.

        Args:
            node_id: Limit search to descendants of this DOM node.
            accessible_name: Match nodes with this accessible name.
            role: Match nodes with this AX role.

        Returns:
            List of matching AX nodes.
        """
        params: dict[str, Any] = {}
        if node_id is not None:
            params["nodeId"] = node_id
        if accessible_name is not None:
            params["accessibleName"] = accessible_name
        if role is not None:
            params["role"] = role

        result = self._send(self._method("queryAXTree"), params or None)
        nodes = result.get("nodes", [])
        return [AXNode.from_cdp(n) for n in nodes]
