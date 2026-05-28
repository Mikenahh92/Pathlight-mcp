"""Tests for the desktop.focus_window tool handler (GW-011).

Validates that the wired focus_window tool:
- Resolves a w-prefixed reference via ElementRefStore.
- Calls backend.focus_window() on success.
- Returns a structured JSON success response with safety metadata.
- Returns structured JSON error for unknown refs (not raises).
- Returns structured JSON error for stale refs.
- Returns structured JSON error for backend-rejected handles.
- Validates input (empty string, non-w-prefixed refs).
- Falls back to static stub response when no backend is provided.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import NativeHandle
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with two windows pre-registered."""
    return (
        MockBackend()
        .add_window(title="Window A", app="App", focused=True)
        .add_window(title="Window B", app="App", focused=False)
    )


@pytest.fixture()
def ref_store(backend: MockBackend) -> ElementRefStore:
    """Return an ElementRefStore with w-prefixed refs for both windows."""
    store = ElementRefStore()
    windows = backend.list_windows()
    for handle in windows:
        store.store(handle, prefix="w")
    return store


@pytest.fixture()
def mcp(backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-focus-window")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-focus-window-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestFocusWindowStub:
    """focus_window returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, focus_window should return a static string."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.focus_window", arguments={"window_ref": "w1"}
        )
        assert result[0].text == "Focused window w1"

    async def test_stub_returns_window_ref_in_text(self, stub_mcp: FastMCP) -> None:
        """Stub response should contain the window_ref value."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.focus_window", arguments={"window_ref": "w99"}
        )
        assert "w99" in result[0].text
        assert "Focused" in result[0].text


# -- Wired mode: success ------------------------------------------------------


class TestFocusWindowSuccess:
    """focus_window wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP) -> None:
        """Should return JSON with success=True, ref, title, risk, target_summary."""
        result, _meta = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "w1"
        assert data["title"] == "Window A"
        assert "risk" in data
        assert "target_summary" in data

    async def test_success_response_has_risk_read_only(self, mcp: FastMCP) -> None:
        """Focus action should always return read_only risk."""
        result, _meta = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["risk"] == "read_only"

    async def test_focuses_second_window(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Focusing w2 should update backend state."""
        await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w2"})
        windows = backend.list_windows()
        info_w2 = backend.get_window_info(windows[1])
        info_w1 = backend.get_window_info(windows[0])
        assert info_w2["focused"] is True
        assert info_w1["focused"] is False

    async def test_focus_same_window_idempotent(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Focusing an already-focused window should succeed."""
        await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w1"})
        result, _meta = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "w1"


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestFocusWindowErrors:
    """focus_window wired to backend — error path returns structured JSON."""

    async def test_unknown_ref_returns_error_json(self, mcp: FastMCP) -> None:
        """Should return structured JSON error for unknown refs, not raise."""
        result, _meta = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w99"})
        data = json.loads(result[0].text)
        assert data["error"] == "window_not_found"
        assert "w99" in data["message"]
        assert data["ref"] == "w99"

    async def test_backend_rejects_unknown_handle(
        self,
        mcp: FastMCP,
        ref_store: ElementRefStore,
    ) -> None:
        """Should return stale error for handle not known to backend."""
        # Store a handle that the backend does not know about
        ghost = NativeHandle("ghost-window-handle")
        ref_store.store(ghost, prefix="w")
        # w3 resolves to a valid ref_store entry but backend.is_valid returns
        # False because the handle is not in _windows or _elements.
        result, _meta = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w3"})
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w3"

    async def test_disposed_window_returns_stale_error(
        self,
        mcp: FastMCP,
        backend: MockBackend,
    ) -> None:
        """Should return stale error after backend window is removed."""
        backend.list_windows()
        # Dispose removes all windows, making the handle invalid
        backend.dispose()
        result, _meta = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"


# -- Input validation ---------------------------------------------------------


class TestFocusWindowValidation:
    """focus_window input validation (AC-6)."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": ""})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_whitespace_only_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "   "})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_non_w_prefix_returns_validation_error(self, mcp: FastMCP) -> None:
        """Non-w-prefixed ref should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "w" in data["message"]
        assert data["ref"] == "e1"


# -- Schema validation --------------------------------------------------------


class TestFocusWindowSchema:
    """focus_window tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should still be desktop.focus_window."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.focus_window" in names

    async def test_window_ref_required(self, mcp: FastMCP) -> None:
        """window_ref should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.focus_window")
        schema = tool.inputSchema
        assert "window_ref" in schema["properties"]
        assert "window_ref" in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.focus_window")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "foreground" in tool.description.lower()
