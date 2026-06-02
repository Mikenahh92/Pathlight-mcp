"""Tests for the desktop.mouse_move tool handler (GW-151).

Validates that the wired mouse_move tool:
- Validates coordinates (non-negative integers).
- Accepts optional duration parameter.
- Resolves the target window from backend.list_windows().
- Delegates to backend.perform_action(window, DesktopAction.MOUSE_MOVE, x=..., y=...).
- Returns a structured JSON success response with safety metadata (INTERACTION risk).
- Includes confirmation_required in success response.
- Returns structured JSON error for backend unavailable / action not supported.
- Falls back to static stub response when no backend is provided.
"""

import json
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import DesktopAction
from pathlight_mcp.errors import ActionNotSupportedError
from pathlight_mcp.tools import register_all

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window."""
    b = MockBackend().add_window(title="Test Window", app="TestApp", focused=True)
    return b


@pytest.fixture()
def mcp(backend: MockBackend) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-mouse-move")
    register_all(mcp, backend=backend)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-mouse-move-stub")
    register_all(mcp)
    return mcp


# -- Stub mode tests ----------------------------------------------------------


class TestMouseMoveStub:
    """mouse_move returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, mouse_move should return a static string."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 100, "y": 200}
        )
        assert "100" in result[0].text
        assert "200" in result[0].text

    async def test_stub_contains_coordinates(self, stub_mcp: FastMCP) -> None:
        """Stub response should contain the x and y values."""
        result, _meta = await stub_mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 50, "y": 75}
        )
        assert "50" in result[0].text
        assert "75" in result[0].text


# -- Wired mode: success ------------------------------------------------------


class TestMouseMoveSuccess:
    """mouse_move wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP) -> None:
        """Should return JSON with success=True, x, y, risk, target_summary."""
        result, _meta = await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 100, "y": 200}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["x"] == 100
        assert data["y"] == 200
        assert "risk" in data
        assert "target_summary" in data

    async def test_interaction_risk_level(self, mcp: FastMCP) -> None:
        """mouse_move should be classified as INTERACTION."""
        result, _meta = await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 100, "y": 200}
        )
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"

    async def test_delegates_to_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Should record a MOUSE_MOVE action in the backend action log."""
        await mcp.call_tool("desktop.mouse_move", arguments={"x": 150, "y": 250})
        assert len(backend.action_log) >= 1
        entry = backend.action_log[-1]
        assert entry["action"] == DesktopAction.MOUSE_MOVE
        assert entry["kwargs"]["x"] == 150
        assert entry["kwargs"]["y"] == 250

    async def test_zero_coordinates(self, mcp: FastMCP) -> None:
        """Moving to (0, 0) should succeed."""
        result, _meta = await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 0, "y": 0}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True

    async def test_large_coordinates(self, mcp: FastMCP) -> None:
        """Moving to large coordinates should succeed."""
        result, _meta = await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 3840, "y": 2160}
        )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["x"] == 3840
        assert data["y"] == 2160

    async def test_confirmation_required_in_response(self, mcp: FastMCP) -> None:
        """Success response should include confirmation_required=False."""
        result, _meta = await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 100, "y": 200}
        )
        data = json.loads(result[0].text)
        assert data["confirmation_required"] is False

    async def test_duration_forwarded_to_backend(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """When duration is provided, it should be forwarded to the backend."""
        await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 100, "y": 200, "duration": 0.5}
        )
        entry = backend.action_log[-1]
        assert entry["kwargs"]["duration"] == 0.5

    async def test_no_duration_omitted_from_kwargs(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """When duration is not provided, it should not be in kwargs."""
        await mcp.call_tool("desktop.mouse_move", arguments={"x": 100, "y": 200})
        entry = backend.action_log[-1]
        assert "duration" not in entry["kwargs"]


# -- Wired mode: error (structured JSON) --------------------------------------


class TestMouseMoveErrors:
    """mouse_move wired to backend — error path returns structured JSON."""

    async def test_negative_x_returns_validation_error(self, mcp: FastMCP) -> None:
        """Negative x should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": -1, "y": 100}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-negative" in data["message"]

    async def test_negative_y_returns_validation_error(self, mcp: FastMCP) -> None:
        """Negative y should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 100, "y": -5}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-negative" in data["message"]

    async def test_both_negative_returns_validation_error(self, mcp: FastMCP) -> None:
        """Both negative should return validation error JSON."""
        result, _meta = await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": -10, "y": -20}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"

    async def test_no_windows_returns_backend_unavailable(
        self, backend: MockBackend
    ) -> None:
        """Should return backend_unavailable when no windows exist."""
        backend.dispose()
        mcp = FastMCP(name="test-no-windows")
        register_all(mcp, backend=backend)
        result, _meta = await mcp.call_tool(
            "desktop.mouse_move", arguments={"x": 100, "y": 200}
        )
        data = json.loads(result[0].text)
        assert data["error"] == "backend_unavailable"

    async def test_action_not_supported_returns_structured_error(
        self,
        mcp: FastMCP,
        backend: MockBackend,
    ) -> None:
        """Should return action_not_supported when backend raises it."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=ActionNotSupportedError("mouse_move not supported"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.mouse_move", arguments={"x": 100, "y": 200}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "action_not_supported"


# -- Schema validation --------------------------------------------------------


class TestMouseMoveSchema:
    """mouse_move tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should be desktop.mouse_move."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.mouse_move" in names

    async def test_x_y_required(self, mcp: FastMCP) -> None:
        """x and y should be required parameters."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.mouse_move")
        schema = tool.inputSchema
        assert "x" in schema["properties"]
        assert "y" in schema["properties"]
        assert "x" in schema["required"]
        assert "y" in schema["required"]

    async def test_duration_optional(self, mcp: FastMCP) -> None:
        """duration should be an optional parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.mouse_move")
        schema = tool.inputSchema
        assert "duration" in schema["properties"]
        required = schema.get("required", [])
        assert "duration" not in required

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.mouse_move")
        assert tool.description is not None
        assert len(tool.description) > 0
