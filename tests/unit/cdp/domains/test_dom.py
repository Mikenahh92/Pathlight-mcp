"""Tests for CDP DOM domain wrapper.

Validates :class:`DOMDomain` — get_document, describe_node, query_selector,
get_box_model, focus, and scroll_into_view, plus CDP DOM node →
:class:`DOMNode` and :class:`BoxModel` conversion.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from guidewire.cdp._types import BoxModel, DOMNode
from guidewire.cdp.domains.dom import DOMDomain

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(responses: list[dict[str, Any]] | None = None) -> MagicMock:
    session = MagicMock()
    responses = responses or [{}]
    session.send_command.side_effect = responses
    return session


def _dom_node(
    *,
    node_id: int = 1,
    node_name: str = "DIV",
    backend_node_id: int = 100,
    children: list[dict[str, Any]] | None = None,
    attributes: list[str] | None = None,
    node_value: str = "",
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "nodeId": node_id,
        "nodeName": node_name,
        "backendNodeId": backend_node_id,
        "nodeValue": node_value,
    }
    if children is not None:
        node["children"] = children
    if attributes is not None:
        node["attributes"] = attributes
    return node


# ---------------------------------------------------------------------------
# DOMNode.from_cdp tests
# ---------------------------------------------------------------------------


class TestDOMNodeFromCdp:
    """Tests for DOMNode.from_cdp conversion."""

    def test_basic_conversion(self) -> None:
        data = _dom_node(node_name="BUTTON", node_id=5, backend_node_id=50)
        node = DOMNode.from_cdp(data)

        assert isinstance(node, DOMNode)
        assert node.node_name == "BUTTON"
        assert node.node_id == 5
        assert node.backend_node_id == 50

    def test_children_recursive(self) -> None:
        data = _dom_node(
            node_name="HTML",
            children=[
                _dom_node(node_name="BODY", node_id=2, backend_node_id=101),
            ],
        )
        node = DOMNode.from_cdp(data)

        assert len(node.children) == 1
        assert node.children[0].node_name == "BODY"

    def test_attributes_as_tuple(self) -> None:
        data = _dom_node(attributes=["id", "main", "class", "container"])
        node = DOMNode.from_cdp(data)

        assert node.attributes == ("id", "main", "class", "container")

    def test_frozen(self) -> None:
        data = _dom_node()
        node = DOMNode.from_cdp(data)
        with pytest.raises(AttributeError):
            node.node_name = "CHANGED"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BoxModel.from_cdp tests
# ---------------------------------------------------------------------------


class TestBoxModelFromCdp:
    """Tests for BoxModel.from_cdp conversion."""

    def test_basic_conversion(self) -> None:
        data = {
            "border": [0, 0, 100, 0, 100, 50, 0, 50],
            "content": [0, 0, 100, 0, 100, 50, 0, 50],
            "width": 100,
            "height": 50,
        }
        model = BoxModel.from_cdp(data)

        assert isinstance(model, BoxModel)
        assert model.width == 100
        assert model.height == 50

    def test_bounds_property(self) -> None:
        data = {"border": [0, 0, 100, 0, 100, 50, 0, 50]}
        model = BoxModel.from_cdp(data)

        bounds = model.bounds
        assert bounds is not None
        assert bounds == (0.0, 0.0, 100.0, 50.0)

    def test_bounds_none_for_empty(self) -> None:
        model = BoxModel()
        assert model.bounds is None

    def test_bounds_none_for_zero_area(self) -> None:
        data = {"border": [0, 0, 0, 0, 0, 0, 0, 0]}
        model = BoxModel.from_cdp(data)
        assert model.bounds is None

    def test_frozen(self) -> None:
        model = BoxModel()
        with pytest.raises(AttributeError):
            model.width = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DOMDomain method tests
# ---------------------------------------------------------------------------


class TestDOMDomainGetDocument:
    """Tests for get_document."""

    def test_returns_dom_node(self) -> None:
        response = {
            "root": _dom_node(
                node_name="HTML",
                children=[_dom_node(node_name="BODY", node_id=2)],
            )
        }
        session = _make_session([response])
        domain = DOMDomain(session)

        doc = domain.get_document()

        assert isinstance(doc, DOMNode)
        assert doc.node_name == "HTML"
        assert len(doc.children) == 1
        assert doc.children[0].node_name == "BODY"

    def test_passes_depth_param(self) -> None:
        session = _make_session([{"root": _dom_node()}])
        domain = DOMDomain(session)

        domain.get_document(depth=2)

        call_args = session.send_command.call_args
        assert call_args[0][0] == "DOM.getDocument"
        assert call_args[0][1] == {"depth": 2}

    def test_sends_default_depth(self) -> None:
        session = _make_session([{"root": _dom_node()}])
        domain = DOMDomain(session)

        domain.get_document()

        call_args = session.send_command.call_args
        assert call_args[0][1] == {"depth": -1}


class TestDOMDomainDescribeNode:
    """Tests for describe_node."""

    def test_with_node_id(self) -> None:
        session = _make_session([{"node": _dom_node(node_name="BUTTON")}])
        domain = DOMDomain(session)

        node = domain.describe_node(node_id=5)

        assert isinstance(node, DOMNode)
        assert node.node_name == "BUTTON"
        call_args = session.send_command.call_args
        assert call_args[0][1]["nodeId"] == 5

    def test_with_backend_node_id(self) -> None:
        session = _make_session([{"node": _dom_node()}])
        domain = DOMDomain(session)

        domain.describe_node(backend_node_id=10)

        call_args = session.send_command.call_args
        assert call_args[0][1]["backendNodeId"] == 10

    def test_with_object_id(self) -> None:
        session = _make_session([{"node": _dom_node()}])
        domain = DOMDomain(session)

        domain.describe_node(object_id="obj-123")

        call_args = session.send_command.call_args
        assert call_args[0][1]["objectId"] == "obj-123"

    def test_raises_without_id(self) -> None:
        session = _make_session()
        domain = DOMDomain(session)

        with pytest.raises(ValueError, match="Must provide"):
            domain.describe_node()


class TestDOMDomainQuerySelector:
    """Tests for query_selector."""

    def test_returns_node_id(self) -> None:
        session = _make_session([{"nodeId": 42}])
        domain = DOMDomain(session)

        result = domain.query_selector(1, "button")
        assert result == 42

    def test_returns_none_for_no_match(self) -> None:
        session = _make_session([{"nodeId": 0}])
        domain = DOMDomain(session)

        result = domain.query_selector(1, ".nonexistent")
        assert result is None

    def test_sends_correct_params(self) -> None:
        session = _make_session([{"nodeId": 0}])
        domain = DOMDomain(session)

        domain.query_selector(5, "input[type='text']")

        call_args = session.send_command.call_args
        assert call_args[0][0] == "DOM.querySelector"
        assert call_args[0][1] == {"nodeId": 5, "selector": "input[type='text']"}


class TestDOMDomainQuerySelectorAll:
    """Tests for query_selector_all."""

    def test_returns_list_of_ids(self) -> None:
        session = _make_session([{"nodeIds": [1, 2, 3]}])
        domain = DOMDomain(session)

        result = domain.query_selector_all(1, "li")
        assert result == [1, 2, 3]

    def test_returns_empty_list(self) -> None:
        session = _make_session([{"nodeIds": []}])
        domain = DOMDomain(session)

        result = domain.query_selector_all(1, ".nothing")
        assert result == []


class TestDOMDomainGetBoxModel:
    """Tests for get_box_model."""

    def test_returns_box_model(self) -> None:
        response = {
            "model": {"border": [0, 0, 100, 0, 100, 50, 0, 50]}
        }
        session = _make_session([response])
        domain = DOMDomain(session)

        model = domain.get_box_model(node_id=5)
        assert isinstance(model, BoxModel)
        assert model.bounds == (0.0, 0.0, 100.0, 50.0)

    def test_returns_none_for_empty(self) -> None:
        session = _make_session([{"model": {}}])
        domain = DOMDomain(session)

        model = domain.get_box_model(node_id=5)
        assert model is None

    def test_raises_without_id(self) -> None:
        session = _make_session()
        domain = DOMDomain(session)

        with pytest.raises(ValueError, match="Must provide"):
            domain.get_box_model()


class TestDOMDomainFocus:
    """Tests for focus."""

    def test_sends_focus_command(self) -> None:
        session = _make_session([{}])
        domain = DOMDomain(session)

        domain.focus(node_id=5)

        call_args = session.send_command.call_args
        assert call_args[0][0] == "DOM.focus"
        assert call_args[0][1] == {"nodeId": 5}

    def test_raises_without_id(self) -> None:
        session = _make_session()
        domain = DOMDomain(session)

        with pytest.raises(ValueError):
            domain.focus()


class TestDOMDomainScrollIntoView:
    """Tests for scroll_into_view_if_needed."""

    def test_sends_scroll_command(self) -> None:
        session = _make_session([{}])
        domain = DOMDomain(session)

        domain.scroll_into_view_if_needed(node_id=5)

        call_args = session.send_command.call_args
        assert call_args[0][0] == "DOM.scrollIntoViewIfNeeded"
        assert call_args[0][1] == {"nodeId": 5}

    def test_with_rect(self) -> None:
        session = _make_session([{}])
        domain = DOMDomain(session)

        domain.scroll_into_view_if_needed(
            node_id=5, rect={"x": 0, "y": 0, "width": 100, "height": 50}
        )

        call_args = session.send_command.call_args
        assert "rect" in call_args[0][1]


class TestDOMDomainResolveNode:
    """Tests for resolve_node."""

    def test_returns_remote_object(self) -> None:
        session = _make_session([{"object": {"type": "object", "objectId": "obj-1"}}])
        domain = DOMDomain(session)

        obj = domain.resolve_node(node_id=5)
        assert obj["objectId"] == "obj-1"

    def test_with_object_group(self) -> None:
        session = _make_session([{"object": {}}])
        domain = DOMDomain(session)

        domain.resolve_node(node_id=5, object_group="test")

        call_args = session.send_command.call_args
        assert call_args[0][1]["objectGroup"] == "test"

    def test_raises_without_id(self) -> None:
        session = _make_session()
        domain = DOMDomain(session)

        with pytest.raises(ValueError):
            domain.resolve_node()
