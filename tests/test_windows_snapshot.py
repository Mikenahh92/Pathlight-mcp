"""Tests for WindowsBackend.snapshot() and find_elements() (GW-022).

Validates:
- snapshot() walks the UIA tree and produces the correct DesktopElement schema.
- find_elements() searches by role and/or name with case-insensitive matching.
- Depth and node limits are respected.
- Role normalization via mapping tables.
- State extraction and normalization.
- Pattern-based action detection.
- Disposed backend raises WindowNotFoundError.
- COM errors are handled gracefully.

All tests mock the COM layer (comtypes / IUIAutomation) since they run on
any platform.
"""

import sys
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from pathlight_mcp.backends.types import NativeHandle
from pathlight_mcp.backends.windows import (
    _UIA_VALUE_PATTERN_ID,
    WindowsBackend,
    _ComHandle,
    _control_type_id_to_name,
    _read_state,
)
from pathlight_mcp.errors import WindowNotFoundError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_element(
    control_type_id: int = 50000,  # Button
    name: str | None = None,
    value: str | None = None,
    is_enabled: bool = True,
    has_focus: bool = False,
    is_offscreen: bool = False,
    bounds: tuple[int, int, int, int] | None = (10, 20, 100, 30),
    toggle_state: int | None = None,
    is_expanded: bool | None = None,
    children: list[Any] | None = None,
) -> MagicMock:
    """Create a mock IUIAutomationElement with configurable properties."""
    element = MagicMock()
    element.CurrentControlType = control_type_id
    element.CurrentName = name
    element.CurrentIsEnabled = is_enabled
    element.CurrentHasKeyboardFocus = has_focus
    element.CurrentIsSelected = False
    element.CurrentIsOffscreen = is_offscreen
    element.CurrentIsReadOnly = False
    element.CurrentIsRequiredForForm = False
    element.CurrentIsPassword = False

    # Bounding rectangle mock
    if bounds is not None:
        rect = MagicMock()
        rect.left, rect.top, rect.width, rect.height = bounds
        element.CurrentBoundingRectangle = rect
    else:
        element.CurrentBoundingRectangle = None

    # Value pattern
    value_pattern = MagicMock()
    value_pattern.CurrentValue = value
    element.GetCurrentPattern.return_value = value_pattern if value is not None else None

    # ToggleState
    if toggle_state is not None:
        element.CurrentToggleState = toggle_state
    else:
        type(element).CurrentToggleState = PropertyMock(
            side_effect=AttributeError("no ToggleState")
        )

    # IsExpanded
    if is_expanded is not None:
        element.CurrentIsExpanded = is_expanded
    else:
        type(element).CurrentIsExpanded = PropertyMock(side_effect=AttributeError("no IsExpanded"))

    # Visibility
    type(element).CurrentVisibility = PropertyMock(side_effect=AttributeError("no Visibility"))

    return element


def _make_backend() -> tuple[WindowsBackend, MagicMock]:
    """Create a WindowsBackend with mocked COM initialization.

    Returns:
        Tuple of (backend instance, mock tree walker).
        The walker is pre-configured with GetFirstChildElement returning
        None (no children) and GetNextSiblingElement returning None.
    """
    with (
        patch("sys.platform", "win32"),
        patch.dict("sys.modules", {"comtypes": type("mod", (), {})}),
    ):
        backend = WindowsBackend.__new__(WindowsBackend)
        backend._com_initialized = True
        uia_mock = MagicMock()
        walker_mock = MagicMock()
        walker_mock.GetFirstChildElement.return_value = None
        walker_mock.GetNextSiblingElement.return_value = None
        uia_mock.ControlViewWalker = walker_mock
        backend._uia = uia_mock
        backend._disposed = False
        backend._element_cache = {}
        return backend, walker_mock


# ---------------------------------------------------------------------------
# Module-level helper tests
# ---------------------------------------------------------------------------


class TestControlTypeIdToName:
    """Verify _control_type_id_to_name conversion."""

    def test_button(self) -> None:
        assert _control_type_id_to_name(50000) == "Button"

    def test_edit(self) -> None:
        assert _control_type_id_to_name(50004) == "Edit"

    def test_window(self) -> None:
        assert _control_type_id_to_name(50032) == "Window"

    def test_unknown_returns_custom(self) -> None:
        assert _control_type_id_to_name(99999) == "Custom"

    def test_zero_returns_custom(self) -> None:
        assert _control_type_id_to_name(0) == "Custom"


class TestReadState:
    """Verify _read_state helper."""

    def test_reads_enabled_state(self) -> None:
        element = MagicMock()
        element.CurrentIsEnabled = True
        states: dict[str, Any] = {}
        _read_state(element, "IsEnabled", "CurrentIsEnabled", bool, states)
        assert states["enabled"] is True

    def test_reads_disabled_state(self) -> None:
        element = MagicMock()
        element.CurrentIsEnabled = False
        states: dict[str, Any] = {}
        _read_state(element, "IsEnabled", "CurrentIsEnabled", bool, states)
        assert states["enabled"] is False

    def test_handles_attribute_error(self) -> None:
        element = MagicMock()
        del element.CurrentIsEnabled
        states: dict[str, Any] = {}
        _read_state(element, "IsEnabled", "CurrentIsEnabled", bool, states)
        assert states == {}


class TestPatternConstants:
    """Verify UIA pattern ID constants are correct."""

    def test_value_pattern_id(self) -> None:
        """_UIA_VALUE_PATTERN_ID must be 10002 (UIA_ValuePatternId)."""
        assert _UIA_VALUE_PATTERN_ID == 10002


class TestComHandle:
    """Verify _ComHandle dataclass (architecture §3.1)."""

    def test_is_alive_success(self) -> None:
        """is_alive returns True when CompareElements succeeds."""
        uia = MagicMock()
        element = MagicMock()
        handle = _ComHandle(element, uia)

        assert handle.is_alive() is True
        uia.CompareElements.assert_called_once_with(element, element)

    def test_is_alive_failure(self) -> None:
        """is_alive returns False when CompareElements raises."""
        uia = MagicMock()
        uia.CompareElements.side_effect = Exception("COM error")
        element = MagicMock()
        handle = _ComHandle(element, uia)

        assert handle.is_alive() is False

    def test_fields_accessible(self) -> None:
        """element and _uia fields are accessible."""
        element = MagicMock()
        uia = MagicMock()
        handle = _ComHandle(element, uia)

        assert handle.element is element
        assert handle._uia is uia


# ---------------------------------------------------------------------------
# snapshot() tests
# ---------------------------------------------------------------------------


class TestSnapshotDisposed:
    """Verify snapshot raises WindowNotFoundError when backend is disposed."""

    def test_snapshot_raises_on_disposed_backend(self) -> None:
        backend, _walker = _make_backend()
        backend.dispose()
        with pytest.raises(WindowNotFoundError, match="disposed"):
            backend.snapshot(NativeHandle("fake"))


class TestSnapshotTreeWalking:
    """Verify snapshot walks the UIA tree correctly."""

    def test_single_element_tree(self) -> None:
        """Snapshot of a single element (no children) returns correct schema."""
        backend, _walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="Main Window")  # Window

        result = backend.snapshot(NativeHandle(root))

        assert result["role"] == "window"
        assert result["name"] == "Main Window"
        assert result["children"] == []
        assert result["ref"] is not None
        assert isinstance(result["ref"], str)
        assert "states" in result
        assert "bounds" in result
        assert "actions" in result

    def test_nested_tree(self) -> None:
        """Snapshot with children produces nested tree."""
        backend, walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="Main Window")
        button = _make_mock_element(control_type_id=50000, name="OK")  # Button
        edit = _make_mock_element(control_type_id=50004, name="Username")  # Edit

        # Walk sequence:
        # GetFirstChild(root)→button, GetNext(button)→edit,
        # GetNext(edit)→None, GetFirstChild(button)→None,
        # GetFirstChild(edit)→None
        walker.GetFirstChildElement.side_effect = [button, None, None]
        walker.GetNextSiblingElement.side_effect = [edit, None]

        result = backend.snapshot(NativeHandle(root))

        assert result["role"] == "window"
        assert len(result["children"]) == 2
        assert result["children"][0]["role"] == "button"
        assert result["children"][0]["name"] == "OK"
        assert result["children"][1]["role"] == "text_input"
        assert result["children"][1]["name"] == "Username"

    def test_deeply_nested_tree(self) -> None:
        """Snapshot traverses multiple levels of nesting."""
        backend, walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="App")
        group = _make_mock_element(control_type_id=50026, name="Group")  # Group
        button = _make_mock_element(control_type_id=50000, name="Click")

        # Walk sequence (3 levels):
        # GetFirstChild(root)→group, GetNext(group)→None,
        # GetFirstChild(group)→button, GetNext(button)→None,
        # GetFirstChild(button)→None
        walker.GetFirstChildElement.side_effect = [group, button, None]
        walker.GetNextSiblingElement.side_effect = [None, None]

        result = backend.snapshot(NativeHandle(root), max_depth=3)

        assert result["role"] == "window"
        assert len(result["children"]) == 1
        assert result["children"][0]["role"] == "group"
        assert len(result["children"][0]["children"]) == 1
        assert result["children"][0]["children"][0]["role"] == "button"


class TestSnapshotDepthLimit:
    """Verify max_depth limits tree traversal."""

    def test_max_depth_zero(self) -> None:
        """max_depth=0 should only include the root node."""
        backend, _walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="Window")

        result = backend.snapshot(NativeHandle(root), max_depth=0)

        assert result["role"] == "window"
        assert result["children"] == []

    def test_max_depth_one(self) -> None:
        """max_depth=1 should include root and direct children."""
        backend, walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="Window")
        child = _make_mock_element(control_type_id=50000, name="Btn")

        walker.GetFirstChildElement.side_effect = [child, None]
        walker.GetNextSiblingElement.side_effect = [None]

        result = backend.snapshot(NativeHandle(root), max_depth=1)

        assert result["role"] == "window"
        assert len(result["children"]) == 1
        assert result["children"][0]["children"] == []


class TestSnapshotNodeLimit:
    """Verify max_nodes limits total nodes."""

    def test_max_nodes_one(self) -> None:
        """max_nodes=1 should only include the root."""
        backend, walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="Window")
        child = _make_mock_element(control_type_id=50000, name="Btn")

        walker.GetFirstChildElement.return_value = child
        walker.GetNextSiblingElement.return_value = None

        result = backend.snapshot(NativeHandle(root), max_depth=4, max_nodes=1)

        assert result["role"] == "window"
        # walker IS called to check for children, but children are not added
        assert result["children"] == []


class TestSnapshotRoleNormalization:
    """Verify ControlType IDs are normalized via mapping tables."""

    def test_button_normalized(self) -> None:
        assert _control_type_id_to_name(50000) == "Button"

    def test_edit_normalized_to_text_input(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50004, name="Edit")

        result = backend.snapshot(NativeHandle(root))
        assert result["role"] == "text_input"

    def test_checkbox_normalized(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50002, name="Check")

        result = backend.snapshot(NativeHandle(root))
        assert result["role"] == "checkbox"

    def test_combobox_normalized(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50003, name="Combo")

        result = backend.snapshot(NativeHandle(root))
        assert result["role"] == "combobox"

    def test_unknown_control_type_falls_back(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=99999, name="Unknown")

        result = backend.snapshot(NativeHandle(root))
        assert result["role"] == "custom"


class TestSnapshotStateExtraction:
    """Verify state properties are extracted and normalized."""

    def test_enabled_state(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50000, name="Btn", is_enabled=True)

        result = backend.snapshot(NativeHandle(root))
        assert result["states"]["enabled"] is True

    def test_disabled_state(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50000, name="Btn", is_enabled=False)

        result = backend.snapshot(NativeHandle(root))
        assert result["states"]["enabled"] is False

    def test_focused_state(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50000, name="Btn", has_focus=True)

        result = backend.snapshot(NativeHandle(root))
        assert result["states"]["focused"] is True

    def test_offscreen_state(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50000, name="Btn", is_offscreen=True)

        result = backend.snapshot(NativeHandle(root))
        assert result["states"]["offscreen"] is True


class TestSnapshotBoundsExtraction:
    """Verify bounding rectangle is extracted."""

    def test_bounds_present(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50032, name="Win", bounds=(10, 20, 800, 600))

        result = backend.snapshot(NativeHandle(root))
        assert result["bounds"] == {"x": 10, "y": 20, "width": 800, "height": 600}

    def test_bounds_none(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50032, name="Win", bounds=None)

        result = backend.snapshot(NativeHandle(root))
        assert result["bounds"] is None


class TestSnapshotActionsExtraction:
    """Verify pattern-based actions are detected."""

    def test_invoke_pattern_adds_action(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50000, name="Btn")

        invoke_pattern = MagicMock()
        root.GetCurrentPattern = MagicMock(
            side_effect=lambda pid: invoke_pattern if pid == 10000 else None
        )

        result = backend.snapshot(NativeHandle(root))
        assert "invoke" in result["actions"]

    def test_value_pattern_adds_action(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50004, name="Edit", value="text")

        value_pattern = MagicMock()
        value_pattern.CurrentValue = "text"
        root.GetCurrentPattern = MagicMock(
            side_effect=lambda pid: value_pattern if pid == 10002 else None
        )

        result = backend.snapshot(NativeHandle(root))
        assert "set_value" in result["actions"]

    def test_no_patterns_no_actions(self) -> None:
        backend, _walker = _make_backend()
        root = _make_mock_element(control_type_id=50020, name="Label", value=None)
        root.GetCurrentPattern = MagicMock(return_value=None)

        result = backend.snapshot(NativeHandle(root))
        assert result["actions"] == []


class TestSnapshotErrorHandling:
    """Verify graceful error handling during tree walking."""

    def test_com_error_on_child_walk_returns_partial_tree(self) -> None:
        """If GetFirstChildElement raises, snapshot returns root without children."""
        backend, walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="Win")
        walker.GetFirstChildElement.side_effect = Exception("COM error")

        result = backend.snapshot(NativeHandle(root))

        assert result["role"] == "window"
        assert result["children"] == []

    def test_com_error_on_property_read(self) -> None:
        """If element property read fails, snapshot still returns a node."""
        backend, _walker = _make_backend()

        root = MagicMock()
        type(root).CurrentControlType = PropertyMock(side_effect=Exception("COM error"))
        type(root).CurrentName = PropertyMock(side_effect=Exception("COM error"))
        type(root).CurrentIsEnabled = PropertyMock(side_effect=Exception("COM error"))
        type(root).CurrentHasKeyboardFocus = PropertyMock(side_effect=Exception("COM error"))
        type(root).CurrentIsSelected = PropertyMock(side_effect=Exception("COM error"))
        type(root).CurrentIsOffscreen = PropertyMock(side_effect=Exception("COM error"))
        type(root).CurrentIsReadOnly = PropertyMock(side_effect=Exception("COM error"))
        type(root).CurrentIsRequiredForForm = PropertyMock(side_effect=Exception("COM error"))
        type(root).CurrentIsPassword = PropertyMock(side_effect=Exception("COM error"))

        # Use callable side_effect so it actually raises (not returns the exception)
        def _raise_attr_err():
            raise AttributeError("no such property")

        type(root).CurrentToggleState = PropertyMock(side_effect=_raise_attr_err)
        type(root).CurrentIsExpanded = PropertyMock(side_effect=_raise_attr_err)
        type(root).CurrentVisibility = PropertyMock(side_effect=_raise_attr_err)
        root.CurrentBoundingRectangle = None
        root.GetCurrentPattern = MagicMock(return_value=None)

        result = backend.snapshot(NativeHandle(root))

        # Should not raise; role falls back to "custom" (control_type_id=0)
        assert result["role"] == "custom"
        assert result["bounds"] is None
        # States may contain residual values from mock defaults; the key
        # assertion is that the call does not crash.


# ---------------------------------------------------------------------------
# find_elements() tests
# ---------------------------------------------------------------------------


class TestFindElementsDisposed:
    """Verify find_elements raises WindowNotFoundError when disposed."""

    def test_find_elements_raises_on_disposed_backend(self) -> None:
        backend, _walker = _make_backend()
        backend.dispose()
        with pytest.raises(WindowNotFoundError, match="disposed"):
            backend.find_elements(NativeHandle("fake"), role="button")


class TestFindElementsByRole:
    """Verify find_elements matches by normalized role."""

    def test_find_buttons(self) -> None:
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        button1 = _make_mock_element(control_type_id=50000, name="OK")
        button2 = _make_mock_element(control_type_id=50000, name="Cancel")
        edit = _make_mock_element(control_type_id=50004, name="Input")

        # Walk: GetFirstChild(win)→b1, GetFirstChild(b1)→None,
        # GetNext(b1)→b2, GetFirstChild(b2)→None, GetNext(b2)→edit,
        # GetFirstChild(edit)→None, GetNext(edit)→None
        walker.GetFirstChildElement.side_effect = [button1, None, None, None]
        walker.GetNextSiblingElement.side_effect = [button2, edit, None]

        results = backend.find_elements(NativeHandle(window), role="button")

        assert len(results) == 2
        # Verify results contain the mock elements (not None or empty)
        assert all(r is not None for r in results)

    def test_find_text_inputs(self) -> None:
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        edit1 = _make_mock_element(control_type_id=50004, name="User")
        edit2 = _make_mock_element(control_type_id=50004, name="Pass")
        button = _make_mock_element(control_type_id=50000, name="Go")

        walker.GetFirstChildElement.side_effect = [edit1, None, None, None]
        walker.GetNextSiblingElement.side_effect = [edit2, button, None]

        results = backend.find_elements(NativeHandle(window), role="text_input")

        assert len(results) == 2

    def test_find_no_match(self) -> None:
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        button = _make_mock_element(control_type_id=50000, name="OK")

        walker.GetFirstChildElement.side_effect = [button]
        walker.GetNextSiblingElement.side_effect = [None]

        results = backend.find_elements(NativeHandle(window), role="checkbox")

        assert results == []


class TestFindElementsByName:
    """Verify find_elements matches by name (case-insensitive substring)."""

    def test_find_by_exact_name(self) -> None:
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        btn = _make_mock_element(control_type_id=50000, name="Submit")
        other = _make_mock_element(control_type_id=50000, name="Cancel")

        walker.GetFirstChildElement.side_effect = [btn, other]
        walker.GetNextSiblingElement.side_effect = [other, None]

        results = backend.find_elements(NativeHandle(window), name="Submit")

        assert len(results) == 1

    def test_find_by_case_insensitive(self) -> None:
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        btn = _make_mock_element(control_type_id=50000, name="Submit Form")

        walker.GetFirstChildElement.side_effect = [btn]
        walker.GetNextSiblingElement.side_effect = [None]

        results = backend.find_elements(NativeHandle(window), name="submit")

        assert len(results) == 1

    def test_find_by_substring(self) -> None:
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        btn = _make_mock_element(control_type_id=50000, name="Save Changes")

        walker.GetFirstChildElement.side_effect = [btn]
        walker.GetNextSiblingElement.side_effect = [None]

        results = backend.find_elements(NativeHandle(window), name="Save")

        assert len(results) == 1


class TestFindElementsByRoleAndName:
    """Verify find_elements matches by both role and name."""

    def test_role_and_name_match(self) -> None:
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        button = _make_mock_element(control_type_id=50000, name="Submit")
        text = _make_mock_element(control_type_id=50020, name="Submit Text")

        walker.GetFirstChildElement.side_effect = [button, text]
        walker.GetNextSiblingElement.side_effect = [text, None]

        results = backend.find_elements(NativeHandle(window), role="button", name="Submit")

        assert len(results) == 1

    def test_role_match_name_mismatch(self) -> None:
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        button = _make_mock_element(control_type_id=50000, name="Cancel")

        walker.GetFirstChildElement.side_effect = [button]
        walker.GetNextSiblingElement.side_effect = [None]

        results = backend.find_elements(NativeHandle(window), role="button", name="Submit")

        assert results == []


class TestFindElementsNoFilters:
    """Verify find_elements with no filters returns empty list (AC-15 / AD-5)."""

    def test_no_filters_returns_empty(self) -> None:
        """When both role and name are None, find_elements must return []."""
        backend, _walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")

        results = backend.find_elements(NativeHandle(window))

        assert results == []


class TestFindElementsNested:
    """Verify find_elements searches nested children."""

    def test_finds_in_nested_tree(self) -> None:
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        group = _make_mock_element(control_type_id=50026, name="Group")
        button = _make_mock_element(control_type_id=50000, name="NestedBtn")

        # Walk: GetFirstChild(window)→group, GetNext(group)→None,
        # GetFirstChild(group)→button, GetNext(button)→None
        walker.GetFirstChildElement.side_effect = [group, button, None]
        walker.GetNextSiblingElement.side_effect = [None, None]

        results = backend.find_elements(NativeHandle(window), role="button")

        assert len(results) == 1


class TestFindElementsErrorHandling:
    """Verify find_elements handles COM errors gracefully."""

    def test_com_error_returns_empty(self) -> None:
        """If tree walking fails, find_elements returns empty list."""
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        walker.GetFirstChildElement.side_effect = Exception("COM error")

        results = backend.find_elements(NativeHandle(window), role="button")

        assert results == []

    def test_element_property_error_skips_element(self) -> None:
        """If reading element properties fails, element is skipped."""
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        bad_element = MagicMock()
        type(bad_element).CurrentControlType = PropertyMock(side_effect=Exception("COM error"))
        type(bad_element).CurrentName = PropertyMock(side_effect=Exception("COM error"))

        walker.GetFirstChildElement.side_effect = [bad_element]
        walker.GetNextSiblingElement.side_effect = [None]

        # Should not raise; bad element's role falls back to "custom"
        results = backend.find_elements(NativeHandle(window), role="custom")

        assert len(results) == 1


# ---------------------------------------------------------------------------
# Offscreen filtering tests (QA fix: ControlViewWalker + explicit filter)
# ---------------------------------------------------------------------------


class TestSnapshotOffscreenFiltering:
    """Verify offscreen elements are excluded from snapshot tree."""

    def test_offscreen_child_excluded(self) -> None:
        """Offscreen children should be filtered out of the snapshot tree."""
        backend, walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="Window")
        visible_btn = _make_mock_element(control_type_id=50000, name="Visible")
        offscreen_btn = _make_mock_element(control_type_id=50000, name="Hidden", is_offscreen=True)

        walker.GetFirstChildElement.side_effect = [visible_btn, None, None]
        walker.GetNextSiblingElement.side_effect = [offscreen_btn, None]

        result = backend.snapshot(NativeHandle(root))

        assert result["role"] == "window"
        # Only the visible button should appear; offscreen button is filtered
        assert len(result["children"]) == 1
        assert result["children"][0]["name"] == "Visible"

    def test_offscreen_root_kept(self) -> None:
        """Root element is always included even if offscreen."""
        backend, _walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="Window", is_offscreen=True)

        result = backend.snapshot(NativeHandle(root))

        # Root is included (it's the starting point), but no children
        assert result["role"] == "window"
        assert result["name"] == "Window"

    def test_nested_offscreen_filtered(self) -> None:
        """Offscreen elements deep in the tree are filtered out."""
        backend, walker = _make_backend()

        root = _make_mock_element(control_type_id=50032, name="Window")
        group = _make_mock_element(control_type_id=50026, name="Group")
        offscreen_child = _make_mock_element(
            control_type_id=50000, name="Hidden", is_offscreen=True
        )

        walker.GetFirstChildElement.side_effect = [group, offscreen_child, None]
        walker.GetNextSiblingElement.side_effect = [None, None]

        result = backend.snapshot(NativeHandle(root), max_depth=3)

        assert result["role"] == "window"
        assert len(result["children"]) == 1  # group is included
        # group's offscreen child is filtered out
        assert result["children"][0]["children"] == []


# ---------------------------------------------------------------------------
# find_elements depth guard tests (QA fix: depth-limited search)
# ---------------------------------------------------------------------------


class TestFindElementsDepthGuard:
    """Verify find_elements traverses exhaustively (no depth limit, §3.3)."""

    def test_find_exhaustive_deep_tree(self) -> None:
        """find_elements should search all depths — no depth limit."""
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        # Create a deep chain: level 0 → level 1 → ... → level 25
        deep_elements = [
            _make_mock_element(control_type_id=50026, name=f"Level{i}") for i in range(25)
        ]

        # Chain: GetFirstChild(window)→e0, GetFirstChild(e0)→e1, ...
        # GetNextSibling returns None for all
        walker.GetFirstChildElement.side_effect = (
            [deep_elements[0]] + [deep_elements[i] for i in range(24)] + [None] * 26
        )
        walker.GetNextSiblingElement.return_value = None

        results = backend.find_elements(NativeHandle(window), role="group")

        # No depth limit — all 25 group elements should be found
        assert len(results) == 25

    def test_find_shallow_tree_finds_all(self) -> None:
        """find_elements should find all elements in a shallow tree."""
        backend, walker = _make_backend()

        window = _make_mock_element(control_type_id=50032, name="Window")
        btn1 = _make_mock_element(control_type_id=50000, name="OK")
        btn2 = _make_mock_element(control_type_id=50000, name="Cancel")

        walker.GetFirstChildElement.side_effect = [btn1, None, None]
        walker.GetNextSiblingElement.side_effect = [btn2, None]

        results = backend.find_elements(NativeHandle(window), role="button")

        assert len(results) == 2
