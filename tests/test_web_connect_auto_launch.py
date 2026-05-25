"""Tests for web_connect auto-launch, browser parameter integration (GW-114),
and internal page filtering (GW-115).

Covers:
- browser parameter validation (valid names accepted, invalid rejected)
- auto_launch=False disables auto-launch and returns error on failure
- auto_launch=True triggers auto-launch on connection failure
- Desktop automation fallback hint in error messages
- Auto-launched browser process tracking
- Browser override parameter passed through to BrowserResolver
- Internal browser pages filtered from target discovery (GW-115)
- Existing tests still pass (no regression)
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.router import BackendRouter
from guidewire.backends.web import WebBackend
from guidewire.cdp._types import CDPTarget
from guidewire.errors import BackendUnavailableError
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all

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
    mcp = FastMCP(name="test-auto-launch")
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


# -- Browser parameter validation --


class TestBrowserParameterValidation:
    """web_connect validates the browser parameter."""

    def test_valid_browser_names_accepted(self, mcp_router: FastMCP) -> None:
        """Valid browser names are accepted (no validation error)."""
        tool = _get_web_connect_tool(mcp_router)

        # Connection will fail but browser name should not cause validation error
        with patch("guidewire.tools.web_connect.WebBackend") as mock_wb:
            mock_instance = MagicMock(spec=WebBackend)
            mock_instance.connect.side_effect = BackendUnavailableError("fail")
            mock_wb.return_value = mock_instance

            with patch("guidewire.tools.web_connect._try_auto_launch", return_value=False):
                result = json.loads(tool.fn(browser="chrome"))
                assert result["error"] != "validation_error"

    def test_invalid_browser_name_returns_error(self, mcp_router: FastMCP) -> None:
        """Invalid browser name returns a validation error with available options."""
        tool = _get_web_connect_tool(mcp_router)

        result = json.loads(tool.fn(browser="firefox"))
        assert result["error"] == "validation_error"
        assert "firefox" in result["message"]
        assert "edge" in result["message"] or "chrome" in result["message"]

    def test_invalid_browser_name_lists_options(self, mcp_router: FastMCP) -> None:
        """Error for invalid browser lists all available options."""
        tool = _get_web_connect_tool(mcp_router)

        result = json.loads(tool.fn(browser="safari"))
        assert result["error"] == "validation_error"
        for name in ("edge", "chrome", "brave", "chromium"):
            assert name in result["message"]

    def test_browser_parameter_case_insensitive(self, mcp_router: FastMCP) -> None:
        """Browser parameter is case-insensitive — 'Chrome' is accepted."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("fail")

        with (
            patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web),
            patch("guidewire.tools.web_connect._try_auto_launch", return_value=False),
        ):
            result = json.loads(tool.fn(browser="Chrome"))
            # No validation error — case-insensitive match accepted
            assert result["error"] != "validation_error"

    def test_browser_null_is_valid(self, mcp_router: FastMCP) -> None:
        """browser=None (default) skips validation and uses discovery."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = _make_mock_web_backend()
        with patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn())
            assert result["success"] is True


# -- auto_launch=False disables auto-launch --


class TestAutoLaunchDisabled:
    """auto_launch=False preserves the original connect-only behavior."""

    def test_auto_launch_false_returns_error_on_failure(self, mcp_router: FastMCP) -> None:
        """When auto_launch=False and connection fails, returns error without auto-launching."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("Connection refused")

        with (
            patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web),
            patch("guidewire.tools.web_connect._try_auto_launch") as mock_launch,
        ):
            result = json.loads(tool.fn(auto_launch=False))
            assert result["error"] == "web_connect_error"
            mock_launch.assert_not_called()

    def test_auto_launch_false_includes_fallback_hint(self, mcp_router: FastMCP) -> None:
        """Error message includes desktop automation fallback hint when auto_launch=False."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("Connection refused")

        with patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn(auto_launch=False))
            assert result["error"] == "web_connect_error"
            assert any("auto_launch" in h or "desktop automation" in h for h in result["hints"])


# -- auto_launch=True triggers auto-launch on failure --


class TestAutoLaunchEnabled:
    """auto_launch=True triggers auto-launch when connection fails."""

    def test_auto_launch_attempted_on_connection_failure(self, mcp_router: FastMCP) -> None:
        """Auto-launch is attempted when the initial connection fails."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web_fail = MagicMock(spec=WebBackend)
        mock_web_fail.connect.side_effect = BackendUnavailableError("Connection refused")

        mock_web_success = _make_mock_web_backend()

        call_count = [0]

        def web_backend_side_effect(*args: object, **kwargs: object) -> MagicMock:
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_web_fail
            return mock_web_success

        with (
            patch("guidewire.tools.web_connect.WebBackend", side_effect=web_backend_side_effect),
            patch("guidewire.tools.web_connect._try_auto_launch", return_value=True),
        ):
            result = json.loads(tool.fn(auto_launch=True))
            assert result["success"] is True

    def test_auto_launch_failure_returns_fallback_error(self, mcp_router: FastMCP) -> None:
        """When auto-launch fails, returns error with desktop automation fallback."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("Connection refused")

        with (
            patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web),
            patch("guidewire.tools.web_connect._try_auto_launch", return_value=False),
        ):
            result = json.loads(tool.fn(auto_launch=True))
            assert result["error"] == "web_connect_error"
            assert any("desktop automation" in h or "Auto-launch" in h for h in result["hints"])

    def test_auto_launched_flag_in_response(self, mcp_router: FastMCP) -> None:
        """Response includes auto_launched=True when a browser was auto-launched."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web_fail = MagicMock(spec=WebBackend)
        mock_web_fail.connect.side_effect = BackendUnavailableError("fail")

        mock_web_success = _make_mock_web_backend()
        mock_resolver = MagicMock()
        mock_resolver.spawned_process = MagicMock()  # Non-None = browser was launched

        call_count = [0]

        def web_backend_side_effect(*args: object, **kwargs: object) -> MagicMock:
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_web_fail
            return mock_web_success

        with (
            patch("guidewire.tools.web_connect.WebBackend", side_effect=web_backend_side_effect),
            patch("guidewire.tools.web_connect._try_auto_launch", return_value=True),
            patch("guidewire.tools.web_connect._get_resolver", return_value=mock_resolver),
        ):
            result = json.loads(tool.fn(auto_launch=True))
            assert result["success"] is True
            assert result.get("auto_launched") is True

    def test_no_auto_launched_flag_when_not_launched(self, mcp_router: FastMCP) -> None:
        """Response does NOT include auto_launched when no browser was launched."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = _make_mock_web_backend()

        with (
            patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web),
        ):
            result = json.loads(tool.fn())
            assert result["success"] is True
            assert "auto_launched" not in result

    def test_browser_override_passed_to_auto_launch(self, mcp_router: FastMCP) -> None:
        """The browser parameter is passed through to the auto-launch function."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("fail")

        with (
            patch(
                "guidewire.tools.web_connect.WebBackend",
                return_value=mock_web,
            ),
            patch(
                "guidewire.tools.web_connect._try_auto_launch",
                return_value=False,
            ) as mock_launch,
        ):
            tool.fn(browser="edge", auto_launch=True)
            # Check that "edge" was passed to _try_auto_launch
            mock_launch.assert_called_once()
            call_args = mock_launch.call_args
            assert call_args[0][2] == "edge"  # third positional arg is browser


# -- Regression: existing tests still pass --


class TestExistingBehaviorPreserved:
    """Existing web_connect behavior is not broken by auto-launch changes."""

    def test_direct_connect_still_works(self, mcp_router: FastMCP) -> None:
        """Direct connection (no auto-launch needed) still works."""
        tool = _get_web_connect_tool(mcp_router)

        mock_web = _make_mock_web_backend()
        with patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web):
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
        with patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web):
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


# -- Internal page filtering tests (GW-115) --


class TestInternalPageFiltering:
    """Internal browser pages are filtered from target discovery."""

    def test_chrome_newtab_filtered(self) -> None:
        """chrome://newtab pages are filtered out."""
        from guidewire.tools.web_connect import _is_internal_page

        assert _is_internal_page("chrome://newtab") is True

    def test_edge_newtab_filtered(self) -> None:
        """edge://newtab pages are filtered out."""
        from guidewire.tools.web_connect import _is_internal_page

        assert _is_internal_page("edge://newtab") is True

    def test_about_blank_filtered(self) -> None:
        """about:blank pages are filtered out."""
        from guidewire.tools.web_connect import _is_internal_page

        assert _is_internal_page("about:blank") is True

    def test_about_newtab_filtered(self) -> None:
        """about:newtab pages are filtered out."""
        from guidewire.tools.web_connect import _is_internal_page

        assert _is_internal_page("about:newtab") is True

    def test_chrome_extension_filtered(self) -> None:
        """chrome-extension:// pages are filtered out."""
        from guidewire.tools.web_connect import _is_internal_page

        assert _is_internal_page("chrome-extension://abc123/popup.html") is True

    def test_https_pages_not_filtered(self) -> None:
        """https:// pages are NOT filtered."""
        from guidewire.tools.web_connect import _is_internal_page

        assert _is_internal_page("https://example.com") is False

    def test_http_pages_not_filtered(self) -> None:
        """http:// pages are NOT filtered."""
        from guidewire.tools.web_connect import _is_internal_page

        assert _is_internal_page("http://localhost:3000") is False

    def test_empty_url_not_filtered(self) -> None:
        """Empty URLs are NOT filtered."""
        from guidewire.tools.web_connect import _is_internal_page

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

        with patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web):
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

        with patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web):
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

        with patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web):
            result = json.loads(tool.fn(host="localhost", port=9222))

        assert result["success"] is True
        assert len(result["pages"]) == 2
        urls = [p["url"] for p in result["pages"]]
        assert "https://google.com" in urls
        assert "https://github.com" in urls
