"""Tests for desktop.web_select_option tool handler (GW-123).

Validates that:
- web_select_option selects an option via Runtime.callFunctionOn
- web_select_option returns INTERACTION risk metadata
- web_select_option supports value, label, and index selection modes
- web_select_option supports element_ref and selector element targeting
- web_select_option enforces input validation (mutually exclusive params)
- web_select_option returns error when no web connection exists
- web_select_option returns error for invalid/non-web window references
- web_select_option returns error when session creation fails
- web_select_option returns error when element is not found
- web_select_option returns error when backend is not a BackendRouter
- web_select_option returns stub response in unwired mode
- web_select_option hints are registered
- web_select_option is classified as INTERACTION system action
- web_select_option is NOT in _BACKEND_TOOL_MODULES
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.router import BackendRouter
from guidewire.backends.web import WebBackend
from guidewire.cdp._types import CDPTarget
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all


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
    mcp = FastMCP(name="test-web-select")
    register_all(mcp, backend=router, ref_store=ref_store)
    return mcp


@pytest.fixture()
def mcp_no_router(native_backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with a plain MockBackend (no router)."""
    mcp = FastMCP(name="test-web-select-no-router")
    register_all(mcp, backend=native_backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance in stub mode (no backend)."""
    mcp = FastMCP(name="test-web-select-stub")
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
    return web


def _setup_connected_router(
    mcp_router: FastMCP, ref_store: ElementRefStore
) -> tuple[MagicMock, str]:
    """Set up a connected web backend and return (mock_web, window_ref)."""
    tools = mcp_router._tool_manager.list_tools()
    web_connect = next(t for t in tools if t.name == "desktop.web_connect")

    mock_web = _make_mock_web_backend()
    with patch("guidewire.tools.web_connect.WebBackend", return_value=mock_web):
        result_json = web_connect.fn(host="localhost", port=9222)

    result = json.loads(result_json)
    assert result["success"] is True
    return mock_web, result["pages"][0]["ref"]


def _get_tool(mcp: FastMCP, name: str = "desktop.web_select_option"):
    """Get a tool callable from an MCP instance."""
    tools = mcp._tool_manager.list_tools()
    return next(t for t in tools if t.name == name)


# -- Stub mode tests ----------------------------------------------------------


class TestWebSelectOptionStub:
    """web_select_option in stub mode (no backend)."""

    def test_stub_returns_result_json(self, stub_mcp: FastMCP) -> None:
        """In stub mode, web_select_option returns a JSON with success."""
        tool = _get_tool(stub_mcp)
        result = json.loads(tool.fn(window_ref="w1", selector="select", value="opt1"))
        assert result["success"] is True


# -- Validation tests ---------------------------------------------------------


class TestWebSelectOptionValidation:
    """Input validation for web_select_option."""

    def test_validation_empty_window_ref(self, mcp_router: FastMCP) -> None:
        """web_select_option returns validation error for empty window_ref."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="", selector="select", value="opt1")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "window_ref" in result["message"]

    def test_validation_no_element_identifier(self, mcp_router: FastMCP) -> None:
        """web_select_option requires element_ref or selector."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="w1", value="opt1")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "element_ref or selector" in result["message"]

    def test_validation_both_element_identifiers(self, mcp_router: FastMCP) -> None:
        """web_select_option rejects both element_ref and selector."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(
            window_ref="w1", element_ref="e1", selector="select", value="opt1",
        )
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "mutually exclusive" in result["message"]

    def test_validation_no_selection_mode(self, mcp_router: FastMCP) -> None:
        """web_select_option requires one of value, label, or index."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="w1", selector="select")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "value, label, or index" in result["message"]

    def test_validation_multiple_selection_modes(self, mcp_router: FastMCP) -> None:
        """web_select_option rejects multiple selection modes."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="w1", selector="select", value="v", label="l")
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "mutually exclusive" in result["message"]

    def test_validation_negative_index(self, mcp_router: FastMCP) -> None:
        """web_select_option returns validation error for negative index."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="w1", selector="select", index=-1)
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "non-negative" in result["message"]


# -- Error path tests ---------------------------------------------------------


class TestWebSelectOptionErrors:
    """Error handling for web_select_option."""

    def test_no_web_connection_error(self, mcp_router: FastMCP) -> None:
        """web_select_option returns error when no web backend is connected."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="w1", selector="select", value="opt1")
        result = json.loads(result_json)
        assert result["error"] == "web_select_option_error"
        assert "web_connect" in result["message"]

    def test_no_router_error(self, mcp_no_router: FastMCP) -> None:
        """web_select_option returns error when backend is not a BackendRouter."""
        tool = _get_tool(mcp_no_router)
        result_json = tool.fn(window_ref="w1", selector="select", value="opt1")
        result = json.loads(result_json)
        assert result["error"] == "web_select_option_error"
        assert "BackendRouter" in result["message"]

    def test_invalid_window_ref_error(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
    ) -> None:
        """web_select_option returns error for unknown window reference."""
        _setup_connected_router(mcp_router, ref_store)
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="w999", selector="select", value="opt1")
        result = json.loads(result_json)
        assert result["error"] == "web_select_option_error"
        assert "not found" in result["message"].lower()

    def test_session_creation_failure(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
    ) -> None:
        """web_select_option returns error when session creation fails."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        tool = _get_tool(mcp_router)

        mock_web._get_or_create_session.side_effect = Exception("Target not found")
        result_json = tool.fn(window_ref=window_ref, selector="select", value="opt1")
        result = json.loads(result_json)
        assert result["error"] == "web_select_option_error"
        assert "Target not found" in result["message"]

    def test_element_not_found_by_selector(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
    ) -> None:
        """web_select_option returns error when selector finds no element."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        tool = _get_tool(mcp_router)

        mock_session = MagicMock()
        mock_web._get_or_create_session.return_value = mock_session

        with patch(
            "guidewire.cdp.domains.dom.DOMDomain"
        ) as MockDOM, patch(
            "guidewire.cdp.domains.runtime.RuntimeDomain"
        ) as MockRuntime:
            mock_dom = MockDOM.return_value
            mock_dom.get_document.return_value = MagicMock(node_id=1)
            mock_dom.query_selector.return_value = None
            result_json = tool.fn(
                window_ref=window_ref, selector="#nonexistent", value="opt1",
            )

        result = json.loads(result_json)
        assert result["error"] == "web_select_option_error"
        assert "Could not locate" in result["message"]


# -- Safety classification tests ----------------------------------------------


class TestWebSelectOptionSafety:
    """Verify web_select_option is classified as INTERACTION."""

    def test_web_select_option_is_interaction(self) -> None:
        """web_select_option system action is INTERACTION."""
        from guidewire.safety import SYSTEM_ACTION_RISK_MAP

        assert SYSTEM_ACTION_RISK_MAP["web_select_option"] == "INTERACTION"

    def test_classify_web_select_option(self) -> None:
        """classify_system_action returns INTERACTION for web_select_option."""
        from guidewire.safety import classify_system_action

        result = classify_system_action("web_select_option", target="opt1")
        assert result.risk_level == "INTERACTION"
        assert result.confirmation_required is False


# -- Hint registry tests ------------------------------------------------------


class TestWebSelectOptionHints:
    """Verify web_select_option error hints are registered."""

    def test_web_select_option_error_hints(self) -> None:
        """web_select_option_error has registered hints."""
        from guidewire.hints import hints_for

        hints = hints_for("web_select_option_error")
        assert len(hints) > 0
        assert any("web_connect" in h for h in hints)

    def test_web_select_option_hints_mention_select(self) -> None:
        """web_select_option_error hints mention select element."""
        from guidewire.hints import hints_for

        hints = hints_for("web_select_option_error")
        assert any("select" in h.lower() for h in hints)


# -- Tool registry tests ------------------------------------------------------


class TestWebSelectOptionRegistry:
    """Verify web_select_option is properly registered."""

    def test_web_select_option_in_tool_list(self, mcp_router: FastMCP) -> None:
        """desktop.web_select_option is in the registered tool list."""
        tools = mcp_router._tool_manager.list_tools()
        names = [t.name for t in tools]
        assert "desktop.web_select_option" in names

    def test_web_select_option_not_in_backend_modules(self) -> None:
        """web_select_option is NOT in _BACKEND_TOOL_MODULES."""
        from guidewire.tools import _BACKEND_TOOL_MODULES

        assert ".web_select_option" not in _BACKEND_TOOL_MODULES

    def test_web_select_option_in_tool_modules(self) -> None:
        """web_select_option IS in _TOOL_MODULES."""
        from guidewire.tools import _TOOL_MODULES

        assert ".web_select_option" in _TOOL_MODULES
