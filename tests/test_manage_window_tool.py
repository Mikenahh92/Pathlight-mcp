"""Tests for the desktop.manage_window tool handler (GW-055).

Validates that the wired manage_window tool:
- Resolves a w-prefixed reference via ElementRefStore.
- Dispatches to the correct backend method for each action.
- Returns structured JSON success response with safety metadata.
- Returns structured JSON error for unknown refs, stale refs, and validation errors.
- Falls back to static stub response when no backend is provided.
- Validates input (empty string, non-w-prefixed refs, invalid actions, missing params).
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.backends.types import NativeHandle
from guidewire.refs import ElementRefStore
from guidewire.tools import register_all

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
    mcp = FastMCP(name="test-manage-window")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-manage-window-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestManageWindowStub:
    """manage_window returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, manage_window should return a static string."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.manage_window", arguments={"window_ref": "w1", "action": "minimize"}
        )
        assert result[0].text == "Managed window w1: minimize"


# -- Wired mode: minimize ------------------------------------------------------


class TestMinimizeWindow:
    """manage_window wired to backend — minimize action."""

    async def test_minimize_success(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Minimize should return JSON with success=True."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window", arguments={"window_ref": "w1", "action": "minimize"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["ref"] == "w1"
        assert data["action"] == "minimize"
        assert "risk" in data

    async def test_minimize_updates_backend_state(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Minimize should update backend window state."""
        windows = backend.list_windows()
        await mcp.call_tool(
            "desktop.manage_window", arguments={"window_ref": "w1", "action": "minimize"}
        )
        info = backend.get_window_info(windows[0])
        assert info["bounds"]["y"] >= 0  # window still exists

    async def test_minimize_logged(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Minimize should be logged in action_log."""
        await mcp.call_tool(
            "desktop.manage_window", arguments={"window_ref": "w1", "action": "minimize"}
        )
        assert any(a["action"] == "minimize_window" for a in backend.action_log)


# -- Wired mode: maximize ------------------------------------------------------


class TestMaximizeWindow:
    """manage_window wired to backend — maximize action."""

    async def test_maximize_success(self, mcp: FastMCP) -> None:
        """Maximize should return JSON with success=True."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window", arguments={"window_ref": "w1", "action": "maximize"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["action"] == "maximize"


# -- Wired mode: restore -------------------------------------------------------


class TestRestoreWindow:
    """manage_window wired to backend — restore action."""

    async def test_restore_success(self, mcp: FastMCP) -> None:
        """Restore should return JSON with success=True."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window", arguments={"window_ref": "w1", "action": "restore"}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["action"] == "restore"


# -- Wired mode: move ----------------------------------------------------------


class TestMoveWindow:
    """manage_window wired to backend — move action."""

    async def test_move_success(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Move should return JSON with success=True and update bounds."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w1", "action": "move", "x": 100, "y": 200},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["action"] == "move"
        assert "(100, 200)" in data["target_summary"]

    async def test_move_updates_backend_bounds(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Move should update backend window bounds."""
        windows = backend.list_windows()
        await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w1", "action": "move", "x": 50, "y": 75},
        )
        info = backend.get_window_info(windows[0])
        assert info["bounds"]["x"] == 50
        assert info["bounds"]["y"] == 75
        # Width/height should be preserved
        assert info["bounds"]["width"] == 800
        assert info["bounds"]["height"] == 600


# -- Wired mode: resize --------------------------------------------------------


class TestResizeWindow:
    """manage_window wired to backend — resize action."""

    async def test_resize_success(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Resize should return JSON with success=True and update bounds."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w1", "action": "resize", "width": 1024, "height": 768},
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["action"] == "resize"
        assert "1024x768" in data["target_summary"]

    async def test_resize_updates_backend_bounds(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Resize should update backend window bounds."""
        windows = backend.list_windows()
        await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w1", "action": "resize", "width": 640, "height": 480},
        )
        info = backend.get_window_info(windows[0])
        assert info["bounds"]["width"] == 640
        assert info["bounds"]["height"] == 480
        # Position should be preserved
        assert info["bounds"]["x"] == 0
        assert info["bounds"]["y"] == 0


# -- Error handling ------------------------------------------------------------


class TestManageWindowErrors:
    """manage_window wired to backend — error path returns structured JSON."""

    async def test_unknown_ref_returns_error_json(self, mcp: FastMCP) -> None:
        """Should return structured JSON error for unknown refs."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w99", "action": "minimize"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "window_not_found"
        assert "w99" in data["message"]

    async def test_stale_ref_returns_error(
        self,
        mcp: FastMCP,
        ref_store: ElementRefStore,
    ) -> None:
        """Should return stale error for handle not known to backend."""
        ghost = NativeHandle("ghost-window-handle")
        ref_store.store(ghost, prefix="w")
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w3", "action": "maximize"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"

    async def test_disposed_window_returns_stale_error(
        self,
        mcp: FastMCP,
        backend: MockBackend,
    ) -> None:
        """Should return stale error after backend window is removed."""
        backend.dispose()
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w1", "action": "restore"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"


# -- Input validation ---------------------------------------------------------


class TestManageWindowValidation:
    """manage_window input validation."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty window_ref should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "", "action": "minimize"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_non_w_prefix_returns_validation_error(self, mcp: FastMCP) -> None:
        """Non-w-prefixed ref should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "e1", "action": "minimize"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "w" in data["message"]

    async def test_invalid_action_returns_validation_error(self, mcp: FastMCP) -> None:
        """Invalid action should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w1", "action": "destroy"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "action" in data["message"].lower()

    async def test_move_without_coords_returns_validation_error(self, mcp: FastMCP) -> None:
        """Move without x,y should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w1", "action": "move"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "x" in data["message"].lower()

    async def test_resize_without_dimensions_returns_validation_error(self, mcp: FastMCP) -> None:
        """Resize without width,height should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w1", "action": "resize"},
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "width" in data["message"].lower()


# -- Safety metadata -----------------------------------------------------------


class TestManageWindowSafety:
    """manage_window safety classification."""

    async def test_risk_level_is_interaction(self, mcp: FastMCP) -> None:
        """window_manage should be classified as INTERACTION."""
        result, _meta = await mcp.call_tool(
            "desktop.manage_window",
            arguments={"window_ref": "w1", "action": "minimize"},
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"


# -- Schema validation --------------------------------------------------------


class TestManageWindowSchema:
    """manage_window tool schema is correct."""

    async def test_tool_name_registered(self, mcp: FastMCP) -> None:
        """Tool name should be desktop.manage_window."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.manage_window" in names

    async def test_required_params(self, mcp: FastMCP) -> None:
        """window_ref and action should be required parameters."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.manage_window")
        schema = tool.inputSchema
        assert "window_ref" in schema["required"]
        assert "action" in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.manage_window")
        assert tool.description is not None
        assert len(tool.description) > 0


# -- Mock backend window state tests ------------------------------------------


class TestMockBackendWindowState:
    """Verify MockBackend window state management methods."""

    def test_minimize_sets_state(self) -> None:
        b = MockBackend().add_window(title="W1")
        w = b.last_window_handle
        b.minimize_window(w)
        assert b._windows[w].minimized is True
        assert b._windows[w].maximized is False
        assert b._windows[w].focused is False

    def test_maximize_sets_state(self) -> None:
        b = MockBackend().add_window(title="W1")
        w = b.last_window_handle
        b.maximize_window(w)
        assert b._windows[w].maximized is True
        assert b._windows[w].minimized is False

    def test_restore_clears_state(self) -> None:
        b = MockBackend().add_window(title="W1")
        w = b.last_window_handle
        b.maximize_window(w)
        b.restore_window(w)
        assert b._windows[w].maximized is False
        assert b._windows[w].minimized is False

    def test_move_updates_bounds(self) -> None:
        b = MockBackend().add_window(title="W1")
        w = b.last_window_handle
        b.move_window(w, 10, 20)
        assert b._windows[w].bounds.x == 10
        assert b._windows[w].bounds.y == 20
        assert b._windows[w].bounds.width == 800
        assert b._windows[w].bounds.height == 600

    def test_resize_updates_bounds(self) -> None:
        b = MockBackend().add_window(title="W1")
        w = b.last_window_handle
        b.resize_window(w, 1024, 768)
        assert b._windows[w].bounds.width == 1024
        assert b._windows[w].bounds.height == 768
        assert b._windows[w].bounds.x == 0
        assert b._windows[w].bounds.y == 0

    def test_invalid_window_raises(self) -> None:
        from guidewire.errors import WindowNotFoundError

        b = MockBackend()
        with pytest.raises(WindowNotFoundError):
            b.minimize_window(NativeHandle("nonexistent"))
