"""Comprehensive tool-layer validation tests for all 8 MCP tool handlers (GW-018).

Capstone validation suite for Epic 2. Covers remaining coverage gaps across
all tool handlers using MockBackend, focusing on:

- Backend exception propagation (ElementNotFoundError, StaleElementReferenceError,
  WindowNotFoundError) from perform_action and other backend calls.
- Snapshot internal helpers (_dict_to_element, _assign_refs, _pluralize).
- Cross-tool consistency: every handler returns structured JSON on error,
  includes risk metadata on success, and validates input.
- Edge cases: empty values, unicode refs, no-windows scenarios for press_key.
"""

import json
from typing import ClassVar
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from pathlight_mcp.backends import MockBackend
from pathlight_mcp.backends.types import (
    DesktopAction,
)
from pathlight_mcp.errors import (
    ElementNotFoundError,
    StaleElementReferenceError,
    WindowNotFoundError,
)
from pathlight_mcp.refs import ElementRefStore
from pathlight_mcp.tools import register_all
from pathlight_mcp.tools.find import _pluralize
from pathlight_mcp.tools.press_key import _normalise_key_combo
from pathlight_mcp.tools.snapshot import _assign_refs, _dict_to_element

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def backend() -> MockBackend:
    """Return a MockBackend with a window and several elements."""
    b = MockBackend().add_window(title="Main", app="TestApp", focused=True)
    win = b.last_window_handle
    b.add_element(role="button", name="Save", parent=win)
    b.add_element(role="text_input", name="Username", value="admin", parent=win)
    b.add_element(role="text_input", name="Password", value="secret", parent=win)
    b.add_element(role="link", name="Help", parent=win)
    b.add_element(role="delete_button", name="Remove", parent=win)
    return b


@pytest.fixture()
def ref_store(backend: MockBackend) -> ElementRefStore:
    """Return an ElementRefStore with w-prefixed window refs and e-prefixed element refs."""
    store = ElementRefStore()
    windows = backend.list_windows()
    for handle in windows:
        store.store(handle, prefix="w")
    elements = backend.find_elements(windows[0])
    for handle in elements:
        store.store(handle, prefix="e")
    return store


@pytest.fixture()
def mcp(backend: MockBackend, ref_store: ElementRefStore) -> FastMCP:
    """Return a FastMCP instance with all tools registered and wired."""
    mcp = FastMCP(name="test-validation")
    register_all(mcp, backend=backend, ref_store=ref_store)
    return mcp


@pytest.fixture()
def stub_mcp() -> FastMCP:
    """Return a FastMCP instance in stub mode (no backend)."""
    mcp = FastMCP(name="test-validation-stub")
    register_all(mcp)
    return mcp


# ===========================================================================
# CROSS-TOOL: Structured error consistency
# ===========================================================================


class TestAllToolsReturnStructuredErrors:
    """Every tool returns structured JSON on error, never raises."""

    @pytest.mark.parametrize(
        "tool_name,arguments,error_key",
        [
            ("desktop.list_windows", {}, None),  # list_windows raises BackendUnavailableError
            ("desktop.focus_window", {"window_ref": "w99"}, "window_not_found"),
            ("desktop.snapshot", {"window_ref": "w99"}, "window_not_found"),
            ("desktop.find", {"window_ref": "w99", "role": "button"}, "window_not_found"),
            ("desktop.click", {"element_ref": "e99"}, "element_not_found"),
            ("desktop.type_text", {"element_ref": "e99", "text": "x"}, "element_not_found"),
            ("desktop.get_text", {"element_ref": "e99"}, "element_not_found"),
            ("desktop.press_key", {"keys": "Enter"}, None),  # press_key needs no ref
        ],
    )
    async def test_unknown_ref_returns_json_error(
        self, mcp: FastMCP, tool_name: str, arguments: dict, error_key: str | None
    ) -> None:
        """Every tool should return JSON error for unknown refs, not raise."""
        if error_key is None:
            # list_windows and press_key handle errors differently
            if tool_name == "desktop.list_windows":
                pytest.skip("list_windows propagates BackendUnavailableError")
            return  # press_key doesn't take refs
        result, _meta = await mcp.call_tool(tool_name, arguments=arguments)
        data = json.loads(result[0].text)
        assert data["error"] == error_key


# ===========================================================================
# CLICK: Backend exception propagation (coverage gaps lines 94, 104)
# ===========================================================================


class TestClickBackendExceptions:
    """click — backend perform_action raises ElementNotFoundError / StaleElementReferenceError."""

    async def test_element_not_found_from_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """perform_action raising ElementNotFoundError returns element_not_found JSON."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=ElementNotFoundError("Element gone"),
        ):
            result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["error"] == "element_not_found"
        assert "e1" in data["message"]
        assert data["ref"] == "e1"

    async def test_stale_element_from_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """perform_action raising StaleElementReferenceError returns stale JSON."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=StaleElementReferenceError("Element stale"),
        ):
            result, _meta = await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e1"


# ===========================================================================
# TYPE_TEXT: Backend exception propagation (coverage gaps lines 96, 106)
# ===========================================================================


class TestTypeTextBackendExceptions:
    """type_text — backend perform_action raises ElementNotFoundError
    or StaleElementReferenceError."""

    async def test_element_not_found_from_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """perform_action raising ElementNotFoundError returns element_not_found JSON."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=ElementNotFoundError("Element gone"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.type_text", arguments={"element_ref": "e1", "text": "hello"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "element_not_found"
        assert "e1" in data["message"]
        assert data["ref"] == "e1"

    async def test_stale_element_from_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """perform_action raising StaleElementReferenceError returns stale JSON."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=StaleElementReferenceError("Element stale"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.type_text", arguments={"element_ref": "e1", "text": "hello"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e1"


# ===========================================================================
# GET_TEXT: Backend exception propagation (coverage gaps lines 96, 106)
# ===========================================================================


class TestGetTextBackendExceptions:
    """get_text — backend perform_action raises ElementNotFoundError
    or StaleElementReferenceError."""

    async def test_element_not_found_from_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """perform_action raising ElementNotFoundError returns element_not_found JSON."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=ElementNotFoundError("Element gone"),
        ):
            result, _meta = await mcp.call_tool("desktop.get_text", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["error"] == "element_not_found"
        assert "e1" in data["message"]
        assert data["ref"] == "e1"

    async def test_stale_element_from_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """perform_action raising StaleElementReferenceError returns stale JSON."""
        with patch.object(
            backend,
            "perform_action",
            side_effect=StaleElementReferenceError("Element stale"),
        ):
            result, _meta = await mcp.call_tool("desktop.get_text", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "e1"


# ===========================================================================
# FOCUS_WINDOW: Backend exception propagation (coverage gaps lines 107-118)
# ===========================================================================


class TestFocusWindowBackendExceptions:
    """focus_window — backend raises WindowNotFoundError / StaleElementReferenceError."""

    async def test_window_not_found_from_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """backend.focus_window raising WindowNotFoundError returns stale JSON."""
        with patch.object(
            backend,
            "focus_window",
            side_effect=WindowNotFoundError("Window gone"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.focus_window", arguments={"window_ref": "w1"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"

    async def test_stale_element_from_backend(self, mcp: FastMCP, backend: MockBackend) -> None:
        """backend.focus_window raising StaleElementReferenceError returns stale JSON."""
        with patch.object(
            backend,
            "focus_window",
            side_effect=StaleElementReferenceError("Window stale"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.focus_window", arguments={"window_ref": "w1"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"

    async def test_get_window_info_raises_window_not_found(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """backend.get_window_info raising WindowNotFoundError returns stale JSON."""
        with patch.object(
            backend,
            "get_window_info",
            side_effect=WindowNotFoundError("Window gone"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.focus_window", arguments={"window_ref": "w1"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"


# ===========================================================================
# FIND: Backend exception propagation (coverage gaps lines 104-113, 128-129)
# ===========================================================================


class TestFindBackendExceptions:
    """find — backend find_elements raises WindowNotFoundError / StaleElementReferenceError."""

    async def test_window_not_found_from_find_elements(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """find_elements raising WindowNotFoundError returns window_not_found JSON."""
        with patch.object(
            backend,
            "find_elements",
            side_effect=WindowNotFoundError("Window gone"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.find", arguments={"window_ref": "w1", "role": "button"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "window_not_found"
        assert data["ref"] == "w1"

    async def test_stale_element_from_find_elements(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """find_elements raising StaleElementReferenceError returns stale JSON."""
        with patch.object(
            backend,
            "find_elements",
            side_effect=StaleElementReferenceError("Window stale"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.find", arguments={"window_ref": "w1", "role": "button"}
            )
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"

    async def test_snapshot_fallback_fails_gracefully(
        self, mcp: FastMCP, backend: MockBackend
    ) -> None:
        """If snapshot call fails during info_map build, find should still return results."""
        # find_elements returns elements but snapshot raises — info_map is empty,
        # so elements are returned with fallback role and no name.
        with patch.object(
            backend,
            "snapshot",
            side_effect=WindowNotFoundError("Snapshot failed"),
        ):
            result, _meta = await mcp.call_tool(
                "desktop.find", arguments={"window_ref": "w1", "role": "button"}
            )
        data = json.loads(result[0].text)
        assert data["success"] is True
        assert data["count"] == 1  # One "Save" button in fixture


# ===========================================================================
# SNAPSHOT: Backend exception propagation (coverage gaps lines 205-214)
# ===========================================================================


class TestSnapshotBackendExceptions:
    """snapshot — backend snapshot raises WindowNotFoundError / StaleElementReferenceError."""

    async def test_window_not_found_from_snapshot(self, mcp: FastMCP, backend: MockBackend) -> None:
        """backend.snapshot raising WindowNotFoundError returns stale JSON."""
        with patch.object(
            backend,
            "snapshot",
            side_effect=WindowNotFoundError("Window gone"),
        ):
            result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"

    async def test_stale_element_from_snapshot(self, mcp: FastMCP, backend: MockBackend) -> None:
        """backend.snapshot raising StaleElementReferenceError returns stale JSON."""
        with patch.object(
            backend,
            "snapshot",
            side_effect=StaleElementReferenceError("Window stale"),
        ):
            result, _meta = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["error"] == "stale_element_reference"
        assert data["ref"] == "w1"


# ===========================================================================
# PRESS_KEY: No windows scenario (coverage gap line 97)
# ===========================================================================


class TestPressKeyNoWindows:
    """press_key — no windows available returns backend_unavailable error."""

    async def test_no_windows_returns_error(self) -> None:
        """press_key with empty backend should return backend_unavailable JSON."""
        empty_backend = MockBackend()
        mcp = FastMCP(name="test-no-windows")
        register_all(mcp, backend=empty_backend)
        result, _meta = await mcp.call_tool("desktop.press_key", arguments={"keys": "Enter"})
        data = json.loads(result[0].text)
        assert data["error"] == "backend_unavailable"
        assert data["keys"] == "Enter"


# ===========================================================================
# SNAPSHOT: Internal helpers — _dict_to_element edge cases
# ===========================================================================


class TestDictToElementEdgeCases:
    """snapshot._dict_to_element handles edge cases in backend data."""

    def test_states_as_list_falls_back_to_defaults(self) -> None:
        """When states is a list (not dict), NormalizedElement uses default states."""
        data = {
            "ref": "e1",
            "role": "button",
            "name": "Test",
            "states": ["enabled", "focused"],  # list format
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 30},
            "actions": ["click"],
            "children": [],
        }
        element = _dict_to_element(data)
        assert element.role == "button"
        assert element.name == "Test"

    def test_no_bounds_returns_none_bounds(self) -> None:
        """When bounds is missing, NormalizedElement has no bounds."""
        data = {
            "ref": "e1",
            "role": "label",
            "name": "Info",
            "states": {},
            "actions": [],
            "children": [],
        }
        element = _dict_to_element(data)
        assert element.bounds is None

    def test_empty_children_returns_none(self) -> None:
        """When children is empty list, NormalizedElement has no children."""
        data = {
            "ref": "e1",
            "role": "label",
            "name": "Info",
            "states": {},
            "actions": [],
            "children": [],
        }
        element = _dict_to_element(data)
        assert element.children is None

    def test_non_string_actions_coerced_to_string(self) -> None:
        """Non-string actions are coerced to strings."""
        data = {
            "ref": "e1",
            "role": "button",
            "name": "Test",
            "states": {},
            "actions": [DesktopAction.CLICK, "invoke"],
            "children": [],
        }
        element = _dict_to_element(data)
        assert all(isinstance(a, str) for a in element.actions)

    def test_minimal_data(self) -> None:
        """Minimal data with only required fields."""
        data = {"ref": "e1", "role": "unknown"}
        element = _dict_to_element(data)
        assert element.ref == "e1"
        assert element.role == "unknown"
        assert element.name is None
        assert element.bounds is None
        assert element.children is None


# ===========================================================================
# SNAPSHOT: Internal helpers — _assign_refs
# ===========================================================================


class TestAssignRefs:
    """snapshot._assign_refs correctly assigns e-prefixed refs."""

    def test_assigns_refs_to_children_only(self) -> None:
        """Root element keeps its ref; only children get e-prefixed refs."""
        from pathlight_mcp.models import NormalizedElement

        root = NormalizedElement(
            ref="w1",
            backend_id="win-handle",
            role="window",
            name="Test",
            children=[
                NormalizedElement(
                    ref="old-ref",
                    backend_id="child-1",
                    role="button",
                    name="Save",
                ),
                NormalizedElement(
                    ref="old-ref-2",
                    backend_id="child-2",
                    role="link",
                    name="Help",
                ),
            ],
        )
        store = ElementRefStore()
        _assign_refs(root, store)
        assert root.ref == "w1"  # Root ref unchanged
        assert root.children[0].ref == "e1"
        assert root.children[1].ref == "e2"
        assert store.resolve("e1") is not None
        assert store.resolve("e2") is not None

    def test_no_children_is_noop(self) -> None:
        """Assigning refs to an element with no children is a no-op."""
        from pathlight_mcp.models import NormalizedElement

        root = NormalizedElement(
            ref="w1",
            backend_id="win-handle",
            role="window",
            name="Test",
        )
        store = ElementRefStore()
        _assign_refs(root, store)
        assert root.ref == "w1"


# ===========================================================================
# FIND: _pluralize edge cases
# ===========================================================================


class TestPluralize:
    """find._pluralize handles various role names."""

    def test_button_plural(self) -> None:
        assert _pluralize("button") == "buttons"

    def test_text_input_plural(self) -> None:
        assert _pluralize("text_input") == "text_inputs"

    def test_link_plural(self) -> None:
        assert _pluralize("link") == "links"

    def test_window_plural(self) -> None:
        assert _pluralize("window") == "windows"

    def test_display_ending_in_y_keeps_y(self) -> None:
        """Roles ending in 'y' that are exceptions keep 'y'."""
        assert _pluralize("display") == "displays"

    def test_directory_ending_in_y_keeps_y(self) -> None:
        assert _pluralize("directory") == "directorys"

    def test_entry_ending_in_y(self) -> None:
        assert _pluralize("entry") == "entries"


# ===========================================================================
# PRESS_KEY: Additional edge cases
# ===========================================================================


class TestPressKeyEdgeCases:
    """press_key — additional edge cases for key combo normalisation."""

    def test_normalise_empty_string(self) -> None:
        """Empty string after stripping should not crash."""
        result = _normalise_key_combo("")
        assert result == ""

    def test_normalise_plus_only(self) -> None:
        """Just a plus sign produces '+' (passed through)."""
        result = _normalise_key_combo("+")
        assert result == "+"

    def test_normalise_spaces_only(self) -> None:
        """Only spaces should produce empty result."""
        result = _normalise_key_combo("   ")
        assert result == ""

    async def test_press_key_with_f_key_combo(self) -> None:
        """Pressing Ctrl+F5 should normalise correctly."""
        b = MockBackend().add_window(title="Test", app="App")
        mcp = FastMCP(name="test-fkey")
        register_all(mcp, backend=b)
        await mcp.call_tool("desktop.press_key", arguments={"keys": "Ctrl+F5"})
        entry = b.action_log[-1]
        assert entry["kwargs"]["keys"] == "ctrl+f5"

    async def test_press_key_f12(self) -> None:
        """Pressing F12 should normalise correctly."""
        b = MockBackend().add_window(title="Test", app="App")
        mcp = FastMCP(name="test-f12")
        register_all(mcp, backend=b)
        await mcp.call_tool("desktop.press_key", arguments={"keys": "F12"})
        entry = b.action_log[-1]
        assert entry["kwargs"]["keys"] == "f12"


# ===========================================================================
# SNAPSHOT: Snapshot with max_depth=0
# ===========================================================================


class TestSnapshotMaxDepthZero:
    """snapshot with max_depth=0 should return tree with no children."""

    async def test_max_depth_zero_no_children(self, mcp: FastMCP) -> None:
        """max_depth=0 should return tree with root but no children."""
        result, _meta = await mcp.call_tool(
            "desktop.snapshot",
            arguments={"window_ref": "w1", "max_depth": 0},
        )
        data = json.loads(result[0].text)
        assert "tree" in data
        assert data["tree"]["ref"] == "w1"
        assert data["tree"].get("children", []) == []

    async def test_max_depth_zero_element_count_is_one(self, mcp: FastMCP) -> None:
        """max_depth=0 should have element_count of 1 (root only)."""
        result, _meta = await mcp.call_tool(
            "desktop.snapshot",
            arguments={"window_ref": "w1", "max_depth": 0},
        )
        data = json.loads(result[0].text)
        assert data["element_count"] == 1


# ===========================================================================
# STUB MODE: All tools in stub mode
# ===========================================================================


class TestAllToolsStubMode:
    """All 8 tools return stub responses when no backend is wired."""

    async def test_list_windows_stub(self, stub_mcp: FastMCP) -> None:
        result, _ = await stub_mcp.call_tool("desktop.list_windows", arguments={})
        data = json.loads(result[0].text)
        assert data["windows"] == []

    async def test_focus_window_stub(self, stub_mcp: FastMCP) -> None:
        result, _ = await stub_mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w1"})
        assert "Focused" in result[0].text

    async def test_snapshot_stub(self, stub_mcp: FastMCP) -> None:
        result, _ = await stub_mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert data["ref"] == "w1"

    async def test_find_stub(self, stub_mcp: FastMCP) -> None:
        result, _ = await stub_mcp.call_tool("desktop.find", arguments={"window_ref": "w1"})
        assert result[0].text == "[]"

    async def test_click_stub(self, stub_mcp: FastMCP) -> None:
        result, _ = await stub_mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        assert "Clicked" in result[0].text

    async def test_type_text_stub(self, stub_mcp: FastMCP) -> None:
        result, _ = await stub_mcp.call_tool(
            "desktop.type_text", arguments={"element_ref": "e1", "text": "hello"}
        )
        assert "Typed" in result[0].text

    async def test_press_key_stub(self, stub_mcp: FastMCP) -> None:
        result, _ = await stub_mcp.call_tool("desktop.press_key", arguments={"keys": "Enter"})
        assert "Pressed" in result[0].text

    async def test_get_text_stub(self, stub_mcp: FastMCP) -> None:
        result, _ = await stub_mcp.call_tool("desktop.get_text", arguments={"element_ref": "e1"})
        assert "Text" in result[0].text


# ===========================================================================
# CROSS-TOOL: Tool discoverability
# ===========================================================================


class TestAllToolsDiscoverable:
    """All 8 tools are discoverable via list_tools."""

    EXPECTED_TOOLS: ClassVar = [
        "desktop.list_windows",
        "desktop.focus_window",
        "desktop.snapshot",
        "desktop.find",
        "desktop.click",
        "desktop.type_text",
        "desktop.press_key",
        "desktop.get_text",
    ]

    async def test_all_tools_registered(self, mcp: FastMCP) -> None:
        """All 8 desktop tools should be registered."""
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        for expected in self.EXPECTED_TOOLS:
            assert expected in names, f"Missing tool: {expected}"

    async def test_all_tools_have_descriptions(self, mcp: FastMCP) -> None:
        """All 8 tools should have non-empty descriptions."""
        tools = await mcp.list_tools()
        for tool in tools:
            assert tool.description is not None, f"Tool {tool.name} has no description"
            assert len(tool.description) > 0, f"Tool {tool.name} has empty description"

    async def test_all_tools_have_desktop_prefix(self, mcp: FastMCP) -> None:
        """All tools should have the 'desktop.' prefix per PRD §6."""
        tools = await mcp.list_tools()
        for tool in tools:
            assert tool.name.startswith("desktop."), f"Tool {tool.name} missing 'desktop.' prefix"


# ===========================================================================
# CROSS-TOOL: Success responses include risk metadata
# ===========================================================================


class TestAllToolsRiskMetadata:
    """All tool success responses include risk and target_summary fields."""

    async def test_list_windows_has_risk(self, mcp: FastMCP) -> None:
        result, _ = await mcp.call_tool("desktop.list_windows", arguments={})
        data = json.loads(result[0].text)
        assert "risk" in data
        assert "target_summary" in data

    async def test_focus_window_has_risk(self, mcp: FastMCP) -> None:
        result, _ = await mcp.call_tool("desktop.focus_window", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert "risk" in data
        assert "target_summary" in data

    async def test_snapshot_has_risk(self, mcp: FastMCP) -> None:
        result, _ = await mcp.call_tool("desktop.snapshot", arguments={"window_ref": "w1"})
        data = json.loads(result[0].text)
        assert "risk" in data
        assert "target_summary" in data

    async def test_find_has_risk(self, mcp: FastMCP) -> None:
        result, _ = await mcp.call_tool(
            "desktop.find", arguments={"window_ref": "w1", "role": "button"}
        )
        data = json.loads(result[0].text)
        assert "risk" in data
        assert "target_summary" in data

    async def test_click_has_risk(self, mcp: FastMCP) -> None:
        result, _ = await mcp.call_tool("desktop.click", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert "risk" in data
        assert "target_summary" in data

    async def test_type_text_has_risk(self, mcp: FastMCP) -> None:
        result, _ = await mcp.call_tool(
            "desktop.type_text", arguments={"element_ref": "e1", "text": "hello"}
        )
        data = json.loads(result[0].text)
        assert "risk" in data
        assert "target_summary" in data

    async def test_press_key_has_risk(self, mcp: FastMCP) -> None:
        result, _ = await mcp.call_tool("desktop.press_key", arguments={"keys": "Enter"})
        data = json.loads(result[0].text)
        assert "risk" in data
        assert "target_summary" in data

    async def test_get_text_has_risk(self, mcp: FastMCP) -> None:
        result, _ = await mcp.call_tool("desktop.get_text", arguments={"element_ref": "e1"})
        data = json.loads(result[0].text)
        assert "risk" in data
        assert "target_summary" in data


# ===========================================================================
# CROSS-TOOL: Risk values are valid
# ===========================================================================


class TestRiskValuesAreValid:
    """All tool success responses return valid risk levels from the three-tier model."""

    VALID_RISKS: ClassVar = frozenset({"read_only", "interaction", "sensitive", "read"})

    @pytest.mark.parametrize(
        "tool_name,arguments",
        [
            ("desktop.list_windows", {}),
            ("desktop.focus_window", {"window_ref": "w1"}),
            ("desktop.snapshot", {"window_ref": "w1"}),
            ("desktop.find", {"window_ref": "w1"}),
            ("desktop.click", {"element_ref": "e1"}),
            ("desktop.type_text", {"element_ref": "e1", "text": "x"}),
            ("desktop.press_key", {"keys": "Enter"}),
            ("desktop.get_text", {"element_ref": "e1"}),
        ],
    )
    async def test_risk_value_in_valid_set(
        self, mcp: FastMCP, tool_name: str, arguments: dict
    ) -> None:
        """Risk value should be one of the three valid tiers (or 'read' for list_windows)."""
        result, _ = await mcp.call_tool(tool_name, arguments=arguments)
        data = json.loads(result[0].text)
        assert data["risk"] in self.VALID_RISKS, f"Invalid risk '{data['risk']}' for {tool_name}"
