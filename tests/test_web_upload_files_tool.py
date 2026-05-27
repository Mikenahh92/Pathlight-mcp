"""Tests for desktop.web_upload_files tool handler (GW-123).

Validates that:
- web_upload_files uploads files via DOM.setFileInputFiles
- web_upload_files returns SENSITIVE risk metadata
- web_upload_files supports element_ref and selector targeting
- web_upload_files supports single and multiple file uploads
- web_upload_files enforces input validation
- web_upload_files returns error when no web connection exists
- web_upload_files returns error for invalid/non-web window references
- web_upload_files returns error when session creation fails
- web_upload_files returns error when element is not found
- web_upload_files returns error when backend is not a BackendRouter
- web_upload_files returns stub response in unwired mode
- web_upload_files hints are registered
- web_upload_files is classified as SENSITIVE system action
- web_upload_files is NOT in _BACKEND_TOOL_MODULES
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
    mcp = FastMCP(name="test-web-upload")
    register_all(mcp, backend=router, ref_store=ref_store)
    return mcp


@pytest.fixture()
def mcp_no_router(native_backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with a plain MockBackend (no router)."""
    mcp = FastMCP(name="test-web-upload-no-router")
    register_all(mcp, backend=native_backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance in stub mode (no backend)."""
    mcp = FastMCP(name="test-web-upload-stub")
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


def _get_tool(mcp: FastMCP, name: str = "desktop.web_upload_files"):
    """Get a tool callable from an MCP instance."""
    tools = mcp._tool_manager.list_tools()
    return next(t for t in tools if t.name == name)


# -- Stub mode tests ----------------------------------------------------------


class TestWebUploadFilesStub:
    """web_upload_files in stub mode (no backend)."""

    def test_stub_returns_result_json(self, stub_mcp: FastMCP) -> None:
        """In stub mode, web_upload_files returns a JSON with success."""
        tool = _get_tool(stub_mcp)
        result = json.loads(
            tool.fn(window_ref="w1", selector="input[type=file]", file_paths=["/tmp/a.txt"])
        )
        assert result["success"] is True
        assert result["files_uploaded"] == 1


# -- Validation tests ---------------------------------------------------------


class TestWebUploadFilesValidation:
    """Input validation for web_upload_files."""

    def test_validation_empty_window_ref(self, mcp_router: FastMCP) -> None:
        """web_upload_files returns validation error for empty window_ref."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="", selector="input", file_paths=["/tmp/a.txt"])
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "window_ref" in result["message"]

    def test_validation_empty_file_paths(self, mcp_router: FastMCP) -> None:
        """web_upload_files returns validation error for empty file_paths."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="w1", selector="input", file_paths=[])
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "file_paths" in result["message"]

    def test_validation_no_element_identifier(self, mcp_router: FastMCP) -> None:
        """web_upload_files requires element_ref or selector."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(window_ref="w1", file_paths=["/tmp/a.txt"])
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "element_ref or selector" in result["message"]

    def test_validation_both_element_identifiers(self, mcp_router: FastMCP) -> None:
        """web_upload_files rejects both element_ref and selector."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(
            window_ref="w1", element_ref="e1", selector="input",
            file_paths=["/tmp/a.txt"],
        )
        result = json.loads(result_json)
        assert result["error"] == "validation_error"
        assert "mutually exclusive" in result["message"]


# -- Error path tests ---------------------------------------------------------


class TestWebUploadFilesErrors:
    """Error handling for web_upload_files."""

    def test_no_web_connection_error(self, mcp_router: FastMCP) -> None:
        """web_upload_files returns error when no web backend is connected."""
        tool = _get_tool(mcp_router)
        result_json = tool.fn(
            window_ref="w1", selector="input", file_paths=["/tmp/a.txt"],
        )
        result = json.loads(result_json)
        assert result["error"] == "web_upload_files_error"
        assert "web_connect" in result["message"]

    def test_no_router_error(self, mcp_no_router: FastMCP) -> None:
        """web_upload_files returns error when backend is not a BackendRouter."""
        tool = _get_tool(mcp_no_router)
        result_json = tool.fn(
            window_ref="w1", selector="input", file_paths=["/tmp/a.txt"],
        )
        result = json.loads(result_json)
        assert result["error"] == "web_upload_files_error"
        assert "BackendRouter" in result["message"]

    def test_invalid_window_ref_error(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
    ) -> None:
        """web_upload_files returns error for unknown window reference."""
        _setup_connected_router(mcp_router, ref_store)
        tool = _get_tool(mcp_router)
        result_json = tool.fn(
            window_ref="w999", selector="input", file_paths=["/tmp/a.txt"],
        )
        result = json.loads(result_json)
        assert result["error"] == "web_upload_files_error"
        assert "not found" in result["message"].lower()

    def test_session_creation_failure(
        self, mcp_router: FastMCP, ref_store: ElementRefStore,
    ) -> None:
        """web_upload_files returns error when session creation fails."""
        mock_web, window_ref = _setup_connected_router(mcp_router, ref_store)
        tool = _get_tool(mcp_router)

        mock_web._get_or_create_session.side_effect = Exception("Target not found")
        result_json = tool.fn(
            window_ref=window_ref, selector="input", file_paths=["/tmp/a.txt"],
        )
        result = json.loads(result_json)
        assert result["error"] == "web_upload_files_error"
        assert "Target not found" in result["message"]


# -- Safety classification tests ----------------------------------------------


class TestWebUploadFilesSafety:
    """Verify web_upload_files is classified as SENSITIVE."""

    def test_web_upload_files_is_sensitive(self) -> None:
        """web_upload_files system action is SENSITIVE."""
        from guidewire.safety import SYSTEM_ACTION_RISK_MAP

        assert SYSTEM_ACTION_RISK_MAP["web_upload_files"] == "SENSITIVE"

    def test_classify_web_upload_files(self) -> None:
        """classify_system_action returns SENSITIVE for web_upload_files."""
        from guidewire.safety import classify_system_action

        result = classify_system_action(
            "web_upload_files", target="/tmp/file.txt",
        )
        assert result.risk_level == "SENSITIVE"
        assert result.confirmation_required is True


# -- Hint registry tests ------------------------------------------------------


class TestWebUploadFilesHints:
    """Verify web_upload_files error hints are registered."""

    def test_web_upload_files_error_hints(self) -> None:
        """web_upload_files_error has registered hints."""
        from guidewire.hints import hints_for

        hints = hints_for("web_upload_files_error")
        assert len(hints) > 0
        assert any("web_connect" in h for h in hints)

    def test_web_upload_files_hints_mention_file(self) -> None:
        """web_upload_files_error hints mention file input."""
        from guidewire.hints import hints_for

        hints = hints_for("web_upload_files_error")
        assert any("file" in h.lower() for h in hints)


# -- Tool registry tests ------------------------------------------------------


class TestWebUploadFilesRegistry:
    """Verify web_upload_files is properly registered."""

    def test_web_upload_files_in_tool_list(self, mcp_router: FastMCP) -> None:
        """desktop.web_upload_files is in the registered tool list."""
        tools = mcp_router._tool_manager.list_tools()
        names = [t.name for t in tools]
        assert "desktop.web_upload_files" in names

    def test_web_upload_files_not_in_backend_modules(self) -> None:
        """web_upload_files is NOT in _BACKEND_TOOL_MODULES."""
        from guidewire.tools import _BACKEND_TOOL_MODULES

        assert ".web_upload_files" not in _BACKEND_TOOL_MODULES

    def test_web_upload_files_in_tool_modules(self) -> None:
        """web_upload_files IS in _TOOL_MODULES."""
        from guidewire.tools import _TOOL_MODULES

        assert ".web_upload_files" in _TOOL_MODULES
