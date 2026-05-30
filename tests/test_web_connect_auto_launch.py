"""Tests for web_connect connect-only behavior (GW-141) and internal page
filtering (GW-115).

After removing auto-launch (GW-141), web_connect only connects to already-
running browsers. When no browser is found, it returns a structured error
with instructions to start one via desktop.launch_app.

Covers:
- Connection failure returns structured error with launch_app instructions
- Error message references --remote-debugging-port
- Direct connection still works when a browser is running
- Stub mode (no backend) returns plain message
- Input validation unchanged
- Already-connected state returns existing pages
- No-router error unchanged
- Internal browser pages filtered from target discovery (GW-115)
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.router import BackendRouter
from pathlight_mcp.backends.web import WebBackend
from pathlight_mcp.cdp._types import CDPTarget
from pathlight_mcp.errors import BackendUnavailableError
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def native_backend() -> MockBackend:
    return MockBackend().add_window(title="Explorer", app="explorer.exe")


@pytest.fixture()
def ref_store() -> ElementRefStore:
    return ElementRefStore()


@pytest.fixture()
def mcp_router(native_backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    router = BackendRouter(native=native_backend)
    mcp = FastMCP(name="test-web-connect")
    register_all(mcp, backend=router, ref_store=ref_store)
    return mcp


def _get_web_connect_tool(mcp: FastMCP) -> MagicMock:
    """Get the web_connect tool function from the MCP server."""
    tools = mcp._tool_manager.list_tools()
    return next(t for t in tools if t.name == "desktop.web_connect")


def _make_mock_web_backend(pages=None):
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
    return web


# -- Connection failure returns structured error --


class TestNoBrowserError:
    """When no browser is found, return structured error with instructions."""

    def test_error_on_connection_failure(self, mcp_router: FastMCP) -> None:
        """Connection failure returns web_connect_error."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("Connection refused")

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn())
            assert result["error"] == "web_connect_error"
            assert "No browser found" in result["message"]

    def test_error_includes_launch_app_instruction(self, mcp_router: FastMCP) -> None:
        """Error message instructs to use desktop.launch_app."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("Connection refused")

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn())
            assert "desktop.launch_app" in result["message"]

    def test_error_includes_remote_debugging_port(self, mcp_router: FastMCP) -> None:
        """Error message mentions --remote-debugging-port."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("Connection refused")

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn())
            assert "--remote-debugging-port" in result["message"]

    def test_error_includes_port_from_params(self, mcp_router: FastMCP) -> None:
        """Error message includes the port number from the request."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("Connection refused")

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn(port=9333))
            assert "9333" in result["message"]

    def test_error_includes_hints(self, mcp_router: FastMCP) -> None:
        """Error includes hints from the hint registry."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("Connection refused")

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn())
            assert "hints" in result
            assert isinstance(result["hints"], list)
            # At least one hint should reference launch_app
            assert any("launch_app" in h for h in result["hints"])


# -- No auto_launch parameter --


class TestNoAutoLaunchParam:
    """web_connect does not accept auto_launch or browser parameters."""

    def test_no_auto_launch_param(self, mcp_router: FastMCP) -> None:
        """Tool does not accept auto_launch parameter."""
        tool = _get_web_connect_tool(mcp_router)
        import inspect

        sig = inspect.signature(tool.fn)
        assert "auto_launch" not in sig.parameters
        assert "browser" not in sig.parameters

    def test_no_auto_launch_in_docstring(self, mcp_router: FastMCP) -> None:
        """Docstring does not mention auto_launch."""
        tool = _get_web_connect_tool(mcp_router)
        assert "auto_launch" not in (tool.description or "")

    def test_no_browser_resolver_import(self) -> None:
        """Module does not import BrowserResolver."""
        import pathlight_mcp.tools.web_connect as wc_module

        assert not hasattr(wc_module, "BrowserResolver")
        assert not hasattr(wc_module, "_resolver")
        assert not hasattr(wc_module, "_get_resolver")
        assert not hasattr(wc_module, "_try_auto_launch")


# -- Existing behavior preserved --


class TestExistingBehaviorPreserved:
    """Existing web_connect behavior is not broken by removing auto-launch."""

    def test_direct_connect_still_works(self, mcp_router: FastMCP) -> None:
        """Direct connection still works."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = _make_mock_web_backend()
        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn(host="localhost", port=9222))
            assert result["success"] is True
            assert result["pages"]

    def test_stub_mode_still_works(self) -> None:
        """Stub mode (no backend) still returns a plain message."""
        mcp = FastMCP(name="test-stub")
        register_all(mcp)
        tools = mcp._tool_manager.list_tools()
        tool = next(t for t in tools if t.name == "desktop.web_connect")
        result = tool.fn(host="localhost", port=9222)
        assert "Connected to localhost:9222" in result

    def test_validation_errors_unchanged(self, mcp_router: FastMCP) -> None:
        """Input validation errors are unchanged."""
        tool = _get_web_connect_tool(mcp_router)

        # Empty host
        result = json.loads(tool.fn(host=""))
        assert result["error"] == "validation_error"

        # Invalid port
        result = json.loads(tool.fn(port=0))
        assert result["error"] == "validation_error"

        # Port too high
        result = json.loads(tool.fn(port=70000))
        assert result["error"] == "validation_error"

    def test_already_connected_still_works(self, mcp_router: FastMCP) -> None:
        """Already-connected state still returns existing pages."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = _make_mock_web_backend()
        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            # First connect
            result1 = json.loads(tool.fn(host="localhost", port=9222))
            assert result1["success"] is True

            # Second connect returns existing
            result2 = json.loads(tool.fn(host="localhost", port=9222))
            assert result2["success"] is True
            assert "Already connected" in result2.get("warning", "")

    def test_no_router_error_unchanged(self) -> None:
        """Error for non-router backend is unchanged."""
        mcp = FastMCP(name="test-no-router")
        mock_backend = MockBackend()
        register_all(mcp, backend=mock_backend, ref_store=ElementRefStore())
        tools = mcp._tool_manager.list_tools()
        tool = next(t for t in tools if t.name == "desktop.web_connect")

        result = json.loads(tool.fn(host="localhost", port=9222))
        assert result["error"] == "web_connect_error"
        assert "BackendRouter" in result["message"]

    def test_no_auto_launched_flag_in_response(self, mcp_router: FastMCP) -> None:
        """Response never includes auto_launched flag."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = _make_mock_web_backend()
        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn())
            assert result["success"] is True
            assert "auto_launched" not in result


# -- Internal page filtering tests (GW-115) --


class TestInternalPageFiltering:
    """Internal browser pages are filtered from target discovery."""

    def test_chrome_newtab_filtered(self) -> None:
        """chrome://newtab pages are filtered out."""
        from pathlight_mcp.tools.web_connect import _is_internal_page

        assert _is_internal_page("chrome://newtab") is True

    def test_edge_newtab_filtered(self) -> None:
        """edge://newtab pages are filtered out."""
        from pathlight_mcp.tools.web_connect import _is_internal_page

        assert _is_internal_page("edge://newtab") is True

    def test_about_blank_filtered(self) -> None:
        """about:blank pages are filtered out."""
        from pathlight_mcp.tools.web_connect import _is_internal_page

        assert _is_internal_page("about:blank") is True

    def test_about_newtab_filtered(self) -> None:
        """about:newtab pages are filtered out."""
        from pathlight_mcp.tools.web_connect import _is_internal_page

        assert _is_internal_page("about:newtab") is True

    def test_chrome_extension_filtered(self) -> None:
        """chrome-extension:// pages are filtered out."""
        from pathlight_mcp.tools.web_connect import _is_internal_page

        assert _is_internal_page("chrome-extension://abc123/popup.html") is True

    def test_https_pages_not_filtered(self) -> None:
        """https:// pages are NOT filtered."""
        from pathlight_mcp.tools.web_connect import _is_internal_page

        assert _is_internal_page("https://example.com") is False

    def test_http_pages_not_filtered(self) -> None:
        """http:// pages are NOT filtered."""
        from pathlight_mcp.tools.web_connect import _is_internal_page

        assert _is_internal_page("http://localhost:3000") is False

    def test_empty_url_not_filtered(self) -> None:
        """Empty URLs are NOT filtered."""
        from pathlight_mcp.tools.web_connect import _is_internal_page

        assert _is_internal_page("") is False

    def test_internal_pages_excluded_from_discovery(self, mcp_router: FastMCP) -> None:
        """Internal browser pages are excluded from web_connect page discovery."""
        tool = _get_web_connect_tool(mcp_router)

        pages = [
            CDPTarget(id="int-1", type="page", title="New Tab", url="chrome://newtab"),
            CDPTarget(id="int-2", type="page", title="", url="about:blank"),
            CDPTarget(id="real-1", type="page", title="Example", url="https://example.com"),
        ]
        mock_web = _make_mock_web_backend(pages=pages)

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn(host="localhost", port=9222))

        assert result["success"] is True
        assert len(result["pages"]) == 1
        assert result["pages"][0]["title"] == "Example"
        assert result["pages"][0]["url"] == "https://example.com"

    def test_all_internal_pages_results_in_empty_list(self, mcp_router: FastMCP) -> None:
        """When all pages are internal, the pages list is empty."""
        tool = _get_web_connect_tool(mcp_router)

        pages = [
            CDPTarget(id="int-1", type="page", title="New Tab", url="chrome://newtab"),
            CDPTarget(id="int-2", type="page", title="", url="about:blank"),
        ]
        mock_web = _make_mock_web_backend(pages=pages)

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn(host="localhost", port=9222))

        assert result["success"] is True
        assert result["pages"] == []

    def test_mixed_pages_only_real_returned(self, mcp_router: FastMCP) -> None:
        """Only non-internal pages are returned from mixed target lists."""
        tool = _get_web_connect_tool(mcp_router)

        pages = [
            CDPTarget(id="int-1", type="page", title="New Tab", url="edge://newtab"),
            CDPTarget(id="real-1", type="page", title="Google", url="https://google.com"),
            CDPTarget(id="int-2", type="page", title="", url="about:blank"),
            CDPTarget(id="real-2", type="page", title="GitHub", url="https://github.com"),
        ]
        mock_web = _make_mock_web_backend(pages=pages)

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn(host="localhost", port=9222))

        assert result["success"] is True
        assert len(result["pages"]) == 2
        urls = [p["url"] for p in result["pages"]]
        assert "https://google.com" in urls
        assert "https://github.com" in urls
