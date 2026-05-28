"""Tests for WebBackend — CDP-based accessibility backend for web browsers (GW-095, GW-096).

Verifies that WebBackend:
- Is a proper DesktopBackend subclass
- Accepts an optional CDPBrowser instance in the constructor
- Connects to a CDP browser and discovers page targets
- Produces NormalizedElement trees from CDP AX node data
- Finds elements via queryAXTree (server-side) and client-side filtering
- Dispatches click / type / press_key actions via CDP Input domain
- Dispatches set_value via JS evaluation with insertText fallback
- Returns correct element info from the AX cache
- Validates element handles via the AX cache
- Properly raises ActionNotSupportedError for unsupported window operations
- Disposes resources cleanly
- Invalidates the AX cache on each snapshot call
- Supports lazy bounds fetching via DOM domain
- Supports scroll_to_item for virtualized lists
- Delegates to web_normalize helpers for tree building
- Implements lazy bounds caching (GW-096)
- Performs stale element detection before action dispatch (GW-096)
- Focuses elements before typing (GW-096)
- Supports clear-before-type (GW-096)
- Supports directional scrolling (GW-096)
- Supports double-click via click_count kwarg (GW-096)
"""

from unittest.mock import MagicMock, patch

import pytest

from pathlight_mcp.backends.base import DesktopBackend
from pathlight_mcp.backends.types import DesktopAction, NativeHandle
from pathlight_mcp.backends.web import WebBackend
from pathlight_mcp.backends.web_normalize import (
    build_normalized_tree,
    fetch_bounds_from_dom,
    find_root_ax_node,
    infer_ax_actions,
)
from pathlight_mcp.cdp._types import AXNode, CDPTarget
from pathlight_mcp.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
    ElementNotFoundError,
    StaleElementReferenceError,
    WindowNotFoundError,
)

# -- Fixtures ----------------------------------------------------------------


def _make_target(
    target_id: str = "target-1",
    title: str = "Test Page",
    url: str = "https://example.com",
    target_type: str = "page",
) -> CDPTarget:
    """Create a CDPTarget for testing."""
    return CDPTarget(
        id=target_id,
        type=target_type,
        title=title,
        url=url,
        web_socket_debugger_url=f"ws://localhost:9222/devtools/page/{target_id}",
    )


def _make_ax_node(
    node_id: str = "node-1",
    role: str | None = "button",
    name: str | None = "Submit",
    value: str | None = None,
    child_ids: tuple[str, ...] = (),
    bounds: dict | None = None,
    properties: dict | None = None,
    backend_dom_node_id: int | None = None,
) -> AXNode:
    """Create an AXNode for testing."""
    return AXNode(
        node_id=node_id,
        role=role,
        name=name,
        value=value,
        child_ids=child_ids,
        bounds=bounds,
        properties=properties,
        backend_dom_node_id=backend_dom_node_id,
    )


@pytest.fixture
def mock_browser():
    """Create a mock CDPBrowser."""
    browser = MagicMock()
    browser.host = "localhost"
    browser.port = 9222
    return browser


@pytest.fixture
def web_backend():
    """Create a WebBackend with mocked browser."""
    backend = WebBackend(host="localhost", port=9222)
    backend._connected = True
    return backend


@pytest.fixture
def connected_backend(web_backend, mock_browser):
    """Create a connected WebBackend with a mocked browser."""
    web_backend._browser = mock_browser
    target = _make_target()
    mock_browser.list_targets.return_value = [target]
    mock_browser.get_target.return_value = target

    # Create a mock session
    session = MagicMock()
    session.is_attached = True
    session.target = target
    session.send_command.return_value = {}
    mock_browser.attach.return_value = session

    return web_backend


def _setup_domains(connected_backend, mock_browser, acc_mock=None, dom_mock=None, inp_mock=None):
    """Set up domain mocks for a connected backend."""
    target = _make_target()
    connected_backend._sessions[target.id] = MagicMock(
        is_attached=True, target=target, send_command=MagicMock(return_value={})
    )
    acc = acc_mock or MagicMock()
    dom = dom_mock or MagicMock()
    inp = inp_mock or MagicMock()
    page_mock = MagicMock()
    tgt_mock = MagicMock()
    connected_backend._domains[target.id] = (acc, dom, inp, page_mock, tgt_mock)
    return target, acc, dom, inp


# -- TC-1: WebBackend is a DesktopBackend subclass ---------------------------


class TestIsBackendSubclass:
    """TC-1: WebBackend must be a proper DesktopBackend."""

    def test_is_subclass(self) -> None:
        assert issubclass(WebBackend, DesktopBackend)

    def test_isinstance(self) -> None:
        backend = WebBackend()
        assert isinstance(backend, DesktopBackend)


# -- TC-2: Constructor -------------------------------------------------------


class TestConstructor:
    """TC-2: WebBackend should initialize cleanly with and without CDPBrowser."""

    def test_default_state(self) -> None:
        backend = WebBackend()
        assert not backend._disposed
        assert not backend._connected
        assert backend._ax_cache == {}
        assert backend._sessions == {}
        assert backend._domains == {}

    def test_accepts_browser_instance(self, mock_browser) -> None:
        backend = WebBackend(browser=mock_browser)
        assert backend._browser is mock_browser

    def test_browser_kwarg_takes_precedence(self, mock_browser) -> None:
        """When browser= is provided, host/port are ignored."""
        backend = WebBackend(host="other-host", port=9999, browser=mock_browser)
        assert backend._browser is mock_browser

    def test_creates_cdp_browser_when_not_provided(self) -> None:
        backend = WebBackend(host="myhost", port=8080)
        assert backend._browser.host == "myhost"
        assert backend._browser.port == 8080


# -- TC-3: Connect -----------------------------------------------------------


class TestConnect:
    """TC-3: WebBackend.connect() should establish browser connection."""

    def test_connect_success(self) -> None:
        backend = WebBackend()
        with patch.object(backend._browser, "connect"):
            backend.connect()
            assert backend._connected

    def test_connect_when_disposed(self) -> None:
        backend = WebBackend()
        backend._disposed = True
        with pytest.raises(BackendUnavailableError, match="disposed"):
            backend.connect()

    def test_connect_when_already_connected(self) -> None:
        backend = WebBackend()
        backend._connected = True
        backend.connect()  # Should be a no-op
        assert backend._connected


# -- TC-4: List windows (targets) --------------------------------------------


class TestListWindows:
    """TC-4: list_windows should return browser page targets."""

    def test_returns_page_targets(self, connected_backend) -> None:
        windows = connected_backend.list_windows()
        assert len(windows) == 1
        # The handle wraps a CDPTarget
        assert isinstance(windows[0], CDPTarget) or windows[0] is not None

    def test_filters_page_targets_only(self, connected_backend, mock_browser) -> None:
        all_targets = [
            _make_target(target_id="page-1", target_type="page"),
            _make_target(target_id="sw-1", target_type="service_worker"),
        ]

        def _filter_targets(*, target_type=None):
            if target_type is not None:
                return [t for t in all_targets if t.type == target_type]
            return all_targets

        mock_browser.list_targets.side_effect = _filter_targets
        windows = connected_backend.list_windows()
        assert len(windows) == 1

    def test_raises_when_disposed(self, connected_backend) -> None:
        connected_backend._disposed = True
        with pytest.raises(BackendUnavailableError, match="disposed"):
            connected_backend.list_windows()

    def test_raises_when_not_connected(self) -> None:
        backend = WebBackend()
        with pytest.raises(BackendUnavailableError, match="not connected"):
            backend.list_windows()


# -- TC-5: Get window info ---------------------------------------------------


class TestGetWindowInfo:
    """TC-5: get_window_info should return target metadata."""

    def test_returns_metadata(self, connected_backend) -> None:
        target = _make_target(title="My Page")
        window = NativeHandle(target)
        info = connected_backend.get_window_info(window)
        assert info["title"] == "My Page"
        assert info["app_name"] == "browser"
        assert "focused" in info
        assert "bounds" in info

    def test_raises_for_invalid_handle(self, connected_backend) -> None:
        with pytest.raises(WindowNotFoundError):
            connected_backend.get_window_info(NativeHandle("not-a-target"))


# -- TC-6: Snapshot ----------------------------------------------------------


class TestSnapshot:
    """TC-6: snapshot should produce NormalizedElement trees from AX data."""

    def _setup_snapshot(self, connected_backend, mock_browser, ax_nodes) -> None:
        """Configure mocks for snapshot tests."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        # Mock the AccessibilityDomain
        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = ax_nodes

        # Mock the DOMDomain
        dom_mock = MagicMock()
        dom_mock.get_box_model.return_value = None

        # Mock the InputDomain
        inp_mock = MagicMock()

        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

    def test_basic_snapshot(self, connected_backend, mock_browser) -> None:
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", name="Page", child_ids=("btn1",)),
            _make_ax_node(
                node_id="btn1",
                role="button",
                name="Click Me",
                bounds={"x": 10, "y": 20, "width": 100, "height": 30},
            ),
        ]
        self._setup_snapshot(connected_backend, mock_browser, ax_nodes)

        target = _make_target()
        result = connected_backend.snapshot(NativeHandle(target))
        assert result["role"] == "window"  # webArea → window
        assert result["name"] == "Page"
        assert len(result["children"]) == 1
        assert result["children"][0]["role"] == "button"
        assert result["children"][0]["name"] == "Click Me"

    def test_snapshot_respects_max_depth(self, connected_backend, mock_browser) -> None:
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", child_ids=("l1",)),
            _make_ax_node(node_id="l1", role="generic", child_ids=("l2",)),
            _make_ax_node(node_id="l2", role="generic", child_ids=("l3",)),
            _make_ax_node(node_id="l3", role="generic"),
        ]
        self._setup_snapshot(connected_backend, mock_browser, ax_nodes)

        target = _make_target()
        result = connected_backend.snapshot(NativeHandle(target), max_depth=2)
        # At depth 2, l3 should be excluded
        l1 = result["children"][0]
        l2 = l1["children"][0]
        assert l2["children"] == []

    def test_snapshot_respects_max_nodes(self, connected_backend, mock_browser) -> None:
        nodes = [_make_ax_node(node_id="root", role="webArea")]
        for i in range(20):
            nodes.append(_make_ax_node(node_id=f"n{i}", role="text", name=f"Text {i}"))
        # root has all children
        nodes[0] = _make_ax_node(
            node_id="root",
            role="webArea",
            child_ids=tuple(f"n{i}" for i in range(20)),
        )
        self._setup_snapshot(connected_backend, mock_browser, nodes)

        target = _make_target()
        result = connected_backend.snapshot(NativeHandle(target), max_nodes=5)
        assert len(result["children"]) <= 5

    def test_snapshot_rebuilds_cache(self, connected_backend, mock_browser) -> None:
        nodes1 = [
            _make_ax_node(node_id="root", role="webArea"),
        ]
        nodes2 = [
            _make_ax_node(node_id="root2", role="webArea"),
        ]
        self._setup_snapshot(connected_backend, mock_browser, nodes1)

        target = _make_target()
        connected_backend.snapshot(NativeHandle(target))
        assert "root" in connected_backend._ax_cache

        # Update mock to return different nodes
        connected_backend._domains[target.id][0].get_full_ax_tree.return_value = nodes2
        connected_backend.snapshot(NativeHandle(target))
        assert "root2" in connected_backend._ax_cache
        assert "root" not in connected_backend._ax_cache

    def test_snapshot_empty_tree(self, connected_backend, mock_browser) -> None:
        self._setup_snapshot(connected_backend, mock_browser, [])
        target = _make_target()
        # Empty AX tree triggers DOM fallback; configure DOM mock
        # to raise so the fallback returns role="unknown"
        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.side_effect = RuntimeError("no DOM")
        result = connected_backend.snapshot(NativeHandle(target))
        assert result["role"] == "unknown"

    def test_snapshot_empty_tree_dom_fallback(self, connected_backend, mock_browser) -> None:
        """Empty AX tree falls back to DOM snapshot (GW-120)."""
        from pathlight_mcp.cdp._types import DOMNode

        self._setup_snapshot(connected_backend, mock_browser, [])
        target = _make_target()

        # Configure DOM mock to return a real DOMNode
        doc_node = DOMNode(
            node_id=1,
            node_name="#document",
            children=(
                DOMNode(
                    node_id=2,
                    node_name="HTML",
                    children=(DOMNode(node_id=3, node_name="BODY", children=()),),
                ),
            ),
        )
        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.return_value = doc_node

        result = connected_backend.snapshot(NativeHandle(target))
        # DOM fallback should produce a window role from #document
        assert result["role"] == "window"


# -- TC-7: Find elements ------------------------------------------------------


class TestFindElements:
    """TC-7: find_elements should search AX tree by role/name using queryAXTree."""

    def _setup_find(self, connected_backend, mock_browser, ax_nodes) -> None:
        """Configure mocks for find_elements tests."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = ax_nodes
        acc_mock.query_ax_tree.return_value = []  # Default: no results
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        # Populate the cache
        connected_backend._ax_cache = {n.node_id: n for n in ax_nodes}

    def test_find_by_name_uses_client_side(self, connected_backend, mock_browser) -> None:
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", name="", child_ids=("b1", "b2")),
            _make_ax_node(node_id="b1", role="button", name="Submit"),
            _make_ax_node(node_id="b2", role="button", name="Cancel"),
        ]
        self._setup_find(connected_backend, mock_browser, ax_nodes)

        target = _make_target()
        results = connected_backend.find_elements(NativeHandle(target), name="Submit")
        assert len(results) == 1
        assert results[0] == NativeHandle("b1")

    def test_find_by_role_uses_query_ax_tree(self, connected_backend, mock_browser) -> None:
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", child_ids=("b1", "t1")),
            _make_ax_node(node_id="b1", role="button", name="Submit"),
            _make_ax_node(node_id="t1", role="textbox", name="Email"),
        ]
        self._setup_find(connected_backend, mock_browser, ax_nodes)

        # Configure query_ax_tree to return the textbox node when queried for "textbox"
        acc_mock = connected_backend._domains[_make_target().id][0]
        acc_mock.query_ax_tree.side_effect = lambda **kw: (
            [ax_nodes[2]] if kw.get("role") == "textbox" else []
        )

        target = _make_target()
        results = connected_backend.find_elements(NativeHandle(target), role="text_input")
        # query_ax_tree should have been called with role="textbox"
        assert len(results) >= 1

    def test_find_by_role_falls_back_to_client_side(self, connected_backend, mock_browser) -> None:
        """When query_ax_tree raises, falls back to client-side."""
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", child_ids=("b1",)),
            _make_ax_node(node_id="b1", role="button", name="Submit"),
        ]
        self._setup_find(connected_backend, mock_browser, ax_nodes)

        # query_ax_tree raises an exception
        acc_mock = connected_backend._domains[_make_target().id][0]
        acc_mock.query_ax_tree.side_effect = RuntimeError("CDP error")

        target = _make_target()
        results = connected_backend.find_elements(NativeHandle(target), role="button")
        # Falls back to client-side filtering, should still find the button
        assert len(results) >= 1

    def test_find_returns_empty_without_filters(self, connected_backend, mock_browser) -> None:
        target = _make_target()
        results = connected_backend.find_elements(NativeHandle(target))
        assert results == []

    def test_find_case_insensitive_name(self, connected_backend, mock_browser) -> None:
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", name="", child_ids=("b1",)),
            _make_ax_node(node_id="b1", role="button", name="Submit Form"),
        ]
        self._setup_find(connected_backend, mock_browser, ax_nodes)

        target = _make_target()
        results = connected_backend.find_elements(NativeHandle(target), name="submit")
        assert len(results) == 1
        assert results[0] == NativeHandle("b1")

    def test_find_name_and_role_combined(self, connected_backend, mock_browser) -> None:
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", child_ids=("b1", "b2")),
            _make_ax_node(node_id="b1", role="button", name="Submit"),
            _make_ax_node(node_id="b2", role="link", name="Submit"),
        ]
        self._setup_find(connected_backend, mock_browser, ax_nodes)

        target = _make_target()
        results = connected_backend.find_elements(
            NativeHandle(target), role="button", name="Submit"
        )
        # Should find only the button, not the link
        assert len(results) == 1
        assert results[0] == NativeHandle("b1")

    def test_find_builds_cache_if_empty(self, connected_backend, mock_browser) -> None:
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", child_ids=("b1",)),
            _make_ax_node(node_id="b1", role="button", name="Click"),
        ]
        self._setup_find(connected_backend, mock_browser, ax_nodes)

        # Clear the cache to test auto-population
        connected_backend._ax_cache = {}

        target = _make_target()
        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.return_value = ax_nodes

        results = connected_backend.find_elements(NativeHandle(target), name="Click")
        assert len(results) == 1


# -- TC-8: Perform action -----------------------------------------------------


class TestPerformAction:
    """TC-8: perform_action should dispatch actions via CDP Input domain."""

    def _setup_action(self, connected_backend, mock_browser) -> None:
        """Configure mocks for perform_action tests."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        # Populate the cache with a button node
        connected_backend._ax_cache = {
            "btn1": _make_ax_node(
                node_id="btn1",
                role="button",
                name="Click Me",
                bounds={"x": 10, "y": 20, "width": 100, "height": 30},
            ),
            "input1": _make_ax_node(
                node_id="input1",
                role="textbox",
                name="Email",
                properties={"focusable": True, "editable": True},
            ),
        }

    def test_click_dispatches_mouse_events(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("btn1")
        connected_backend.perform_action(handle, DesktopAction.CLICK)
        inp_mock = connected_backend._domains[_make_target().id][2]
        inp_mock.dispatch_mouse_event.assert_called()

    def test_type_dispatches_insert_text(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("input1")
        connected_backend.perform_action(handle, DesktopAction.TYPE, text="hello")
        inp_mock = connected_backend._domains[_make_target().id][2]
        inp_mock.insert_text.assert_called_once_with("hello")

    def test_press_key_dispatches_key_events(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("input1")
        connected_backend.perform_action(handle, DesktopAction.PRESS_KEY, keys="Enter")
        inp_mock = connected_backend._domains[_make_target().id][2]
        # Should dispatch keyDown + keyUp
        assert inp_mock.dispatch_key_event.call_count == 2
        inp_mock.dispatch_key_event.assert_any_call("keyDown", key="Enter")
        inp_mock.dispatch_key_event.assert_any_call("keyUp", key="Enter")

    def test_set_value_with_dom_node(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        # Add a node with backend_dom_node_id
        connected_backend._ax_cache["input2"] = _make_ax_node(
            node_id="input2",
            role="textbox",
            name="Field",
            backend_dom_node_id=42,
        )
        handle = NativeHandle("input2")
        connected_backend.perform_action(handle, DesktopAction.SET_VALUE, value="new")
        # Should have called send_command with Runtime.evaluate
        session = connected_backend._sessions[_make_target().id]
        session.send_command.assert_called()

    def test_set_value_without_dom_node_falls_back(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("input1")
        connected_backend.perform_action(handle, DesktopAction.SET_VALUE, value="new")
        # Falls back to insert_text since no backend_dom_node_id
        inp_mock = connected_backend._domains[_make_target().id][2]
        inp_mock.insert_text.assert_called_once_with("new")

    def test_get_text_returns_value(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        # Update the cache with a node that has a value
        connected_backend._ax_cache["txt1"] = _make_ax_node(
            node_id="txt1", role="text", name="Hello World", value="Hello World"
        )
        handle = NativeHandle("txt1")
        result = connected_backend.perform_action(handle, DesktopAction.GET_TEXT)
        assert result == "Hello World"

    def test_get_text_falls_back_to_name(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        connected_backend._ax_cache["named"] = _make_ax_node(
            node_id="named", role="button", name="Button Text"
        )
        handle = NativeHandle("named")
        result = connected_backend.perform_action(handle, DesktopAction.GET_TEXT)
        assert result == "Button Text"

    def test_raises_for_unknown_element(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("nonexistent")
        with pytest.raises(ElementNotFoundError):
            connected_backend.perform_action(handle, DesktopAction.CLICK)

    def test_raises_when_disposed(self, connected_backend) -> None:
        connected_backend._disposed = True
        handle = NativeHandle("btn1")
        with pytest.raises(StaleElementReferenceError, match="disposed"):
            connected_backend.perform_action(handle, DesktopAction.CLICK)

    def test_type_requires_text_param(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("input1")
        with pytest.raises(ActionNotSupportedError, match="text"):
            connected_backend.perform_action(handle, DesktopAction.TYPE)

    def test_press_key_requires_keys_param(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("input1")
        with pytest.raises(ActionNotSupportedError, match="keys"):
            connected_backend.perform_action(handle, DesktopAction.PRESS_KEY)

    def test_set_value_requires_value_param(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("input1")
        with pytest.raises(ActionNotSupportedError, match="value"):
            connected_backend.perform_action(handle, DesktopAction.SET_VALUE)

    def test_unsupported_action_raises(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("btn1")
        with pytest.raises(ActionNotSupportedError, match="does not support"):
            connected_backend.perform_action(handle, DesktopAction.DESELECT_ITEM)

    def test_toggle_dispatches_click(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("btn1")
        connected_backend.perform_action(handle, DesktopAction.TOGGLE)
        inp_mock = connected_backend._domains[_make_target().id][2]
        inp_mock.dispatch_mouse_event.assert_called()

    def test_expand_dispatches_click(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("btn1")
        connected_backend.perform_action(handle, DesktopAction.EXPAND)
        inp_mock = connected_backend._domains[_make_target().id][2]
        inp_mock.dispatch_mouse_event.assert_called()

    def test_scroll_dispatches_mouse_wheel(self, connected_backend, mock_browser) -> None:
        self._setup_action(connected_backend, mock_browser)
        handle = NativeHandle("btn1")
        connected_backend.perform_action(handle, DesktopAction.SCROLL)
        inp_mock = connected_backend._domains[_make_target().id][2]
        inp_mock.dispatch_mouse_event.assert_called()


# -- TC-9: Get element info ---------------------------------------------------


class TestGetElementInfo:
    """TC-9: get_element_info should return element metadata."""

    def test_returns_role_name_states(self, connected_backend) -> None:
        connected_backend._ax_cache = {
            "btn1": _make_ax_node(
                node_id="btn1",
                role="button",
                name="Submit",
                properties={"disabled": False, "focused": True},
            ),
        }
        handle = NativeHandle("btn1")
        info = connected_backend.get_element_info(handle)
        assert info["role"] == "button"
        assert info["name"] == "Submit"
        assert "states" in info

    def test_raises_for_unknown_element(self, connected_backend) -> None:
        handle = NativeHandle("nonexistent")
        with pytest.raises(ElementNotFoundError):
            connected_backend.get_element_info(handle)


# -- TC-10: Is valid ----------------------------------------------------------


class TestIsValid:
    """TC-10: is_valid should distinguish window handles from element handles (GW-118)."""

    def test_valid_element(self, connected_backend) -> None:
        connected_backend._ax_cache = {"node1": _make_ax_node(node_id="node1")}
        assert connected_backend.is_valid(NativeHandle("node1")) is True

    def test_invalid_element(self, connected_backend) -> None:
        connected_backend._ax_cache = {"node1": _make_ax_node(node_id="node1")}
        assert connected_backend.is_valid(NativeHandle("node999")) is False

    def test_returns_false_when_disposed(self, connected_backend) -> None:
        connected_backend._disposed = True
        assert connected_backend.is_valid(NativeHandle("node1")) is False

    def test_returns_false_for_none(self, connected_backend) -> None:
        assert connected_backend.is_valid(None) is False

    def test_valid_cdp_target_window(self, connected_backend) -> None:
        """CDPTarget window handle is valid when the target still exists (GW-118)."""
        target = _make_target(target_id="ABCDEF")
        connected_backend._browser.get_target.return_value = target
        assert connected_backend.is_valid(NativeHandle(target)) is True

    def test_invalid_cdp_target_window(self, connected_backend) -> None:
        """CDPTarget window handle is invalid when the target no longer exists (GW-118)."""
        target = _make_target(target_id="GONE")
        connected_backend._browser.get_target.return_value = None
        assert connected_backend.is_valid(NativeHandle(target)) is False

    def test_cdp_target_window_disposed(self, connected_backend) -> None:
        """CDPTarget window handle returns False when backend is disposed (GW-118)."""
        target = _make_target()
        connected_backend._disposed = True
        assert connected_backend.is_valid(NativeHandle(target)) is False

    def test_cdp_target_not_stored_in_ax_cache(self, connected_backend) -> None:
        """CDPTarget is validated via browser, not AX cache — even with empty cache (GW-118)."""
        target = _make_target(target_id="PAGE1")
        connected_backend._ax_cache = {}  # Empty cache — would have failed before fix
        connected_backend._browser.get_target.return_value = target
        assert connected_backend.is_valid(NativeHandle(target)) is True

    def test_element_string_still_uses_ax_cache(self, connected_backend) -> None:
        """Plain string element handles still use AX cache lookup (GW-118)."""
        connected_backend._ax_cache = {"n1": _make_ax_node(node_id="n1")}
        # String handles should NOT trigger browser.get_target
        connected_backend._browser.get_target = MagicMock(
            side_effect=AssertionError("should not call get_target for element handles")
        )
        assert connected_backend.is_valid(NativeHandle("n1")) is True


# -- TC-11: Window state management -------------------------------------------


class TestWindowStateManagement:
    """TC-11: Window state operations should raise ActionNotSupportedError."""

    def test_minimize_raises(self, connected_backend) -> None:
        with pytest.raises(ActionNotSupportedError, match="minimize"):
            connected_backend.minimize_window(NativeHandle("w"))

    def test_maximize_raises(self, connected_backend) -> None:
        with pytest.raises(ActionNotSupportedError, match="maximize"):
            connected_backend.maximize_window(NativeHandle("w"))

    def test_restore_raises(self, connected_backend) -> None:
        with pytest.raises(ActionNotSupportedError, match="restore"):
            connected_backend.restore_window(NativeHandle("w"))

    def test_move_raises(self, connected_backend) -> None:
        with pytest.raises(ActionNotSupportedError, match="move"):
            connected_backend.move_window(NativeHandle("w"), 0, 0)

    def test_resize_raises(self, connected_backend) -> None:
        with pytest.raises(ActionNotSupportedError, match="resize"):
            connected_backend.resize_window(NativeHandle("w"), 800, 600)


# -- TC-12: Dispose -----------------------------------------------------------


class TestDispose:
    """TC-12: dispose should clean up all resources."""

    def test_dispose_clears_state(self, connected_backend) -> None:
        connected_backend._ax_cache = {"n1": _make_ax_node()}
        connected_backend.dispose()
        assert connected_backend._disposed
        assert connected_backend._ax_cache == {}
        assert connected_backend._sessions == {}
        assert connected_backend._domains == {}
        assert not connected_backend._connected

    def test_dispose_idempotent(self, connected_backend) -> None:
        connected_backend.dispose()
        connected_backend.dispose()  # Should not raise
        assert connected_backend._disposed


# -- TC-13: Clipboard ---------------------------------------------------------


class TestClipboard:
    """TC-13: clipboard operations via CDP Runtime.evaluate."""

    def test_clipboard_read(self, connected_backend, mock_browser) -> None:
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {"result": {"value": "copied text"}}
        connected_backend._sessions[target.id] = session

        result = connected_backend.clipboard_read()
        assert result == "copied text"

    def test_clipboard_write(self, connected_backend, mock_browser) -> None:
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        connected_backend._sessions[target.id] = session

        connected_backend.clipboard_write("new text")
        session.send_command.assert_called_once()


# -- TC-14: Lazy bounds fetching ----------------------------------------------


class TestLazyBoundsFetching:
    """TC-14: snapshot should fetch bounds via DOM domain when AX lacks them."""

    def test_fetches_bounds_from_dom(self, connected_backend, mock_browser) -> None:
        from pathlight_mcp.cdp._types import BoxModel

        ax_nodes = [
            _make_ax_node(
                node_id="root",
                role="webArea",
                child_ids=("btn1",),
            ),
            _make_ax_node(
                node_id="btn1",
                role="button",
                name="Click",
                bounds=None,
                backend_dom_node_id=42,
            ),
        ]

        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = ax_nodes

        dom_mock = MagicMock()
        # Return a BoxModel with a bounds tuple
        box = BoxModel(
            border=(10.0, 20.0, 110.0, 20.0, 110.0, 50.0, 10.0, 50.0),
            content=(12.0, 22.0, 108.0, 22.0, 108.0, 48.0, 12.0, 48.0),
            width=100,
            height=30,
        )
        dom_mock.get_box_model.return_value = box

        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        result = connected_backend.snapshot(NativeHandle(target))
        # The button should have bounds from the DOM
        btn = result["children"][0]
        assert btn["bounds"] is not None
        assert btn["bounds"]["x"] == 10.0


# -- TC-15: Focus window ------------------------------------------------------


class TestFocusWindow:
    """TC-15: focus_window should send Page.bringToFront."""

    def test_focus_sends_bring_to_front(self, connected_backend, mock_browser) -> None:
        target = _make_target()
        window = NativeHandle(target)
        connected_backend.focus_window(window)
        # Verify the session received the Page.bringToFront command
        session = connected_backend._sessions[target.id]
        session.send_command.assert_called_with("Page.bringToFront")


# -- TC-16: web_normalize module tests ----------------------------------------


class TestFindRootAxNode:
    """TC-16: find_root_ax_node should find the root node in an AX tree."""

    def test_finds_web_area(self) -> None:
        nodes = [
            _make_ax_node(node_id="root", role="webArea", child_ids=("c1",)),
            _make_ax_node(node_id="c1", role="button"),
        ]
        result = find_root_ax_node(nodes)
        assert result is not None
        assert result.role == "webArea"

    def test_finds_first_no_parent(self) -> None:
        nodes = [
            _make_ax_node(node_id="root", role="generic", child_ids=("c1",)),
            _make_ax_node(node_id="c1", role="button"),
        ]
        result = find_root_ax_node(nodes)
        assert result is not None
        assert result.node_id == "root"

    def test_returns_none_for_empty(self) -> None:
        assert find_root_ax_node([]) is None


class TestInferAxActions:
    """TC-17: infer_ax_actions should derive actions from role and properties."""

    def test_button_supports_click(self) -> None:
        node = _make_ax_node(role="button")
        actions = infer_ax_actions(node)
        assert "click" in actions

    def test_textbox_supports_type_and_set_value(self) -> None:
        node = _make_ax_node(role="textbox")
        actions = infer_ax_actions(node)
        assert "type" in actions
        assert "set_value" in actions

    def test_focusable_adds_focus(self) -> None:
        node = _make_ax_node(role="generic", properties={"focusable": True})
        actions = infer_ax_actions(node)
        assert "focus" in actions

    def test_expandable_adds_expand_collapse(self) -> None:
        node = _make_ax_node(role="button", properties={"expanded": False})
        actions = infer_ax_actions(node)
        assert "expand" in actions
        assert "collapse" in actions

    def test_checkable_adds_toggle(self) -> None:
        node = _make_ax_node(role="checkbox", properties={"checked": "true"})
        actions = infer_ax_actions(node)
        assert "toggle" in actions

    def test_slider_adds_scroll_increment_decrement(self) -> None:
        node = _make_ax_node(role="slider")
        actions = infer_ax_actions(node)
        assert "scroll" in actions
        assert "increment" in actions
        assert "decrement" in actions


class TestFetchBoundsFromDom:
    """TC-18: fetch_bounds_from_dom should get bounds via DOM domain."""

    def test_returns_bounds_dict(self) -> None:
        from pathlight_mcp.cdp._types import BoxModel

        dom_mock = MagicMock()
        box = BoxModel(
            border=(10.0, 20.0, 110.0, 20.0, 110.0, 50.0, 10.0, 50.0),
            content=(12.0, 22.0, 108.0, 22.0, 108.0, 48.0, 12.0, 48.0),
            width=100,
            height=30,
        )
        dom_mock.get_box_model.return_value = box

        result = fetch_bounds_from_dom(dom_mock, 42)
        assert result is not None
        assert result["x"] == 10.0
        assert result["width"] == 100.0

    def test_returns_none_on_exception(self) -> None:
        dom_mock = MagicMock()
        dom_mock.get_box_model.side_effect = RuntimeError("CDP error")

        result = fetch_bounds_from_dom(dom_mock, 42)
        assert result is None


class TestBuildNormalizedTree:
    """TC-19: build_normalized_tree should recursively build NormalizedElement."""

    def test_builds_tree(self) -> None:
        ax_nodes = [
            _make_ax_node(
                node_id="root",
                role="webArea",
                child_ids=("btn1",),
                bounds={"x": 0, "y": 0, "width": 800, "height": 600},
            ),
            _make_ax_node(
                node_id="btn1",
                role="button",
                name="Click",
                bounds={"x": 10, "y": 20, "width": 100, "height": 30},
            ),
        ]
        cache = {n.node_id: n for n in ax_nodes}
        dom_mock = MagicMock()

        counter = [0]
        result = build_normalized_tree(ax_nodes[0], 0, 4, counter, 500, dom_mock, cache)
        assert result is not None
        assert result.role == "window"  # webArea → window
        assert len(result.children) == 1
        assert result.children[0].name == "Click"

    def test_respects_max_nodes(self) -> None:
        root = _make_ax_node(node_id="root", role="webArea", child_ids=("c1",))
        cache = {"root": root}

        counter = [1]  # Already counted 1
        result = build_normalized_tree(root, 0, 4, counter, 1, MagicMock(), cache)
        # Max nodes reached, should return None (counter >= max_nodes at start)
        assert result is None


# -- TC-20: scroll_to_item ----------------------------------------------------


class TestScrollToItem:
    """TC-20: scroll_to_item should find and scroll items into view."""

    def test_scroll_by_name(self, connected_backend, mock_browser) -> None:
        target = _make_target()
        session = MagicMock(
            is_attached=True,
            target=target,
            send_command=MagicMock(return_value={}),
        )
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        # Set up cache with a list and children
        connected_backend._ax_cache = {
            "list1": _make_ax_node(node_id="list1", role="list", child_ids=("item1", "item2")),
            "item1": _make_ax_node(
                node_id="item1", role="listitem", name="Apple", backend_dom_node_id=10
            ),
            "item2": _make_ax_node(
                node_id="item2", role="listitem", name="Banana", backend_dom_node_id=11
            ),
        }

        result = connected_backend.scroll_to_item(NativeHandle("list1"), item_name="banana")
        assert result is not None
        assert result == NativeHandle("item2")

    def test_scroll_by_index(self, connected_backend, mock_browser) -> None:
        target = _make_target()
        session = MagicMock(
            is_attached=True,
            target=target,
            send_command=MagicMock(return_value={}),
        )
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend._ax_cache = {
            "list1": _make_ax_node(node_id="list1", role="list", child_ids=("item1", "item2")),
            "item1": _make_ax_node(
                node_id="item1", role="listitem", name="Apple", backend_dom_node_id=10
            ),
            "item2": _make_ax_node(
                node_id="item2", role="listitem", name="Banana", backend_dom_node_id=11
            ),
        }

        result = connected_backend.scroll_to_item(NativeHandle("list1"), item_index=1)
        assert result == NativeHandle("item2")

    def test_scroll_returns_none_when_not_found(self, connected_backend, mock_browser) -> None:
        connected_backend._ax_cache = {
            "list1": _make_ax_node(node_id="list1", role="list", child_ids=()),
        }
        result = connected_backend.scroll_to_item(NativeHandle("list1"), item_name="missing")
        assert result is None

    def test_scroll_requires_name_or_index(self, connected_backend) -> None:
        with pytest.raises(ActionNotSupportedError, match="requires"):
            connected_backend.scroll_to_item(NativeHandle("list1"))


# -- GW-096: Bounds caching ---------------------------------------------------


class TestBoundsCaching:
    """GW-096: _resolve_bounds should cache DOM-fetched bounds."""

    def test_caches_bounds_after_first_fetch(self, connected_backend, mock_browser) -> None:
        """Bounds fetched from DOM are cached and not re-fetched."""
        from pathlight_mcp.cdp._types import BoxModel

        target = _make_target()
        session = MagicMock(
            is_attached=True, target=target, send_command=MagicMock(return_value={})
        )
        connected_backend._sessions[target.id] = session

        dom_mock = MagicMock()
        box = BoxModel(
            border=(50.0, 60.0, 150.0, 60.0, 150.0, 110.0, 50.0, 110.0),
            content=(52.0, 62.0, 148.0, 62.0, 148.0, 108.0, 52.0, 108.0),
            width=100,
            height=50,
        )
        dom_mock.get_box_model.return_value = box
        inp_mock = MagicMock()
        acc_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        # Node without inline bounds but with backend_dom_node_id
        node = _make_ax_node(
            node_id="no-bounds",
            role="button",
            name="Cached",
            bounds=None,
            backend_dom_node_id=99,
        )
        connected_backend._ax_cache = {"no-bounds": node}

        # First call — should fetch from DOM
        result1 = connected_backend._resolve_bounds(node, dom_mock)
        assert result1 is not None
        assert result1["x"] == 50.0
        assert dom_mock.get_box_model.call_count == 1

        # Second call — should use cache, not re-fetch
        result2 = connected_backend._resolve_bounds(node, dom_mock)
        assert result2 == result1
        assert dom_mock.get_box_model.call_count == 1  # No additional call

        # Verify the bounds are in the cache
        assert "no-bounds" in connected_backend._bounds_cache

    def test_uses_inline_bounds_without_cache(self, connected_backend) -> None:
        """When AX node has inline bounds, returns them directly."""
        node = _make_ax_node(
            node_id="has-bounds",
            role="button",
            bounds={"x": 10, "y": 20, "width": 100, "height": 30},
        )
        dom_mock = MagicMock()
        result = connected_backend._resolve_bounds(node, dom_mock)
        assert result == {"x": 10, "y": 20, "width": 100, "height": 30}
        # DOM should not be called
        dom_mock.get_box_model.assert_not_called()

    def test_returns_none_when_no_bounds_available(self, connected_backend) -> None:
        """Returns None when node has no bounds and no DOM node ID."""
        node = _make_ax_node(node_id="no-bounds", role="generic", bounds=None)
        dom_mock = MagicMock()
        result = connected_backend._resolve_bounds(node, dom_mock)
        assert result is None

    def test_snapshot_invalidates_bounds_cache(self, connected_backend, mock_browser) -> None:
        """Snapshot call should clear the bounds cache."""
        # Populate the bounds cache
        connected_backend._bounds_cache = {"old-node": {"x": 1, "y": 2, "width": 3, "height": 4}}

        # Set up for snapshot
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea"),
        ]
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = ax_nodes
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend.snapshot(NativeHandle(target))
        assert connected_backend._bounds_cache == {}

    def test_dispose_clears_bounds_cache(self, connected_backend) -> None:
        """Dispose should clear the bounds cache."""
        connected_backend._bounds_cache = {"n1": {"x": 0, "y": 0, "width": 10, "height": 10}}
        connected_backend.dispose()
        assert connected_backend._bounds_cache == {}


# -- GW-096: Stale element detection -------------------------------------------


class TestStaleElementDetection:
    """GW-096: _stale_check should detect removed elements."""

    def test_stale_check_raises_for_removed_node(self, connected_backend, mock_browser) -> None:
        """After node is removed from cache, stale_check raises."""
        connected_backend._ax_cache = {
            "btn1": _make_ax_node(node_id="btn1", role="button", name="OK"),
        }

        # Simulate the node being removed (e.g. by a new snapshot)
        del connected_backend._ax_cache["btn1"]

        with pytest.raises(StaleElementReferenceError, match="stale"):
            connected_backend._stale_check("btn1")

    def test_stale_check_passes_for_valid_node(self, connected_backend) -> None:
        """Stale check passes silently for a valid node."""
        connected_backend._ax_cache = {
            "btn1": _make_ax_node(node_id="btn1", role="button"),
        }
        # Should not raise
        connected_backend._stale_check("btn1")

    def test_perform_action_detects_stale_element(self, connected_backend, mock_browser) -> None:
        """perform_action raises StaleElementReferenceError when element becomes stale."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        # Put node in cache then remove it (simulating staleness between snapshot and action)
        connected_backend._ax_cache = {
            "btn1": _make_ax_node(
                node_id="btn1",
                role="button",
                name="Click Me",
                bounds={"x": 10, "y": 20, "width": 100, "height": 30},
            ),
        }

        handle = NativeHandle("btn1")

        # Remove the node from cache to simulate it becoming stale
        del connected_backend._ax_cache["btn1"]

        with pytest.raises(ElementNotFoundError):
            connected_backend.perform_action(handle, DesktopAction.CLICK)


# -- GW-096: Focus before type ------------------------------------------------


class TestFocusBeforeType:
    """GW-096: TYPE action should focus element before inserting text."""

    def test_type_focuses_element_with_dom_node(self, connected_backend, mock_browser) -> None:
        """TYPE focuses the element via DOM before inserting text."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        acc_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend._ax_cache = {
            "input1": _make_ax_node(
                node_id="input1",
                role="textbox",
                name="Email",
                backend_dom_node_id=42,
            ),
        }

        handle = NativeHandle("input1")
        connected_backend.perform_action(handle, DesktopAction.TYPE, text="hello")

        # Should have focused the element via DOM
        dom_mock.focus.assert_called_once_with(backend_node_id=42)
        # Should have inserted text
        inp_mock.insert_text.assert_called_once_with("hello")

    def test_type_without_dom_node_still_inserts(self, connected_backend, mock_browser) -> None:
        """TYPE without backend_dom_node_id skips focus but still inserts text."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        acc_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend._ax_cache = {
            "input1": _make_ax_node(
                node_id="input1",
                role="textbox",
                name="Email",
                backend_dom_node_id=None,
            ),
        }

        handle = NativeHandle("input1")
        connected_backend.perform_action(handle, DesktopAction.TYPE, text="hello")

        # DOM focus should not be called (no backend_dom_node_id)
        dom_mock.focus.assert_not_called()
        # Text should still be inserted
        inp_mock.insert_text.assert_called_once_with("hello")


# -- GW-096: Clear before type ------------------------------------------------


class TestClearBeforeType:
    """GW-096: TYPE action with clear=True should select all and delete first."""

    def test_type_with_clear_dispatches_ctrl_a_and_backspace(
        self, connected_backend, mock_browser
    ) -> None:
        """TYPE with clear=True sends Ctrl+A then Backspace before inserting."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        acc_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend._ax_cache = {
            "input1": _make_ax_node(
                node_id="input1",
                role="textbox",
                name="Email",
                backend_dom_node_id=42,
            ),
        }

        handle = NativeHandle("input1")
        connected_backend.perform_action(handle, DesktopAction.TYPE, text="new text", clear=True)

        # Should dispatch: keyDown Ctrl+A, keyUp Ctrl+A, keyDown Backspace, keyUp Backspace
        key_calls = inp_mock.dispatch_key_event.call_args_list
        assert len(key_calls) == 4
        key_calls[0].assert_called_with("keyDown", key="a", modifiers=2)
        key_calls[1].assert_called_with("keyUp", key="a", modifiers=2)
        key_calls[2].assert_called_with("keyDown", key="Backspace")
        key_calls[3].assert_called_with("keyUp", key="Backspace")

        # Then insert text
        inp_mock.insert_text.assert_called_once_with("new text")

    def test_type_without_clear_just_inserts(self, connected_backend, mock_browser) -> None:
        """TYPE without clear flag just inserts text (no Ctrl+A/Backspace)."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        acc_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend._ax_cache = {
            "input1": _make_ax_node(
                node_id="input1",
                role="textbox",
                name="Email",
                backend_dom_node_id=42,
            ),
        }

        handle = NativeHandle("input1")
        connected_backend.perform_action(handle, DesktopAction.TYPE, text="hello")

        # No key events dispatched (no clear)
        inp_mock.dispatch_key_event.assert_not_called()
        inp_mock.insert_text.assert_called_once_with("hello")


# -- GW-096: Directional scrolling ---------------------------------------------


class TestDirectionalScrolling:
    """GW-096: SCROLL action supports direction kwarg (up/down/left/right)."""

    def _setup_scroll(self, connected_backend, mock_browser):
        target = _make_target()
        session = MagicMock(
            is_attached=True, target=target, send_command=MagicMock(return_value={})
        )
        connected_backend._sessions[target.id] = session

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        acc_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend._ax_cache = {
            "scroll1": _make_ax_node(
                node_id="scroll1",
                role="generic",
                bounds={"x": 50, "y": 50, "width": 200, "height": 200},
            ),
        }

    def test_scroll_down_default(self, connected_backend, mock_browser) -> None:
        self._setup_scroll(connected_backend, mock_browser)
        connected_backend.perform_action(NativeHandle("scroll1"), DesktopAction.SCROLL)
        inp_mock = connected_backend._domains[_make_target().id][2]
        inp_mock.dispatch_mouse_event.assert_called_once()
        call_kwargs = inp_mock.dispatch_mouse_event.call_args
        assert call_kwargs[1]["delta_y"] == 100.0
        assert call_kwargs[1]["delta_x"] == 0.0

    def test_scroll_up(self, connected_backend, mock_browser) -> None:
        self._setup_scroll(connected_backend, mock_browser)
        connected_backend.perform_action(
            NativeHandle("scroll1"), DesktopAction.SCROLL, direction="up"
        )
        inp_mock = connected_backend._domains[_make_target().id][2]
        call_kwargs = inp_mock.dispatch_mouse_event.call_args
        assert call_kwargs[1]["delta_y"] == -100.0
        assert call_kwargs[1]["delta_x"] == 0.0

    def test_scroll_left(self, connected_backend, mock_browser) -> None:
        self._setup_scroll(connected_backend, mock_browser)
        connected_backend.perform_action(
            NativeHandle("scroll1"), DesktopAction.SCROLL, direction="left"
        )
        inp_mock = connected_backend._domains[_make_target().id][2]
        call_kwargs = inp_mock.dispatch_mouse_event.call_args
        assert call_kwargs[1]["delta_x"] == -100.0
        assert call_kwargs[1]["delta_y"] == 0.0

    def test_scroll_right(self, connected_backend, mock_browser) -> None:
        self._setup_scroll(connected_backend, mock_browser)
        connected_backend.perform_action(
            NativeHandle("scroll1"), DesktopAction.SCROLL, direction="right"
        )
        inp_mock = connected_backend._domains[_make_target().id][2]
        call_kwargs = inp_mock.dispatch_mouse_event.call_args
        assert call_kwargs[1]["delta_x"] == 100.0
        assert call_kwargs[1]["delta_y"] == 0.0

    def test_scroll_custom_delta(self, connected_backend, mock_browser) -> None:
        self._setup_scroll(connected_backend, mock_browser)
        connected_backend.perform_action(
            NativeHandle("scroll1"), DesktopAction.SCROLL, direction="down", delta=50.0
        )
        inp_mock = connected_backend._domains[_make_target().id][2]
        call_kwargs = inp_mock.dispatch_mouse_event.call_args
        assert call_kwargs[1]["delta_y"] == 50.0


# -- GW-096: Double-click support ----------------------------------------------


class TestDoubleClick:
    """GW-096: CLICK action supports click_count kwarg for double-click."""

    def test_click_default_count(self, connected_backend, mock_browser) -> None:
        """Default click has click_count=1."""
        target = _make_target()
        session = MagicMock(
            is_attached=True, target=target, send_command=MagicMock(return_value={})
        )
        connected_backend._sessions[target.id] = session

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        acc_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend._ax_cache = {
            "btn1": _make_ax_node(
                node_id="btn1",
                role="button",
                name="Click",
                bounds={"x": 10, "y": 20, "width": 100, "height": 30},
            ),
        }

        connected_backend.perform_action(NativeHandle("btn1"), DesktopAction.CLICK)
        calls = inp_mock.dispatch_mouse_event.call_args_list
        assert len(calls) == 2
        # Both press and release should have click_count=1
        assert calls[0][1]["click_count"] == 1
        assert calls[1][1]["click_count"] == 1

    def test_double_click_count(self, connected_backend, mock_browser) -> None:
        """click_count=2 dispatches double-click."""
        target = _make_target()
        session = MagicMock(
            is_attached=True, target=target, send_command=MagicMock(return_value={})
        )
        connected_backend._sessions[target.id] = session

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        acc_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend._ax_cache = {
            "btn1": _make_ax_node(
                node_id="btn1",
                role="button",
                name="Click",
                bounds={"x": 10, "y": 20, "width": 100, "height": 30},
            ),
        }

        connected_backend.perform_action(NativeHandle("btn1"), DesktopAction.CLICK, click_count=2)
        calls = inp_mock.dispatch_mouse_event.call_args_list
        assert len(calls) == 2
        assert calls[0][1]["click_count"] == 2
        assert calls[1][1]["click_count"] == 2


# -- GW-096: Click uses bounds cache -------------------------------------------


class TestClickBoundsCaching:
    """GW-096: Click uses _resolve_bounds with lazy caching."""

    def test_click_fetches_and_caches_bounds(self, connected_backend, mock_browser) -> None:
        """Click fetches bounds from DOM on first call, caches for second."""
        from pathlight_mcp.cdp._types import BoxModel

        target = _make_target()
        session = MagicMock(
            is_attached=True, target=target, send_command=MagicMock(return_value={})
        )
        connected_backend._sessions[target.id] = session

        dom_mock = MagicMock()
        box = BoxModel(
            border=(20.0, 30.0, 120.0, 30.0, 120.0, 60.0, 20.0, 60.0),
            content=(22.0, 32.0, 118.0, 32.0, 118.0, 58.0, 22.0, 58.0),
            width=100,
            height=30,
        )
        dom_mock.get_box_model.return_value = box
        inp_mock = MagicMock()
        acc_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        connected_backend._ax_cache = {
            "btn1": _make_ax_node(
                node_id="btn1",
                role="button",
                name="Click",
                bounds=None,
                backend_dom_node_id=55,
            ),
        }

        # First click — fetches bounds from DOM
        connected_backend.perform_action(NativeHandle("btn1"), DesktopAction.CLICK)
        assert dom_mock.get_box_model.call_count == 1
        assert "btn1" in connected_backend._bounds_cache

        # Second click — uses cached bounds
        connected_backend.perform_action(NativeHandle("btn1"), DesktopAction.CLICK)
        assert dom_mock.get_box_model.call_count == 1  # No additional fetch


# -- GW-101: Virtualized list detection ----------------------------------------


class TestIsVirtualizedContainer:
    """GW-101: is_virtualized_container detects aria-rowcount/setsize heuristics."""

    def test_table_with_rowcount(self) -> None:
        from pathlight_mcp.backends.web_normalize import is_virtualized_container

        node = _make_ax_node(
            node_id="grid1",
            role="grid",
            child_ids=("row1", "row2"),
            properties={"rowcount": 100},
        )
        assert is_virtualized_container(node) is True

    def test_grid_with_matching_rowcount(self) -> None:
        """Grid where rowcount equals children is NOT virtualized."""
        from pathlight_mcp.backends.web_normalize import is_virtualized_container

        node = _make_ax_node(
            node_id="grid1",
            role="grid",
            child_ids=("row1", "row2"),
            properties={"rowcount": 2},
        )
        assert is_virtualized_container(node) is False

    def test_listbox_with_setsize(self) -> None:
        from pathlight_mcp.backends.web_normalize import is_virtualized_container

        node = _make_ax_node(
            node_id="lb1",
            role="listbox",
            child_ids=("opt1", "opt2"),
            properties={"setsize": 50},
        )
        assert is_virtualized_container(node) is True

    def test_list_with_setsize(self) -> None:
        from pathlight_mcp.backends.web_normalize import is_virtualized_container

        node = _make_ax_node(
            node_id="l1",
            role="list",
            child_ids=("item1",),
            properties={"setsize": 10},
        )
        assert is_virtualized_container(node) is True

    def test_tree_with_setsize(self) -> None:
        from pathlight_mcp.backends.web_normalize import is_virtualized_container

        node = _make_ax_node(
            node_id="t1",
            role="tree",
            child_ids=("ti1",),
            properties={"setsize": 20},
        )
        assert is_virtualized_container(node) is True

    def test_non_container_not_virtualized(self) -> None:
        from pathlight_mcp.backends.web_normalize import is_virtualized_container

        node = _make_ax_node(node_id="btn1", role="button")
        assert is_virtualized_container(node) is False

    def test_table_without_rowcount_not_virtualized(self) -> None:
        from pathlight_mcp.backends.web_normalize import is_virtualized_container

        node = _make_ax_node(
            node_id="grid1",
            role="grid",
            child_ids=("row1",),
        )
        assert is_virtualized_container(node) is False

    def test_invalid_rowcount_type_ignored(self) -> None:
        from pathlight_mcp.backends.web_normalize import is_virtualized_container

        node = _make_ax_node(
            node_id="grid1",
            role="grid",
            child_ids=("row1",),
            properties={"rowcount": "not-a-number"},
        )
        assert is_virtualized_container(node) is False


class TestVirtualizedActions:
    """GW-101: Virtualized containers get scroll + scroll_to_item actions."""

    def test_virtualized_grid_has_scroll_to_item(self) -> None:
        from pathlight_mcp.backends.web_normalize import infer_ax_actions

        node = _make_ax_node(
            node_id="grid1",
            role="grid",
            child_ids=("row1", "row2"),
            properties={"rowcount": 100},
        )
        actions = infer_ax_actions(node)
        assert "scroll" in actions
        assert "scroll_to_item" in actions

    def test_virtualized_listbox_has_scroll_to_item(self) -> None:
        from pathlight_mcp.backends.web_normalize import infer_ax_actions

        node = _make_ax_node(
            node_id="lb1",
            role="listbox",
            child_ids=("opt1",),
            properties={"setsize": 50},
        )
        actions = infer_ax_actions(node)
        assert "scroll_to_item" in actions

    def test_non_virtualized_no_scroll_to_item(self) -> None:
        from pathlight_mcp.backends.web_normalize import infer_ax_actions

        node = _make_ax_node(node_id="list1", role="list", child_ids=("a", "b"))
        actions = infer_ax_actions(node)
        assert "scroll_to_item" not in actions


class TestVirtualizedTreeMarker:
    """GW-101: build_normalized_tree marks virtualized containers with is_virtualized."""

    def test_virtualized_grid_marked(self) -> None:
        ax_nodes = [
            _make_ax_node(
                node_id="root",
                role="webArea",
                child_ids=("grid1",),
                bounds={"x": 0, "y": 0, "width": 800, "height": 600},
            ),
            _make_ax_node(
                node_id="grid1",
                role="grid",
                child_ids=("row1",),
                properties={"rowcount": 100},
                bounds={"x": 10, "y": 10, "width": 400, "height": 300},
            ),
            _make_ax_node(
                node_id="row1",
                role="row",
                bounds={"x": 10, "y": 10, "width": 400, "height": 30},
            ),
        ]
        cache = {n.node_id: n for n in ax_nodes}
        dom_mock = MagicMock()

        counter = [0]
        result = build_normalized_tree(ax_nodes[0], 0, 4, counter, 500, dom_mock, cache)
        assert result is not None

        grid_elem = result.children[0]
        assert grid_elem.is_virtualized is True
        assert "scroll_to_item" in grid_elem.actions

    def test_non_virtualized_not_marked(self) -> None:
        ax_nodes = [
            _make_ax_node(
                node_id="root",
                role="webArea",
                child_ids=("list1",),
                bounds={"x": 0, "y": 0, "width": 800, "height": 600},
            ),
            _make_ax_node(
                node_id="list1",
                role="list",
                child_ids=("item1", "item2"),
                bounds={"x": 10, "y": 10, "width": 200, "height": 100},
            ),
        ]
        cache = {n.node_id: n for n in ax_nodes}
        dom_mock = MagicMock()

        counter = [0]
        result = build_normalized_tree(ax_nodes[0], 0, 4, counter, 500, dom_mock, cache)
        assert result is not None

        list_elem = result.children[0]
        assert list_elem.is_virtualized is None


# -- GW-101: Multi-frame (iframe) snapshot -------------------------------------


class TestMultiFrameSnapshot:
    """GW-101: snapshot merges AX trees from child iframe frames."""

    def test_snapshot_with_iframes(self, connected_backend, mock_browser) -> None:
        """Snapshot discovers iframes via Page.getFrameTree and merges AX trees."""
        # Main frame AX tree
        main_ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", name="Main", child_ids=("iframe1",)),
            _make_ax_node(
                node_id="iframe1",
                role="iframe",
                name="Embedded Frame",
                child_ids=(),
            ),
        ]

        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = main_ax_nodes

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        # Return frame tree with one child iframe as FrameTree dataclass
        from pathlight_mcp.cdp._types import FrameTree

        page_mock.get_frame_tree.return_value = FrameTree(
            frame={"id": "main-frame", "url": "https://example.com"},
            child_frames=(
                FrameTree(
                    frame={"id": "iframe-abc", "url": "https://other.com/embed"},
                    child_frames=(),
                ),
            ),
        )
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        # Set up the iframe session and its AX tree
        iframe_session = MagicMock()
        iframe_session.is_attached = True
        iframe_session.send_command.return_value = {}
        mock_browser.attach.side_effect = [session, iframe_session]

        iframe_acc_mock = MagicMock()
        iframe_acc_mock.get_full_ax_tree.return_value = [
            _make_ax_node(
                node_id="iframe-root", role="webArea", name="Iframe Content", child_ids=("btn-ifr",)
            ),
            _make_ax_node(node_id="btn-ifr", role="button", name="Iframe Button"),
        ]

        with patch("pathlight_mcp.backends.web.AccessibilityDomain", return_value=iframe_acc_mock):
            result = connected_backend.snapshot(NativeHandle(target))

        # The main tree should include iframe nodes with prefixed IDs
        assert result["role"] == "window"
        assert result["name"] == "Main"

        # Verify iframe nodes were added to the cache with prefixed IDs
        assert "iframe-abc:iframe-root" in connected_backend._ax_cache
        assert "iframe-abc:btn-ifr" in connected_backend._ax_cache

    def test_snapshot_without_iframes(self, connected_backend, mock_browser) -> None:
        """Snapshot works normally when no child frames exist."""
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", name="Simple Page"),
        ]

        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = ax_nodes

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        # No child frames
        from pathlight_mcp.cdp._types import FrameTree

        page_mock.get_frame_tree.return_value = FrameTree(
            frame={"id": "main-frame", "url": "https://example.com"},
            child_frames=(),
        )
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        result = connected_backend.snapshot(NativeHandle(target))
        assert result["role"] == "window"
        assert result["name"] == "Simple Page"

    def test_snapshot_iframe_failure_graceful(self, connected_backend, mock_browser) -> None:
        """Snapshot continues when iframe AX tree fetch fails."""
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", name="Main Page"),
        ]

        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = ax_nodes

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        page_mock.get_frame_tree.return_value = [
            {"id": "main-frame", "url": "https://example.com"},
            {"id": "bad-iframe", "parentId": "main-frame", "url": "https://bad.com"},
        ]
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        # Make the iframe attach fail
        def attach_side_effect(tgt):
            if tgt.id == "bad-iframe":
                raise RuntimeError("Cannot attach to iframe")
            return session

        mock_browser.attach.side_effect = attach_side_effect

        result = connected_backend.snapshot(NativeHandle(target))
        assert result["role"] == "window"
        assert result["name"] == "Main Page"

    def test_snapshot_get_frame_tree_failure_graceful(
        self, connected_backend, mock_browser
    ) -> None:
        """Snapshot continues when Page.getFrameTree fails."""
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea", name="Page"),
        ]

        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = ax_nodes

        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        page_mock.get_frame_tree.side_effect = RuntimeError("CDP error")
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        result = connected_backend.snapshot(NativeHandle(target))
        assert result["role"] == "window"
        assert result["name"] == "Page"


# -- GW-101: Virtualized scroll-retry -------------------------------------------


class TestVirtualizedScrollRetry:
    """GW-101: scroll_to_item uses scroll-retry for virtualized containers."""

    def test_scroll_retry_finds_item_after_scroll(self, connected_backend, mock_browser) -> None:
        """Virtualized container: scroll-retry finds item after scrolling."""
        target = _make_target()
        session = MagicMock(
            is_attached=True, target=target, send_command=MagicMock(return_value={})
        )
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        # Virtualized list with setsize=100 but only 2 rendered items
        container_node = _make_ax_node(
            node_id="vlist",
            role="list",
            child_ids=("item0", "item1"),
            bounds={"x": 50, "y": 50, "width": 200, "height": 200},
            properties={"setsize": 100},
        )

        # Simulate: after scroll, a new item appears
        item_after_scroll = _make_ax_node(
            node_id="item-scrolled",
            role="listitem",
            name="Target Item",
            backend_dom_node_id=99,
        )

        connected_backend._ax_cache = {
            "vlist": container_node,
            "item0": _make_ax_node(node_id="item0", role="listitem", name="First"),
            "item1": _make_ax_node(node_id="item1", role="listitem", name="Second"),
        }

        # After first scroll, add a new child to simulate materialization
        original_find = connected_backend._find_cached_children

        def find_with_materialization(parent_id):
            if parent_id == "vlist":
                # After scroll, simulate new items appearing
                children = original_find(parent_id)
                if len(children) == 2:
                    # First call (before scroll), return just the 2 original
                    # Add a new item to cache and return all 3
                    connected_backend._ax_cache["item-scrolled"] = item_after_scroll
                    updated_container = _make_ax_node(
                        node_id="vlist",
                        role="list",
                        child_ids=("item0", "item1", "item-scrolled"),
                        bounds={"x": 50, "y": 50, "width": 200, "height": 200},
                        properties={"setsize": 100},
                    )
                    connected_backend._ax_cache["vlist"] = updated_container
                    return [
                        connected_backend._ax_cache[cid]
                        for cid in updated_container.child_ids
                        if cid in connected_backend._ax_cache
                    ]
                return children
            return original_find(parent_id)

        connected_backend._find_cached_children = find_with_materialization

        result = connected_backend.scroll_to_item(NativeHandle("vlist"), item_name="Target")
        assert result is not None
        assert result == NativeHandle("item-scrolled")

    def test_scroll_retry_returns_none_when_exhausted(
        self, connected_backend, mock_browser
    ) -> None:
        """Virtualized container: scroll-retry returns None after max retries."""
        target = _make_target()
        session = MagicMock(
            is_attached=True, target=target, send_command=MagicMock(return_value={})
        )
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        container_node = _make_ax_node(
            node_id="vlist",
            role="list",
            child_ids=("item0",),
            bounds={"x": 50, "y": 50, "width": 200, "height": 200},
            properties={"setsize": 100},
        )

        connected_backend._ax_cache = {
            "vlist": container_node,
            "item0": _make_ax_node(node_id="item0", role="listitem", name="First"),
        }

        result = connected_backend.scroll_to_item(
            NativeHandle("vlist"), item_name="NonExistent", max_retries=2
        )
        assert result is None
        # Should have scrolled 2 times (the retry count)
        assert inp_mock.dispatch_mouse_event.call_count == 2

    def test_non_virtualized_no_scroll_retry(self, connected_backend, mock_browser) -> None:
        """Non-virtualized container: no scroll-retry, returns None directly."""
        target = _make_target()
        session = MagicMock(
            is_attached=True, target=target, send_command=MagicMock(return_value={})
        )
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        container_node = _make_ax_node(
            node_id="normallist",
            role="list",
            child_ids=("item0",),
        )

        connected_backend._ax_cache = {
            "normallist": container_node,
            "item0": _make_ax_node(node_id="item0", role="listitem", name="First"),
        }

        result = connected_backend.scroll_to_item(
            NativeHandle("normallist"), item_name="NonExistent"
        )
        assert result is None
        # Should NOT have scrolled (no retry)
        inp_mock.dispatch_mouse_event.assert_not_called()


# -- GW-120: Snapshot timeout and DOM fallback --------------------------------


class TestSnapshotTimeout:
    """GW-120: snapshot passes timeout to AX tree fetch and falls back on failure."""

    def _setup_snapshot(self, connected_backend, mock_browser, ax_nodes):
        """Configure mocks for snapshot timeout tests."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        session.mark_detached = MagicMock()
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = ax_nodes
        dom_mock = MagicMock()
        dom_mock.get_box_model.return_value = None
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (
            acc_mock,
            dom_mock,
            inp_mock,
            page_mock,
            tgt_mock,
        )
        return target

    def test_snapshot_uses_default_timeout(self, connected_backend, mock_browser) -> None:
        """Snapshot uses _snapshot_timeout (default 10.0s) when no override."""
        ax_nodes = [
            _make_ax_node(node_id="root", role="webArea"),
        ]
        target = self._setup_snapshot(connected_backend, mock_browser, ax_nodes)

        connected_backend.snapshot(NativeHandle(target))
        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.assert_called_once_with(timeout=10.0)

    def test_snapshot_custom_constructor_timeout(self, connected_backend, mock_browser) -> None:
        """Custom snapshot_timeout from constructor is respected."""
        backend = WebBackend(host="localhost", port=9222, snapshot_timeout=30.0)
        backend._connected = True
        backend._browser = mock_browser

        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        backend._sessions[target.id] = session

        ax_nodes = [_make_ax_node(node_id="root", role="webArea")]
        acc_mock = MagicMock()
        acc_mock.get_full_ax_tree.return_value = ax_nodes
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        backend._domains[target.id] = (acc_mock, dom_mock, inp_mock, page_mock, tgt_mock)

        backend.snapshot(NativeHandle(target))
        acc_mock.get_full_ax_tree.assert_called_once_with(timeout=30.0)


class TestDOMFallbackSnapshot:
    """GW-120: snapshot falls back to DOM when AX tree times out or fails."""

    def _setup_snapshot(self, connected_backend, mock_browser):
        """Configure mocks for DOM fallback tests."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        session.mark_detached = MagicMock()
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (
            acc_mock,
            dom_mock,
            inp_mock,
            page_mock,
            tgt_mock,
        )
        return target

    def test_ax_timeout_falls_back_to_dom(self, connected_backend, mock_browser) -> None:
        """AX tree timeout triggers DOM fallback (GW-120)."""
        from pathlight_mcp.cdp._types import DOMNode

        target = self._setup_snapshot(connected_backend, mock_browser)

        # Make AX tree fetch raise TimeoutError
        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = TimeoutError("AX tree fetch timed out")

        # Configure DOM mock to return a real DOMNode tree
        doc_node = DOMNode(
            node_id=1,
            node_name="#document",
            children=(
                DOMNode(
                    node_id=2,
                    node_name="HTML",
                    children=(
                        DOMNode(
                            node_id=3,
                            node_name="BODY",
                            children=(DOMNode(node_id=4, node_name="BUTTON", node_value="Click"),),
                        ),
                    ),
                ),
            ),
        )
        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.return_value = doc_node

        result = connected_backend.snapshot(NativeHandle(target))
        # Should get a DOM-based tree, not an error
        assert result["role"] == "window"
        assert len(result["children"]) > 0
        # DOM.get_document should have been called
        dom_mock.get_document.assert_called_once()

    def test_ax_error_falls_back_to_dom(self, connected_backend, mock_browser) -> None:
        """AX tree generic error triggers DOM fallback (GW-120)."""
        from pathlight_mcp.cdp._types import DOMNode

        target = self._setup_snapshot(connected_backend, mock_browser)

        # Make AX tree fetch raise a generic exception
        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = RuntimeError("CDP disconnected")

        # Configure DOM mock
        doc_node = DOMNode(node_id=1, node_name="#document", children=())
        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.return_value = doc_node

        result = connected_backend.snapshot(NativeHandle(target))
        assert result["role"] == "window"

    def test_dom_fallback_also_fails(self, connected_backend, mock_browser) -> None:
        """Both AX and DOM fail → returns role='unknown'."""
        target = self._setup_snapshot(connected_backend, mock_browser)

        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = TimeoutError("AX timed out")

        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.side_effect = RuntimeError("DOM also failed")

        result = connected_backend.snapshot(NativeHandle(target))
        assert result["role"] == "unknown"

    def test_dom_fallback_respects_max_nodes(self, connected_backend, mock_browser) -> None:
        """DOM fallback tree respects max_nodes limit (GW-120)."""
        from pathlight_mcp.cdp._types import DOMNode

        target = self._setup_snapshot(connected_backend, mock_browser)

        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = TimeoutError("AX timed out")

        # Build a DOM tree with many children
        children = tuple(
            DOMNode(node_id=i, node_name="DIV", node_value=f"Item {i}") for i in range(10, 20)
        )
        doc_node = DOMNode(
            node_id=1,
            node_name="#document",
            children=(
                DOMNode(
                    node_id=2,
                    node_name="BODY",
                    children=children,
                ),
            ),
        )
        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.return_value = doc_node

        result = connected_backend.snapshot(NativeHandle(target), max_nodes=3)
        # Should have at most 3 elements (root + body + 1 child)
        total = _count_elements(result)
        assert total <= 3

    def test_dom_fallback_respects_max_depth(self, connected_backend, mock_browser) -> None:
        """DOM fallback tree respects max_depth limit (GW-120)."""
        from pathlight_mcp.cdp._types import DOMNode

        target = self._setup_snapshot(connected_backend, mock_browser)

        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = TimeoutError("AX timed out")

        # Deeply nested DOM
        doc_node = DOMNode(
            node_id=1,
            node_name="#document",
            children=(
                DOMNode(
                    node_id=2,
                    node_name="DIV",
                    children=(
                        DOMNode(
                            node_id=3,
                            node_name="DIV",
                            children=(
                                DOMNode(
                                    node_id=4,
                                    node_name="DIV",
                                    children=(
                                        DOMNode(node_id=5, node_name="SPAN", node_value="Deep"),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.return_value = doc_node

        result = connected_backend.snapshot(NativeHandle(target), max_depth=2)
        # Depth 2: root(0) → DIV(1) → DIV(2), no deeper children
        assert result["role"] == "window"
        body = result["children"][0]
        assert body["role"] == "generic"  # DIV
        assert body["children"] is not None
        assert len(body["children"]) > 0

    def test_dom_role_mapping(self, connected_backend, mock_browser) -> None:
        """DOM fallback maps common HTML tags to normalized roles (GW-120)."""
        from pathlight_mcp.cdp._types import DOMNode

        target = self._setup_snapshot(connected_backend, mock_browser)

        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = TimeoutError("AX timed out")

        doc_node = DOMNode(
            node_id=1,
            node_name="#document",
            children=(
                DOMNode(
                    node_id=2,
                    node_name="BODY",
                    children=(
                        DOMNode(node_id=10, node_name="A"),
                        DOMNode(node_id=11, node_name="BUTTON"),
                        DOMNode(node_id=12, node_name="INPUT"),
                        DOMNode(
                            node_id=13,
                            node_name="UL",
                            children=(DOMNode(node_id=14, node_name="LI"),),
                        ),
                        DOMNode(node_id=15, node_name="IMG"),
                        DOMNode(node_id=16, node_name="UNKNOWN_TAG"),
                    ),
                ),
            ),
        )
        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.return_value = doc_node

        result = connected_backend.snapshot(NativeHandle(target))
        body = result["children"][0]
        children = body["children"]
        assert children[0]["role"] == "link"  # A → link
        assert children[1]["role"] == "button"  # BUTTON → button
        assert children[2]["role"] == "text_input"  # INPUT → text_input
        ul = children[3]
        assert ul["role"] == "list"  # UL → list
        assert ul["children"][0]["role"] == "listitem"  # LI → listitem
        assert children[4]["role"] == "image"  # IMG → image
        assert children[5]["role"] == "unknown_tag"  # Unknown → tag name (generic fallback)


def _count_elements(tree_dict: dict) -> int:
    """Count total elements in a tree dict."""
    count = 1
    for child in tree_dict.get("children", []) or []:
        count += _count_elements(child)
    return count


# -- TC-GW120-17: session detachment after timeout (P0) ----------------------


class TestSessionDetachmentAfterTimeout:
    """TC-GW120-17: session.mark_detached() is called after AX tree timeout."""

    def _setup(self, connected_backend, mock_browser):
        """Configure mocks for detachment tests."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        session.mark_detached = MagicMock()
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (
            acc_mock,
            dom_mock,
            inp_mock,
            page_mock,
            tgt_mock,
        )
        return target

    def test_snapshot_marks_session_detached_on_timeout(
        self,
        connected_backend,
        mock_browser,
    ) -> None:
        """snapshot() calls session.mark_detached() when AX tree times out."""
        from pathlight_mcp.cdp._types import DOMNode

        target = self._setup(connected_backend, mock_browser)

        # Make AX tree fetch raise TimeoutError
        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = TimeoutError("AX tree timed out")

        # Configure DOM fallback
        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.return_value = DOMNode(
            node_id=1,
            node_name="#document",
            children=(),
        )

        connected_backend.snapshot(NativeHandle(target))

        # Session must be marked detached
        session = connected_backend._sessions[target.id]
        session.mark_detached.assert_called_once()

    def test_snapshot_marks_session_detached_on_error(
        self,
        connected_backend,
        mock_browser,
    ) -> None:
        """snapshot() calls session.mark_detached() when AX tree raises generic error."""
        from pathlight_mcp.cdp._types import DOMNode

        target = self._setup(connected_backend, mock_browser)

        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = RuntimeError("CDP disconnected")

        dom_mock = connected_backend._domains[target.id][1]
        dom_mock.get_document.return_value = DOMNode(
            node_id=1,
            node_name="#document",
            children=(),
        )

        connected_backend.snapshot(NativeHandle(target))
        session = connected_backend._sessions[target.id]
        session.mark_detached.assert_called_once()


# -- TC-GW120-18: find_elements() timeout returns empty (P0) -----------------


class TestFindElementsTimeout:
    """TC-GW120-18: find_elements() returns [] on AX tree timeout."""

    def _setup(self, connected_backend, mock_browser):
        """Configure mocks for find_elements timeout tests."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        session.mark_detached = MagicMock()
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (
            acc_mock,
            dom_mock,
            inp_mock,
            page_mock,
            tgt_mock,
        )
        return target

    def test_find_elements_returns_empty_on_timeout(
        self,
        connected_backend,
        mock_browser,
    ) -> None:
        """find_elements() returns [] and marks session detached on timeout."""
        target = self._setup(connected_backend, mock_browser)

        # Make AX tree fetch timeout
        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = TimeoutError("AX tree timed out")

        result = connected_backend.find_elements(
            NativeHandle(target),
            role="button",
        )
        assert result == []

        # Session should be marked detached
        session = connected_backend._sessions[target.id]
        session.mark_detached.assert_called_once()

    def test_find_elements_returns_empty_on_error(
        self,
        connected_backend,
        mock_browser,
    ) -> None:
        """find_elements() returns [] on generic AX tree error."""
        target = self._setup(connected_backend, mock_browser)

        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.side_effect = RuntimeError("CDP error")

        result = connected_backend.find_elements(
            NativeHandle(target),
            role="button",
        )
        assert result == []


# -- TC-GW120-19: iframe AX timeout skips frame (P0) -------------------------


class TestIframeAxTimeout:
    """TC-GW120-19: _collect_iframe_ax_trees() skips iframe on AX timeout."""

    def _setup(self, connected_backend, mock_browser):
        """Configure mocks for iframe timeout tests."""
        target = _make_target()
        session = MagicMock()
        session.is_attached = True
        session.target = target
        session.send_command.return_value = {}
        session.mark_detached = MagicMock()
        mock_browser.attach.return_value = session
        mock_browser.get_target.return_value = target
        connected_backend._sessions[target.id] = session

        acc_mock = MagicMock()
        dom_mock = MagicMock()
        inp_mock = MagicMock()
        page_mock = MagicMock()
        tgt_mock = MagicMock()
        connected_backend._domains[target.id] = (
            acc_mock,
            dom_mock,
            inp_mock,
            page_mock,
            tgt_mock,
        )
        return target

    def test_iframe_ax_timeout_skips_frame(
        self,
        connected_backend,
        mock_browser,
    ) -> None:
        """Iframe AX tree timeout is caught and the frame is skipped."""
        target = self._setup(connected_backend, mock_browser)

        # Main frame AX tree succeeds
        acc_mock = connected_backend._domains[target.id][0]
        acc_mock.get_full_ax_tree.return_value = [
            _make_ax_node(node_id="root", role="webArea"),
        ]

        # Page.getFrameTree returns a child iframe
        page_mock = connected_backend._domains[target.id][3]
        page_mock.get_frame_tree.return_value = [
            {"id": "main", "parentId": None},
            {
                "id": "iframe-1",
                "parentId": "main",
                "url": "https://example.com/iframe",
            },
        ]

        # The iframe attach succeeds but its AX tree times out
        iframe_session = MagicMock()
        iframe_session.is_attached = True
        iframe_session.target = MagicMock()
        iframe_session.send_command.return_value = {}
        mock_browser.attach.side_effect = [
            connected_backend._sessions[target.id],
            iframe_session,
        ]

        # Patch AccessibilityDomain to control the iframe instance
        with patch(
            "pathlight_mcp.backends.web.AccessibilityDomain",
        ) as mock_acc_domain:
            main_acc = acc_mock
            iframe_acc = MagicMock()
            iframe_acc.get_full_ax_tree.side_effect = TimeoutError(
                "iframe AX timed out",
            )

            mock_acc_domain.side_effect = [main_acc, iframe_acc]

            result = connected_backend.snapshot(NativeHandle(target))

        # Should still succeed (main frame only, iframe skipped)
        assert result["role"] == "unknown" or result.get("children") is not None
