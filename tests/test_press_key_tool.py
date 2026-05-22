"""Tests for the desktop.press_key tool handler (GW-016).

Validates that the wired press_key tool:
- Parses key combo strings and normalises them (Ctrl+S, Alt+Tab, etc.).
- Delegates to backend.perform_action(handle, DesktopAction.PRESS_KEY, keys=...).
- Returns a structured JSON success response with safety metadata.
- Returns structured JSON error for empty input.
- Returns structured JSON error for backend-rejected actions.
- Falls back to static stub response when no backend is provided.
"""

import json
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from guidewire.backends import MockBackend
from guidewire.errors import ActionNotSupportedError, BackendUnavailableError
from guidewire.tools import register_all
from guidewire.tools.press_key import _normalise_key_combo

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window for press_key context."""
    return MockBackend().add_window(title="Test Window", app="TestApp", focused=True)


@pytest.fixture()
def mcp(backend: MockBackend) -> FastMCP:
    """Return a FastMCP instance with tools registered using a wired backend."""
    mcp = FastMCP(name="test-press-key")
    register_all(mcp, backend=backend)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance with tools registered in stub mode (no backend)."""
    mcp = FastMCP(name="test-press-key-stub")
    register_all(mcp)
    return mcp


# -- Key combo normalisation tests --------------------------------------------


class TestNormaliseKeyCombo:
    """_normalise_key_combo correctly normalises key combo strings."""

    def test_simple_key(self) -> None:
        assert _normalise_key_combo("Enter") == "enter"

    def test_ctrl_combo(self) -> None:
        assert _normalise_key_combo("Ctrl+S") == "ctrl+s"

    def test_alt_combo(self) -> None:
        assert _normalise_key_combo("Alt+Tab") == "alt+tab"

    def test_shift_combo(self) -> None:
        assert _normalise_key_combo("Shift+A") == "shift+a"

    def test_command_to_super(self) -> None:
        assert _normalise_key_combo("Command+Q") == "super+q"

    def test_cmd_to_super(self) -> None:
        assert _normalise_key_combo("Cmd+Q") == "super+q"

    def test_control_alias(self) -> None:
        assert _normalise_key_combo("Control+C") == "ctrl+c"

    def test_win_to_super(self) -> None:
        assert _normalise_key_combo("Win+R") == "super+r"

    def test_meta_to_super(self) -> None:
        assert _normalise_key_combo("Meta+R") == "super+r"

    def test_triple_combo(self) -> None:
        assert _normalise_key_combo("Ctrl+Shift+Escape") == "ctrl+shift+escape"

    def test_return_to_enter(self) -> None:
        assert _normalise_key_combo("Return") == "enter"

    def test_esc_to_escape(self) -> None:
        assert _normalise_key_combo("Esc") == "escape"

    def test_del_to_delete(self) -> None:
        assert _normalise_key_combo("Del") == "delete"

    def test_pageup_alias(self) -> None:
        assert _normalise_key_combo("PageUp") == "page_up"

    def test_pagedown_alias(self) -> None:
        assert _normalise_key_combo("PageDown") == "page_down"

    def test_arrow_up_alias(self) -> None:
        assert _normalise_key_combo("ArrowUp") == "up"

    def test_arrow_down_alias(self) -> None:
        assert _normalise_key_combo("ArrowDown") == "down"

    def test_function_key(self) -> None:
        assert _normalise_key_combo("F5") == "f5"

    def test_single_lowercase_char(self) -> None:
        assert _normalise_key_combo("a") == "a"

    def test_single_uppercase_char(self) -> None:
        assert _normalise_key_combo("A") == "a"

    def test_whitespace_trimmed(self) -> None:
        assert _normalise_key_combo("  Ctrl + S  ") == "ctrl+s"

    def test_single_char_passthrough(self) -> None:
        """Unknown single characters pass through as-is."""
        assert _normalise_key_combo("x") == "x"


# -- Stub mode tests ----------------------------------------------------------


class TestPressKeyStub:
    """press_key returns static stub response when no backend is provided."""

    async def test_stub_returns_static_message(self, stub_mcp: FastMCP) -> None:
        """Without a backend, press_key should return a static string."""
        result, _meta = await stub_mcp.call_tool("desktop.press_key", arguments={"keys": "Enter"})
        assert result[0].text == 'Pressed "Enter"'

    async def test_stub_contains_key_in_text(self, stub_mcp: FastMCP) -> None:
        """Stub response should contain the keys value."""
        result, _meta = await stub_mcp.call_tool("desktop.press_key", arguments={"keys": "Ctrl+S"})
        assert "Ctrl+S" in result[0].text
        assert "Pressed" in result[0].text


# -- Wired mode: success ------------------------------------------------------


class TestPressKeySuccess:
    """press_key wired to backend — success path."""

    async def test_returns_json_success(self, mcp: FastMCP) -> None:
        """Should return JSON with success=True, keys, risk, target_summary."""
        result, _meta = await mcp.call_tool("desktop.press_key", arguments={"keys": "Enter"})
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["keys"] == "enter"
        assert "risk" in data
        assert "target_summary" in data

    async def test_success_response_has_interaction_risk(self, mcp: FastMCP) -> None:
        """Press key on a generic element should return interaction risk."""
        result, _meta = await mcp.call_tool("desktop.press_key", arguments={"keys": "Tab"})
        data = json.loads(result[0].text)
        assert data["risk"] == "interaction"

    async def test_press_key_delegates_to_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Pressing Enter should record a PRESS_KEY action in the backend action log."""
        await mcp.call_tool("desktop.press_key", arguments={"keys": "Enter"})
        assert len(backend.action_log) >= 1
        entry = backend.action_log[-1]
        assert entry["action"] == "press_key"
        assert entry["kwargs"]["keys"] == "enter"

    async def test_combo_key_delegates_normalised(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Pressing Ctrl+S should normalise and pass to backend."""
        await mcp.call_tool("desktop.press_key", arguments={"keys": "Ctrl+S"})
        entry = backend.action_log[-1]
        assert entry["kwargs"]["keys"] == "ctrl+s"

    async def test_target_summary_contains_key(self, mcp: FastMCP) -> None:
        """Target summary should describe the key press."""
        result, _meta = await mcp.call_tool("desktop.press_key", arguments={"keys": "Alt+Tab"})
        data = json.loads(result[0].text)
        assert "alt+tab" in data["target_summary"]

    async def test_press_key_multiple_times(self, mcp: FastMCP, backend: MockBackend) -> None:
        """Pressing keys multiple times should succeed each time."""
        await mcp.call_tool("desktop.press_key", arguments={"keys": "Enter"})
        await mcp.call_tool("desktop.press_key", arguments={"keys": "Tab"})
        assert len(backend.action_log) == 2


# -- Wired mode: error (structured JSON, not exceptions) ----------------------


class TestPressKeyErrors:
    """press_key wired to backend — error path returns structured JSON."""

    async def test_empty_string_returns_validation_error(self, mcp: FastMCP) -> None:
        """Empty string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.press_key", arguments={"keys": ""})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]
        assert data["keys"] == ""

    async def test_whitespace_only_returns_validation_error(self, mcp: FastMCP) -> None:
        """Whitespace-only string should return validation error JSON."""
        result, _meta = await mcp.call_tool("desktop.press_key", arguments={"keys": "   "})
        data = json.loads(result[0].text)
        assert data["error"] == "validation_error"
        assert "non-empty" in data["message"]

    async def test_backend_unavailable_returns_error(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Should return backend_unavailable error when backend raises."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=BackendUnavailableError("Backend not connected"),
        ):
            result, _meta = await mcp.call_tool("desktop.press_key", arguments={"keys": "Enter"})
        data = json.loads(result[0].text)
        assert data["error"] == "backend_unavailable"
        assert "Enter" in data["keys"]

    async def test_action_not_supported_returns_error(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """Should return action_not_supported error when backend raises."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=ActionNotSupportedError("Press key not supported"),
        ):
            result, _meta = await mcp.call_tool("desktop.press_key", arguments={"keys": "Ctrl+S"})
        data = json.loads(result[0].text)
        assert data["error"] == "action_not_supported"
        assert "Ctrl+S" in data["message"]


# -- Schema validation --------------------------------------------------------


class TestPressKeySchema:
    """press_key tool schema remains correct after wiring."""

    async def test_tool_name_unchanged(self, mcp: FastMCP) -> None:
        """Tool name should still be desktop.press_key."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "desktop.press_key" in names

    async def test_keys_required(self, mcp: FastMCP) -> None:
        """keys should be a required parameter."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.press_key")
        schema = tool.inputSchema
        assert "keys" in schema["properties"]
        assert "keys" in schema["required"]

    async def test_description_present(self, mcp: FastMCP) -> None:
        """Tool should have a non-empty description."""
        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "desktop.press_key")
        assert tool.description is not None
        assert len(tool.description) > 0
        assert "key" in tool.description.lower()
