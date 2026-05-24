"""Tests for WebBackend — CDP-based accessibility backend for web browsers (GW-095).

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
"""

from unittest.mock import MagicMock, patch

import pytest

from guidewire.backends.base import DesktopBackend
from guidewire.backends.types import DesktopAction, NativeHandle
from guidewire.backends.web import WebBackend
from guidewire.backends.web_normalize import (
    build_normalized_tree,
    fetch_bounds_from_dom,
    find_root_ax_node,
    infer_ax_actions,
)
from guidewire.cdp._types import AXNode, CDPTarget
from guidewire.errors import (
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
    connected_backend._domains[target.id] = (acc, dom, inp)
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

        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock)

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
        result = connected_backend.snapshot(NativeHandle(target))
        assert result["role"] == "unknown"


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
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock)

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
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock)

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
    """TC-10: is_valid should check the AX cache."""

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
        from guidewire.cdp._types import BoxModel

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
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock)

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
        from guidewire.cdp._types import BoxModel

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
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock)

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
        connected_backend._domains[target.id] = (acc_mock, dom_mock, inp_mock)

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
