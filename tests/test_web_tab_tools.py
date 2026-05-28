"""Tests for desktop.web_list_tabs, desktop.web_tab_action, and desktop.web_frame_tree
tool handlers (GW-124).

Validates:
- web_list_tabs lists browser tabs with metadata
- web_list_tabs returns error when no web connection
- web_list_tabs returns stub in unwired mode
- web_tab_action activate/close/create/navigate actions
- web_tab_action validation (invalid action, missing target_id, missing url)
- web_tab_action returns error when no web connection
- web_tab_action returns stub in unwired mode
- web_frame_tree returns iframe hierarchy via window_ref or target_id
- web_frame_tree validation (neither ref nor target_id)
- web_frame_tree returns error when no web connection
- web_frame_tree returns stub in unwired mode
- Popup detection: enable/disable/pop_detected_targets on WebBackend
"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.router import BackendRouter
from pathlight_mcp.backends.web import WebBackend
from pathlight_mcp.cdp._types import CDPTarget
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def native_backend() -> MockBackend:
    """Return a MockBackend simulating the native backend."""
    return MockBackend().add_window(title="Explorer", app="explorer.exe")


@pytest.fixture()
def ref_store() -> ElementRefStore:
    """Return a fresh ElementRefStore."""
    return ElementRefStore()


@pytest.fixture()
def mcp_router(native_backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a BackendRouter."""
    router = BackendRouter(native=native_backend)
    mcp = FastMCP(name="test-web-tab-tools")
    register_all(mcp, backend=router, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance in stub mode (no backend)."""
    mcp = FastMCP(name="test-web-tab-tools-stub")
    register_all(mcp)
    return mcp


def _make_mock_web_backend(pages: list[CDPTarget] | None = None) -> MagicMock:
    """Create a mock WebBackend with optional page targets."""
    web = MagicMock(spec=WebBackend)
    if pages is None:
        pages = [
            CDPTarget(id="target-1", type="page", title="Test Page", url="https://example.com"),
        ]
    web.list_windows.return_value = pages
    web._disposed = False
    web._connected = True
    web._ax_cache = {}
    web._bounds_cache = {}

    # Mock the browser with list_targets and connection
    mock_browser = MagicMock()
    mock_browser.list_targets.return_value = pages
    mock_browser.connection = MagicMock()
    web._browser = mock_browser

    return web


def _setup_connected_router(
    mcp_router: FastMCP,
    ref_store: ElementRefStore,
    pages: list[CDPTarget] | None = None,
) -> tuple[MagicMock, list[dict]]:
    """Set up a connected web backend and return (mock_web, pages_info)."""
    tools = mcp_router._tool_manager.list_tools()
    web_connect = next(t for t in tools if t.name == "desktop.web_connect")

    if pages is None:
        pages = [
            CDPTarget(id="target-1", type="page", title="Test Page", url="https://example.com"),
        ]
    mock_web = _make_mock_web_backend(pages)

    with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
        result_json = web_connect.fn(host="localhost", port=9222)

    result = json.loads(result_json)
    assert result["success"] is True
    return mock_web, result.get("pages", [])


# -- web_list_tabs: stub mode tests -------------------------------------------


class TestWebListTabsStub:
    """web_list_tabs in stub mode (no backend)."""

    def test_stub_returns_empty_tabs(self, stub_mcp: FastMCP) -> None:
        """In stub mode, web_list_tabs returns empty tabs list."""
        tools = stub_mcp._tool_manager.list_tools()
        web_list_tabs = next(t for t in tools if t.name == "desktop.web_list_tabs")
        result = json.loads(web_list_tabs.fn())
        assert result["success"] is True
        assert result["tabs"] == []
        assert result["tab_count"] == 0


# -- web_list_tabs: wired mode tests ------------------------------------------


class TestWebListTabsWired:
    """web_list_tabs with a BackendRouter and active web connection."""

    def test_lists_tabs(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_list_tabs returns tab metadata."""
        pages = [
            CDPTarget(id="t1", type="page", title="Tab 1", url="https://one.com"),
            CDPTarget(id="t2", type="page", title="Tab 2", url="https://two.com"),
        ]
        _mock_web, _ = _setup_connected_router(mcp_router, ref_store, pages=pages)

        tools = mcp_router._tool_manager.list_tools()
        web_list_tabs = next(t for t in tools if t.name == "desktop.web_list_tabs")
        result = json.loads(web_list_tabs.fn())

        assert result["success"] is True
        assert result["tab_count"] == 2
        assert result["tabs"][0]["target_id"] == "t1"
        assert result["tabs"][0]["title"] == "Tab 1"
        assert result["tabs"][0]["url"] == "https://one.com"
        assert result["tabs"][1]["target_id"] == "t2"

    def test_no_web_connection_error(self, mcp_router: FastMCP) -> None:
        """web_list_tabs returns error when no web connection exists."""
        tools = mcp_router._tool_manager.list_tools()
        web_list_tabs = next(t for t in tools if t.name == "desktop.web_list_tabs")
        result = json.loads(web_list_tabs.fn())
        assert result["error"] == "web_list_tabs_error"
        assert "web_connect" in result["message"]

    def test_discovery_failure(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_list_tabs returns error when target discovery fails."""
        mock_web, _ = _setup_connected_router(mcp_router, ref_store)
        mock_web._browser.list_targets.side_effect = Exception("Network error")

        tools = mcp_router._tool_manager.list_tools()
        web_list_tabs = next(t for t in tools if t.name == "desktop.web_list_tabs")
        result = json.loads(web_list_tabs.fn())

        assert result["error"] == "web_list_tabs_error"
        assert "Network error" in result["message"]


# -- web_tab_action: stub mode tests ------------------------------------------


class TestWebTabActionStub:
    """web_tab_action in stub mode."""

    def test_stub_returns_success(self, stub_mcp: FastMCP) -> None:
        """In stub mode, web_tab_action returns success."""
        tools = stub_mcp._tool_manager.list_tools()
        web_tab_action = next(t for t in tools if t.name == "desktop.web_tab_action")
        result = json.loads(web_tab_action.fn(action="activate", target_id="t1"))
        assert result["success"] is True
        assert result["action"] == "activate"


# -- web_tab_action: wired mode tests -----------------------------------------


class TestWebTabActionWired:
    """web_tab_action with a BackendRouter and active web connection."""

    def _setup_with_connection(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> MagicMock:
        """Set up a connected router and return the mock_web."""
        mock_web, _ = _setup_connected_router(mcp_router, ref_store)
        return mock_web

    def _get_tool(self, mcp_router: FastMCP) -> Any:
        tools = mcp_router._tool_manager.list_tools()
        return next(t for t in tools if t.name == "desktop.web_tab_action")

    def test_invalid_action(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_tab_action returns validation error for invalid action."""
        self._setup_with_connection(mcp_router, ref_store)
        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="explode", target_id="t1"))
        assert result["error"] == "validation_error"
        assert "Invalid action" in result["message"]

    def test_activate_missing_target_id(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_tab_action activate requires target_id."""
        self._setup_with_connection(mcp_router, ref_store)
        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="activate"))
        assert result["error"] == "validation_error"

    def test_activate_tab(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_tab_action activate brings a tab to foreground."""
        mock_web = self._setup_with_connection(mcp_router, ref_store)
        mock_connection = MagicMock()
        mock_web._browser.connection = mock_connection
        mock_connection.send_command.return_value = {}

        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="activate", target_id="target-1"))

        assert result["success"] is True
        assert result["action"] == "activate"
        assert result["target_id"] == "target-1"
        mock_connection.send_command.assert_called_once_with(
            "Target.activateTarget", {"targetId": "target-1"}
        )

    def test_close_tab(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_tab_action close closes a tab."""
        mock_web = self._setup_with_connection(mcp_router, ref_store)
        mock_connection = MagicMock()
        mock_web._browser.connection = mock_connection
        mock_connection.send_command.return_value = {"success": True}

        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="close", target_id="target-1"))

        assert result["success"] is True
        assert result["action"] == "close"
        mock_connection.send_command.assert_called_once_with(
            "Target.closeTarget", {"targetId": "target-1"}
        )

    def test_new_tab(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_tab_action new opens a new tab."""
        mock_web = self._setup_with_connection(mcp_router, ref_store)
        mock_connection = MagicMock()
        mock_web._browser.connection = mock_connection
        mock_connection.send_command.return_value = {"targetId": "new-tab-1"}

        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="new", url="https://example.com"))

        assert result["success"] is True
        assert result["action"] == "new"
        assert result["target_id"] == "new-tab-1"
        assert result["url"] == "https://example.com"

    def test_new_tab_default_url(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_tab_action new uses about:blank when url not provided."""
        mock_web = self._setup_with_connection(mcp_router, ref_store)
        mock_connection = MagicMock()
        mock_web._browser.connection = mock_connection
        mock_connection.send_command.return_value = {"targetId": "new-tab-2"}

        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="new"))

        assert result["success"] is True
        assert result["url"] == "about:blank"

    def test_navigate_tab(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_tab_action navigate navigates a tab to a URL."""
        mock_web = self._setup_with_connection(mcp_router, ref_store)
        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="navigate", target_id="target-1", url="https://new.com"))

        assert result["success"] is True
        assert result["action"] == "navigate"
        assert result["url"] == "https://new.com"

    def test_navigate_missing_url(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_tab_action navigate requires url."""
        self._setup_with_connection(mcp_router, ref_store)
        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="navigate", target_id="target-1"))
        assert result["error"] == "validation_error"

    def test_navigate_missing_target_id(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_tab_action navigate requires target_id."""
        self._setup_with_connection(mcp_router, ref_store)
        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="navigate", url="https://example.com"))
        assert result["error"] == "validation_error"

    def test_no_web_connection_error(self, mcp_router: FastMCP) -> None:
        """web_tab_action returns error when no web connection."""
        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="activate", target_id="t1"))
        assert result["error"] == "web_tab_action_error"

    def test_no_browser_connection(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_tab_action returns error when browser connection is None."""
        mock_web = self._setup_with_connection(mcp_router, ref_store)
        mock_web._browser.connection = None

        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(action="activate", target_id="target-1"))
        assert result["error"] == "web_tab_action_error"


# -- web_frame_tree: stub mode tests ------------------------------------------


class TestWebFrameTreeStub:
    """web_frame_tree in stub mode."""

    def test_stub_returns_empty_tree(self, stub_mcp: FastMCP) -> None:
        """In stub mode, web_frame_tree returns empty tree."""
        tools = stub_mcp._tool_manager.list_tools()
        web_frame_tree = next(t for t in tools if t.name == "desktop.web_frame_tree")
        result = json.loads(web_frame_tree.fn())
        assert result["success"] is True
        assert result["tree"] == {}
        assert result["frame_count"] == 0


# -- web_frame_tree: wired mode tests -----------------------------------------


class TestWebFrameTreeWired:
    """web_frame_tree with a BackendRouter and active web connection."""

    def _setup_with_connection(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> tuple[MagicMock, list[dict]]:
        return _setup_connected_router(mcp_router, ref_store)

    def _get_tool(self, mcp_router: FastMCP) -> Any:
        tools = mcp_router._tool_manager.list_tools()
        return next(t for t in tools if t.name == "desktop.web_frame_tree")

    def test_frame_tree_by_window_ref(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_frame_tree returns nested tree by window_ref."""
        mock_web, pages = self._setup_with_connection(mcp_router, ref_store)
        window_ref = pages[0]["ref"]

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session
        mock_page = MagicMock()
        mock_page.get_frame_tree_raw.return_value = {
            "frame": {"id": "main", "url": "https://example.com", "name": ""},
            "childFrames": [
                {
                    "frame": {"id": "iframe-1", "url": "https://embed.com", "name": "embed"},
                }
            ],
        }

        with patch("pathlight_mcp.cdp.domains.page.PageDomain", return_value=mock_page):
            tool = self._get_tool(mcp_router)
            result = json.loads(tool.fn(window_ref=window_ref))

        assert result["success"] is True
        assert result["frame_count"] == 2
        # Main frame
        tree = result["tree"]
        assert tree["id"] == "main"
        assert tree["is_main"] is True
        # Iframe as nested child
        assert "children" in tree
        assert tree["children"][0]["id"] == "iframe-1"
        assert tree["children"][0]["is_main"] is False

    def test_frame_tree_by_target_id(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_frame_tree returns nested tree by target_id."""
        mock_web, _ = self._setup_with_connection(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session
        mock_page = MagicMock()
        mock_page.get_frame_tree_raw.return_value = {
            "frame": {"id": "main", "url": "https://example.com"},
        }

        with patch("pathlight_mcp.cdp.domains.page.PageDomain", return_value=mock_page):
            tool = self._get_tool(mcp_router)
            result = json.loads(tool.fn(target_id="target-1"))

        assert result["success"] is True
        assert result["frame_count"] == 1
        assert result["target_id"] == "target-1"
        assert result["tree"]["id"] == "main"

    def test_neither_ref_nor_target_id(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_frame_tree returns error when neither ref nor target_id."""
        self._setup_with_connection(mcp_router, ref_store)
        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn())
        assert result["error"] == "validation_error"

    def test_no_web_connection(self, mcp_router: FastMCP) -> None:
        """web_frame_tree returns error when no web connection."""
        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(target_id="t1"))
        assert result["error"] == "web_frame_tree_error"

    def test_session_failure(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_frame_tree returns error when session creation fails."""
        mock_web, _ = self._setup_with_connection(mcp_router, ref_store)
        mock_web._get_or_create_session.side_effect = Exception("Session failed")

        tool = self._get_tool(mcp_router)
        result = json.loads(tool.fn(target_id="bad-target"))
        assert result["error"] == "web_frame_tree_error"
        assert "Session failed" in result["message"]

    def test_nested_iframes(self, mcp_router: FastMCP, ref_store: ElementRefStore) -> None:
        """web_frame_tree correctly handles nested iframes."""
        mock_web, _ = self._setup_with_connection(mcp_router, ref_store)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session
        mock_page = MagicMock()
        mock_page.get_frame_tree_raw.return_value = {
            "frame": {"id": "main", "url": "https://example.com"},
            "childFrames": [
                {
                    "frame": {"id": "iframe-1", "url": "https://embed.com"},
                    "childFrames": [
                        {
                            "frame": {"id": "iframe-2", "url": "https://nested.com"},
                        }
                    ],
                }
            ],
        }

        with patch("pathlight_mcp.cdp.domains.page.PageDomain", return_value=mock_page):
            tool = self._get_tool(mcp_router)
            result = json.loads(tool.fn(target_id="target-1"))

        assert result["success"] is True
        assert result["frame_count"] == 3
        # Verify nesting: iframe-2 should be nested inside iframe-1
        tree = result["tree"]
        assert tree["id"] == "main"
        iframe_1 = tree["children"][0]
        assert iframe_1["id"] == "iframe-1"
        assert iframe_1["children"][0]["id"] == "iframe-2"


# -- Popup detection tests ----------------------------------------------------


class TestPopupDetection:
    """WebBackend popup detection (GW-124)."""

    def test_enable_popup_detection(self) -> None:
        """enable_popup_detection calls Target.setDiscoverTargets."""
        web = MagicMock(spec=WebBackend)
        web._disposed = False
        web._connected = True

        mock_session = MagicMock()
        web._get_active_session.return_value = mock_session

        # Call the real method
        WebBackend.enable_popup_detection(web)

        mock_session.send_command.assert_called()

    def test_pop_detected_targets_empty(self) -> None:
        """pop_detected_targets returns empty when no events."""
        web = MagicMock(spec=WebBackend)
        mock_session = MagicMock()
        mock_session._connection.events.get_by_method.return_value = []
        web._get_active_session.return_value = mock_session

        targets = WebBackend.pop_detected_targets(web)
        assert targets == []

    def test_pop_detected_targets_with_events(self) -> None:
        """pop_detected_targets returns targets from events."""
        web = MagicMock(spec=WebBackend)
        mock_session = MagicMock()

        # Simulate a Target.targetCreated event
        mock_event = MagicMock()
        mock_event.params = {
            "targetInfo": {
                "targetId": "popup-1",
                "type": "page",
                "title": "Popup",
                "url": "https://popup.com",
            }
        }
        mock_session._connection.events.get_by_method.return_value = [mock_event]
        web._get_active_session.return_value = mock_session

        targets = WebBackend.pop_detected_targets(web)
        assert len(targets) == 1
        assert targets[0].id == "popup-1"
        assert targets[0].type == "page"

    def test_pop_detected_targets_filters_non_page(self) -> None:
        """pop_detected_targets only returns page-type targets."""
        web = MagicMock(spec=WebBackend)
        mock_session = MagicMock()

        mock_event = MagicMock()
        mock_event.params = {
            "targetInfo": {
                "targetId": "worker-1",
                "type": "service_worker",
                "title": "Worker",
                "url": "https://example.com/sw.js",
            }
        }
        mock_session._connection.events.get_by_method.return_value = [mock_event]
        web._get_active_session.return_value = mock_session

        targets = WebBackend.pop_detected_targets(web)
        assert len(targets) == 0
