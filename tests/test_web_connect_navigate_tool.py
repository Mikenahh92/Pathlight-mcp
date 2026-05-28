"""Tests for desktop.web_connect and desktop.web_navigate tool handlers (GW-098).

Validates that:
- web_connect establishes a CDP browser connection via WebBackend
- web_connect discovers page targets and returns w-prefixed refs
- web_connect returns SENSITIVE risk metadata
- web_connect handles validation errors (empty host, invalid port)
- web_connect returns error when backend is not a BackendRouter
- web_connect returns error when connection fails
- web_connect handles already-connected state gracefully
- web_navigate navigates a page to a URL and returns success
- web_navigate returns SENSITIVE risk metadata
- web_navigate handles validation errors (empty ref, empty URL, negative timeout)
- web_navigate returns error when no web connection exists
- web_navigate returns error for invalid window references
- web_navigate returns error for non-web window references
- Both tools return stub responses in unwired mode
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.router import BackendRouter, TaggedHandle
from pathlight_mcp.backends.web import WebBackend
from pathlight_mcp.cdp._types import CDPTarget
from pathlight_mcp.errors import BackendUnavailableError
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
    mcp = FastMCP(name="test-web-tools")
    register_all(mcp, backend=router, ref_store=ref_store)
    return mcp


@pytest.fixture()
def mcp_no_router(native_backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with a plain MockBackend (no router)."""
    mcp = FastMCP(name="test-web-tools-no-router")
    register_all(mcp, backend=native_backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance in stub mode (no backend)."""
    mcp = FastMCP(name="test-web-tools-stub")
    register_all(mcp)
    return mcp


def _make_mock_web_backend(pages: list[CDPTarget] | None = None) -> MagicMock:
    """Create a mock WebBackend with optional page targets.

    Args:
        pages: List of CDPTarget instances to return from list_windows.

    Returns:
        A MagicMock configured as a WebBackend.
    """
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


# -- web_connect: stub mode tests --------------------------------------------


class TestWebConnectStub:
    """web_connect in stub mode (no backend)."""

    def test_stub_returns_connect_message(self, stub_mcp: FastMCP) -> None:
        """In stub mode, web_connect returns a plain text message."""
        tools = stub_mcp._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")
        result = web_connect.fn(host="localhost", port=9222)
        assert "Connected to localhost:9222" in result


# -- web_connect: wired mode tests -------------------------------------------


class TestWebConnectWired:
    """web_connect with a BackendRouter."""

    def test_connects_and_discovers_pages(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_connect creates a WebBackend, connects, and returns page refs."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        mock_web = _make_mock_web_backend()
        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result_json = web_connect.fn(host="localhost", port=9222)

        result = json.loads(result_json)
        assert result["success"] is True
        assert result["host"] == "localhost"
        assert result["port"] == 9222
        assert result["risk"] == "sensitive"
        assert result["confirmation_required"] is True
        assert "pages" in result
        assert len(result["pages"]) == 1
        page = result["pages"][0]
        assert page["title"] == "Test Page"
        assert page["url"] == "https://example.com"
        assert page["ref"].startswith("w")

    def test_returns_sensitive_risk_metadata(
        self, mcp_router: FastMCP
    ) -> None:
        """web_connect response includes SENSITIVE-tier risk metadata."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        mock_web = _make_mock_web_backend()
        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result_json = web_connect.fn(host="localhost", port=9222)

        result = json.loads(result_json)
        assert result["risk"] == "sensitive"
        assert result["confirmation_required"] is True

    def test_validation_empty_host(self, mcp_router: FastMCP) -> None:
        """web_connect returns validation error for empty host."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        result_json = web_connect.fn(host="", port=9222)
        result = json.loads(result_json)
        assert result["error"] == "validation_error"

    def test_validation_invalid_port_zero(self, mcp_router: FastMCP) -> None:
        """web_connect returns validation error for port 0."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        result_json = web_connect.fn(host="localhost", port=0)
        result = json.loads(result_json)
        assert result["error"] == "validation_error"

    def test_validation_invalid_port_negative(self, mcp_router: FastMCP) -> None:
        """web_connect returns validation error for negative port."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        result_json = web_connect.fn(host="localhost", port=-1)
        result = json.loads(result_json)
        assert result["error"] == "validation_error"

    def test_validation_invalid_port_too_high(self, mcp_router: FastMCP) -> None:
        """web_connect returns validation error for port > 65535."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        result_json = web_connect.fn(host="localhost", port=70000)
        result = json.loads(result_json)
        assert result["error"] == "validation_error"

    def test_connection_failure(self, mcp_router: FastMCP) -> None:
        """web_connect returns error when connection fails."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        mock_web = MagicMock(spec=WebBackend)
        mock_web.connect.side_effect = BackendUnavailableError("Connection refused")

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result_json = web_connect.fn(host="localhost", port=9222)

        result = json.loads(result_json)
        assert result["error"] == "web_connect_error"
        assert "Connection refused" in result["message"]
        assert len(result["hints"]) > 0

    def test_no_router_error(
        self, mcp_no_router: FastMCP
    ) -> None:
        """web_connect returns error when backend is not a BackendRouter."""
        tools = mcp_no_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        result_json = web_connect.fn(host="localhost", port=9222)
        result = json.loads(result_json)
        assert result["error"] == "web_connect_error"
        assert "BackendRouter" in result["message"]

    def test_already_connected_returns_existing_pages(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_connect returns existing pages when already connected."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        # Pre-configure the router with a web backend
        mock_web = _make_mock_web_backend()
        router = None
        # Find the router from the backend
        for _ in range(1):
            # Access the closure to get the backend
            pass

        # Use the tool with the existing web backend already set
        mock_web2 = _make_mock_web_backend([
            CDPTarget(id="t1", type="page", title="Existing Page", url="https://existing.com"),
        ])
        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web2):
            # First connect
            result1_json = web_connect.fn(host="localhost", port=9222)
            result1 = json.loads(result1_json)
            assert result1["success"] is True

            # Second connect should return existing pages
            result2_json = web_connect.fn(host="localhost", port=9222)
            result2 = json.loads(result2_json)
            assert result2["success"] is True
            assert "warning" in result2
            assert "Already connected" in result2["warning"]

    def test_discovers_multiple_pages(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_connect discovers and returns refs for multiple pages."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        pages = [
            CDPTarget(id="t1", type="page", title="Page 1", url="https://one.com"),
            CDPTarget(id="t2", type="page", title="Page 2", url="https://two.com"),
            CDPTarget(id="t3", type="page", title="Page 3", url="https://three.com"),
        ]
        mock_web = _make_mock_web_backend(pages)
        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result_json = web_connect.fn(host="localhost", port=9222)

        result = json.loads(result_json)
        assert result["success"] is True
        assert len(result["pages"]) == 3
        # All refs should be unique
        refs = [p["ref"] for p in result["pages"]]
        assert len(set(refs)) == 3


# -- web_navigate: stub mode tests -------------------------------------------


class TestWebNavigateStub:
    """web_navigate in stub mode (no backend)."""

    async def test_stub_returns_navigate_message(self, stub_mcp: FastMCP) -> None:
        """In stub mode, web_navigate returns a plain text message."""
        tools = stub_mcp._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")
        result = await web_navigate.fn(window_ref="w1", url="https://example.com")
        assert "Navigated w1 to https://example.com" in result


# -- web_navigate: wired mode tests ------------------------------------------


class TestWebNavigateWired:
    """web_navigate with a BackendRouter and active web connection."""

    def _setup_connected_router(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> tuple[MagicMock, str]:
        """Set up a connected web backend and return (mock_web, window_ref)."""
        tools = mcp_router._tool_manager.list_tools()
        web_connect = next(t for t in tools if t.name == "desktop.web_connect")

        pages = [
            CDPTarget(id="target-1", type="page", title="Test Page", url="https://example.com"),
        ]
        mock_web = _make_mock_web_backend(pages)

        with patch("pathlight_mcp.tools.web_connect.WebBackend", return_value=mock_web):
            result_json = web_connect.fn(host="localhost", port=9222)

        result = json.loads(result_json)
        assert result["success"] is True
        return mock_web, result["pages"][0]["ref"]

    async def test_navigates_to_url(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_navigate navigates a page to a URL and returns success."""
        mock_web, window_ref = self._setup_connected_router(mcp_router, ref_store)

        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        # Mock the session and page domain
        mock_session = MagicMock()
        mock_session.target.id = "target-1"
        mock_session.is_attached = True
        mock_session.send_command.return_value = {
            "frameId": "frame-1",
            "loaderId": "loader-1",
        }
        mock_web._get_or_create_session.return_value = mock_session
        mock_web._browser = MagicMock()
        mock_web._browser.list_targets.return_value = [
            CDPTarget(
                id="target-1",
                type="page",
                title="Example",
                url="https://example.com",
            )
        ]

        result_json = await web_navigate.fn(
            window_ref=window_ref, url="https://example.com", timeout=0
        )
        result = json.loads(result_json)
        assert result["success"] is True
        assert result["url"] == "https://example.com"
        assert result["risk"] == "sensitive"
        assert result["confirmation_required"] is True

    async def test_returns_sensitive_risk_metadata(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_navigate response includes SENSITIVE-tier risk metadata."""
        mock_web, window_ref = self._setup_connected_router(mcp_router, ref_store)

        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        mock_session = MagicMock()
        mock_session.target.id = "target-1"
        mock_session.is_attached = True
        mock_session.send_command.return_value = {
            "frameId": "frame-1",
            "loaderId": "loader-1",
        }
        mock_web._get_or_create_session.return_value = mock_session
        mock_web._browser = MagicMock()
        mock_web._browser.list_targets.return_value = []

        result_json = await web_navigate.fn(
            window_ref=window_ref, url="https://example.com", timeout=0
        )
        result = json.loads(result_json)
        assert result["risk"] == "sensitive"
        assert result["confirmation_required"] is True

    async def test_validation_empty_window_ref(self, mcp_router: FastMCP) -> None:
        """web_navigate returns validation error for empty window_ref."""
        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        result_json = await web_navigate.fn(window_ref="", url="https://example.com")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"

    async def test_validation_empty_url(self, mcp_router: FastMCP) -> None:
        """web_navigate returns validation error for empty URL."""
        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        result_json = await web_navigate.fn(window_ref="w1", url="")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"

    async def test_validation_negative_timeout(self, mcp_router: FastMCP) -> None:
        """web_navigate returns validation error for negative timeout."""
        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        result_json = await web_navigate.fn(
            window_ref="w1", url="https://example.com", timeout=-1
        )
        result = json.loads(result_json)
        assert result["error"] == "validation_error"

    async def test_no_web_connection_error(self, mcp_router: FastMCP) -> None:
        """web_navigate returns error when no web backend is connected."""
        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        # Router has no web backend
        result_json = await web_navigate.fn(
            window_ref="w1", url="https://example.com"
        )
        result = json.loads(result_json)
        assert result["error"] == "web_navigate_error"
        assert "web_connect" in result["message"]

    async def test_invalid_window_ref_error(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_navigate returns error for unknown window reference."""
        # Set up a connected web backend
        mock_web, _ = self._setup_connected_router(mcp_router, ref_store)

        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        result_json = await web_navigate.fn(
            window_ref="w999", url="https://example.com"
        )
        result = json.loads(result_json)
        assert result["error"] == "web_navigate_error"
        assert "not found" in result["message"].lower()

    async def test_non_web_window_ref_error(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
        native_backend: MockBackend,
    ) -> None:
        """web_navigate returns error for native (non-web) window refs."""
        # First connect a web backend so we pass the "no web" check
        mock_web, _ = self._setup_connected_router(mcp_router, ref_store)

        # Store a native window ref (not tagged as "web")
        from pathlight_mcp.backends.types import NativeHandle

        native_handle = NativeHandle("native-window-0")
        native_ref = ref_store.store(native_handle, prefix="w")

        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        result_json = await web_navigate.fn(
            window_ref=native_ref, url="https://example.com"
        )
        result = json.loads(result_json)
        assert result["error"] == "web_navigate_error"
        assert "not a web window" in result["message"]

    async def test_no_router_error(self, mcp_no_router: FastMCP) -> None:
        """web_navigate returns error when backend is not a BackendRouter."""
        tools = mcp_no_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        result_json = await web_navigate.fn(
            window_ref="w1", url="https://example.com"
        )
        result = json.loads(result_json)
        assert result["error"] == "web_navigate_error"

    async def test_navigation_failure(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_navigate returns error when Page.navigate fails."""
        mock_web, window_ref = self._setup_connected_router(mcp_router, ref_store)

        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        mock_session = MagicMock()
        mock_session.target.id = "target-1"
        mock_session.is_attached = True
        mock_session.send_command.side_effect = Exception("Network error")
        mock_web._get_or_create_session.return_value = mock_session

        result_json = await web_navigate.fn(
            window_ref=window_ref, url="https://example.com", timeout=0
        )
        result = json.loads(result_json)
        assert result["error"] == "web_navigate_error"
        assert "Network error" in result["message"]

    async def test_session_creation_failure(
        self, mcp_router: FastMCP, ref_store: ElementRefStore
    ) -> None:
        """web_navigate returns error when session creation fails."""
        mock_web, window_ref = self._setup_connected_router(mcp_router, ref_store)

        tools = mcp_router._tool_manager.list_tools()
        web_navigate = next(t for t in tools if t.name == "desktop.web_navigate")

        mock_web._get_or_create_session.side_effect = Exception("Target not found")

        result_json = await web_navigate.fn(
            window_ref=window_ref, url="https://example.com", timeout=0
        )
        result = json.loads(result_json)
        assert result["error"] == "web_navigate_error"
        assert "Target not found" in result["message"]


# -- Safety classification tests ----------------------------------------------


class TestWebSafetyClassification:
    """Verify web_connect and web_navigate are classified as SENSITIVE."""

    def test_web_connect_is_sensitive(self) -> None:
        """web_connect system action is SENSITIVE."""
        from pathlight_mcp.safety import SYSTEM_ACTION_RISK_MAP

        assert SYSTEM_ACTION_RISK_MAP["web_connect"] == "SENSITIVE"

    def test_web_navigate_is_sensitive(self) -> None:
        """web_navigate system action is SENSITIVE."""
        from pathlight_mcp.safety import SYSTEM_ACTION_RISK_MAP

        assert SYSTEM_ACTION_RISK_MAP["web_navigate"] == "SENSITIVE"

    def test_classify_web_connect(self) -> None:
        """classify_system_action returns SENSITIVE for web_connect."""
        from pathlight_mcp.safety import classify_system_action

        result = classify_system_action("web_connect", target="localhost:9222")
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True
        assert "localhost:9222" in result.reason

    def test_classify_web_navigate(self) -> None:
        """classify_system_action returns SENSITIVE for web_navigate."""
        from pathlight_mcp.safety import classify_system_action

        result = classify_system_action("web_navigate", target="https://example.com")
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True
        assert "example.com" in result.reason


# -- Hint registry tests -----------------------------------------------------


class TestWebHints:
    """Verify web error hints are registered."""

    def test_web_connect_error_hints(self) -> None:
        """web_connect_error has registered hints."""
        from pathlight_mcp.hints import hints_for

        hints = hints_for("web_connect_error")
        assert len(hints) > 0
        assert any("remote-debugging-port" in h for h in hints)

    def test_web_navigate_error_hints(self) -> None:
        """web_navigate_error has registered hints."""
        from pathlight_mcp.hints import hints_for

        hints = hints_for("web_navigate_error")
        assert len(hints) > 0
        assert any("web_connect" in h for h in hints)
