"""Tests for CDP Accessibility domain wrapper.

Validates :class:`AccessibilityDomain` — full tree, partial tree, and
query operations, plus CDP AX node → :class:`AXNode` conversion.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from pathlight_mcp.cdp._types import AXNode
from pathlight_mcp.cdp.domains.accessibility import AccessibilityDomain

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_session(responses: list[dict[str, Any]] | None = None) -> MagicMock:
    """Create a mock session that returns preset responses."""
    session = MagicMock()
    responses = responses or [{}]
    session.send_command.side_effect = responses
    return session


def _ax_node(
    *,
    node_id: str = "node-1",
    role: str = "button",
    name: str = "Click Me",
    value: str | None = None,
    backend_dom_node_id: int = 42,
    child_ids: list[str] | None = None,
    children: list[dict[str, Any]] | None = None,
    bounds: dict[str, float] | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a CDP AX node dict."""
    node: dict[str, Any] = {
        "nodeId": node_id,
        "role": {"type": "role", "value": role},
        "name": {"type": "string", "value": name},
        "backendDOMNodeId": backend_dom_node_id,
    }
    if value is not None:
        node["value"] = {"type": "string", "value": value}
    if child_ids is not None:
        node["childIds"] = child_ids
    if children is not None:
        node["children"] = children
    if bounds is not None:
        node["bounds"] = bounds
    if properties is not None:
        node["properties"] = properties
    return node


# ---------------------------------------------------------------------------
# AXNode.from_cdp tests
# ---------------------------------------------------------------------------


class TestAXNodeFromCdp:
    """Tests for AXNode.from_cdp conversion."""

    def test_basic_conversion(self) -> None:
        data = _ax_node(role="button", name="Submit")
        ax = AXNode.from_cdp(data)

        assert isinstance(ax, AXNode)
        assert ax.role == "button"
        assert ax.name == "Submit"
        assert ax.node_id == "node-1"

    def test_bounds_parsing(self) -> None:
        data = _ax_node(bounds={"x": 10.0, "y": 20.0, "width": 100.0, "height": 30.0})
        ax = AXNode.from_cdp(data)

        assert ax.bounds is not None
        assert ax.bounds["x"] == 10.0
        assert ax.bounds["y"] == 20.0
        assert ax.bounds["width"] == 100.0
        assert ax.bounds["height"] == 30.0

    def test_no_bounds(self) -> None:
        data = _ax_node()
        ax = AXNode.from_cdp(data)
        assert ax.bounds is None

    def test_backend_dom_node_id(self) -> None:
        data = _ax_node(backend_dom_node_id=99)
        ax = AXNode.from_cdp(data)
        assert ax.backend_dom_node_id == 99

    def test_value_converted_to_string(self) -> None:
        data = _ax_node(value="current-value")
        ax = AXNode.from_cdp(data)
        assert ax.value == "current-value"

    def test_child_ids(self) -> None:
        data = _ax_node(child_ids=["child-1", "child-2"])
        ax = AXNode.from_cdp(data)
        assert ax.child_ids == ("child-1", "child-2")

    def test_properties_from_dict(self) -> None:
        data = _ax_node(properties={"disabled": True, "focused": False})
        ax = AXNode.from_cdp(data)
        assert ax.properties is not None
        assert ax.properties["disabled"] is True
        assert ax.properties["focused"] is False

    def test_properties_from_list(self) -> None:
        data = _ax_node(
            properties=[
                {"name": "disabled", "value": True},
                {"name": "focused", "value": True},
            ]
        )
        ax = AXNode.from_cdp(data)
        assert ax.properties is not None
        assert ax.properties["disabled"] is True
        assert ax.properties["focused"] is True

    def test_raw_preserved(self) -> None:
        data = _ax_node(role="button")
        ax = AXNode.from_cdp(data)
        assert ax.raw == data

    def test_frozen(self) -> None:
        data = _ax_node()
        ax = AXNode.from_cdp(data)
        with pytest.raises(AttributeError):
            ax.role = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AccessibilityDomain method tests
# ---------------------------------------------------------------------------


class TestAccessibilityDomainGetFullAxTree:
    """Tests for get_full_ax_tree."""

    def test_returns_ax_nodes(self) -> None:
        response = {
            "nodes": [
                _ax_node(
                    node_id="root",
                    role="rootWebArea",
                    name="Page",
                    child_ids=["child-1"],
                ),
                _ax_node(
                    node_id="child-1",
                    role="button",
                    name="Click",
                ),
            ]
        }
        session = _make_session([response])
        domain = AccessibilityDomain(session)

        nodes = domain.get_full_ax_tree()

        assert len(nodes) == 2
        assert all(isinstance(n, AXNode) for n in nodes)
        root = next(n for n in nodes if n.node_id == "root")
        assert root.role == "rootWebArea"
        assert root.child_ids == ("child-1",)

    def test_empty_tree(self) -> None:
        session = _make_session([{"nodes": []}])
        domain = AccessibilityDomain(session)
        assert domain.get_full_ax_tree() == []

    def test_sends_correct_method(self) -> None:
        session = _make_session([{"nodes": []}])
        domain = AccessibilityDomain(session)
        domain.get_full_ax_tree()
        session.send_command.assert_called_once_with(
            "Accessibility.getFullAXTree", None, timeout=None
        )


class TestAccessibilityDomainGetPartialAxTree:
    """Tests for get_partial_ax_tree."""

    def test_sends_backend_node_id(self) -> None:
        session = _make_session([{"nodes": []}])
        domain = AccessibilityDomain(session)
        domain.get_partial_ax_tree(node_id=42)
        session.send_command.assert_called_once_with(
            "Accessibility.getPartialAXTree",
            {"backendNodeId": 42},
            timeout=None,
        )

    def test_returns_ax_nodes(self) -> None:
        response = {"nodes": [_ax_node(role="button", name="Partial")]}
        session = _make_session([response])
        domain = AccessibilityDomain(session)

        nodes = domain.get_partial_ax_tree(node_id=10)
        assert len(nodes) == 1
        assert isinstance(nodes[0], AXNode)
        assert nodes[0].role == "button"


class TestAccessibilityDomainQueryAxTree:
    """Tests for query_ax_tree."""

    def test_query_by_role(self) -> None:
        session = _make_session([{"nodes": [_ax_node(role="button", name="Found")]}])
        domain = AccessibilityDomain(session)

        nodes = domain.query_ax_tree(role="button")
        assert len(nodes) == 1
        assert isinstance(nodes[0], AXNode)

        call_args = session.send_command.call_args
        assert call_args[0][0] == "Accessibility.queryAXTree"
        assert call_args[0][1] == {"role": "button"}

    def test_query_by_name(self) -> None:
        session = _make_session([{"nodes": []}])
        domain = AccessibilityDomain(session)

        domain.query_ax_tree(accessible_name="Submit")

        call_args = session.send_command.call_args
        assert call_args[0][1] == {"accessibleName": "Submit"}

    def test_query_all_filters(self) -> None:
        session = _make_session([{"nodes": []}])
        domain = AccessibilityDomain(session)

        domain.query_ax_tree(
            node_id=5, accessible_name="Search", role="button"
        )

        call_args = session.send_command.call_args
        params = call_args[0][1]
        assert params["nodeId"] == 5
        assert params["accessibleName"] == "Search"
        assert params["role"] == "button"

    def test_query_no_filters_sends_none_params(self) -> None:
        session = _make_session([{"nodes": []}])
        domain = AccessibilityDomain(session)

        domain.query_ax_tree()

        call_args = session.send_command.call_args
        assert call_args[0][1] is None
