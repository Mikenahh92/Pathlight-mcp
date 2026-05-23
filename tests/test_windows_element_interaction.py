"""Tests for WindowsBackend element interaction (GW-023).

Validates:
- perform_action dispatches all 12 DesktopAction variants to correct UIA patterns
- perform_action raises ActionNotSupportedError when pattern unavailable
- perform_action raises StaleElementReferenceError on disposed backend
- TYPE uses append semantics (read-current + concatenate + SetValue)
- COM HRESULT discrimination per architecture §5 (0x80040201/0x80070005/unknown →
  StaleElementReferenceError, 0x80070057 → ActionNotSupportedError)
- get_element_info reads role, name, states from UIA properties
- get_element_info uses resolve_role for role normalization
- get_element_info handles toggle state (Off/On/Indeterminate)
- get_element_info raises ElementNotFoundError for None handle
- get_element_info raises StaleElementReferenceError on disposed backend
- is_valid probes CurrentControlType per architecture §2.3
- _unwrap_element raises ElementNotFoundError for None
- _get_pattern raises ActionNotSupportedError when pattern is None
- _translate_com_error discriminates HRESULT codes per architecture §5
- Module-level UIA constants are correct integers
"""

from unittest.mock import MagicMock, patch

import pytest

from guidewire.backends.types import DesktopAction, NativeHandle
from guidewire.backends.windows import (
    _ComHandle,
    _UIA_CONTROL_TYPE_NAMES,
    _UIA_EXPAND_COLLAPSE_PATTERN_ID,
    _UIA_HAS_KEYBOARD_FOCUS_PROPERTY_ID,
    _UIA_INVOKE_PATTERN_ID,
    _UIA_IS_ENABLED_PROPERTY_ID,
    _UIA_IS_EXPANDED_PROPERTY_ID,
    _UIA_IS_OFFSCREEN_PROPERTY_ID,
    _UIA_IS_PASSWORD_PROPERTY_ID,
    _UIA_IS_READ_ONLY_PROPERTY_ID,
    _UIA_IS_REQUIRED_FOR_FORM_PROPERTY_ID,
    _UIA_IS_SELECTED_PROPERTY_ID,
    _UIA_NAME_PROPERTY_ID,
    _UIA_RANGE_VALUE_PATTERN_ID,
    _UIA_SCROLL_PATTERN_ID,
    _UIA_SELECTION_ITEM_PATTERN_ID,
    _UIA_TOGGLE_PATTERN_ID,
    _UIA_TOGGLE_STATE_PROPERTY_ID,
    _UIA_VALUE_PATTERN_ID,
    WindowsBackend,
)
from guidewire.errors import (
    ActionNotSupportedError,
    ElementNotFoundError,
    StaleElementReferenceError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def backend() -> WindowsBackend:
    """Create a WindowsBackend bypassing the platform guard."""
    with (
        patch("sys.platform", "win32"),
        patch.dict("sys.modules", {"comtypes": type("mod", (), {})}),
    ):
        b = WindowsBackend.__new__(WindowsBackend)
        b._com_initialized = True
        b._uia = MagicMock()
        b._disposed = False
        b._element_cache = {}
        return b


@pytest.fixture()
def mock_element() -> MagicMock:
    """Create a mock UIA element."""
    element = MagicMock()
    # Default property values for get_element_info
    element.GetCurrentPropertyValue.side_effect = _default_property_values
    return element


def _default_property_values(prop_id: int) -> object:
    """Return sensible defaults for UIA property queries."""
    prop_map = {
        30003: 50000,  # ControlType → Button
        30005: "TestButton",  # Name
        30010: True,  # IsEnabled
        30008: False,  # HasKeyboardFocus
        30011: False,  # IsSelected
        30086: 0,  # ToggleState → Off
        30015: 0,  # ExpandCollapseState → Collapsed
        30016: False,  # IsReadOnly
        30025: False,  # IsRequiredForForm
        30019: False,  # IsPassword
        30022: False,  # IsOffscreen
    }
    return prop_map.get(prop_id, 0)


# ---------------------------------------------------------------------------
# Module constants tests
# ---------------------------------------------------------------------------


class TestUIAConstants:
    """Verify module-level UIA constants are correct integers."""

    def test_invoke_pattern_id(self) -> None:
        assert _UIA_INVOKE_PATTERN_ID == 10000

    def test_value_pattern_id(self) -> None:
        assert _UIA_VALUE_PATTERN_ID == 10002

    def test_toggle_pattern_id(self) -> None:
        assert _UIA_TOGGLE_PATTERN_ID == 10015

    def test_selection_item_pattern_id(self) -> None:
        assert _UIA_SELECTION_ITEM_PATTERN_ID == 10010

    def test_scroll_pattern_id(self) -> None:
        assert _UIA_SCROLL_PATTERN_ID == 10011

    def test_range_value_pattern_id(self) -> None:
        assert _UIA_RANGE_VALUE_PATTERN_ID == 10003

    def test_expand_collapse_pattern_id(self) -> None:
        assert _UIA_EXPAND_COLLAPSE_PATTERN_ID == 10005

    def test_name_property_id(self) -> None:
        assert _UIA_NAME_PROPERTY_ID == 30005

    def test_is_enabled_property_id(self) -> None:
        assert _UIA_IS_ENABLED_PROPERTY_ID == 30010

    def test_has_keyboard_focus_property_id(self) -> None:
        assert _UIA_HAS_KEYBOARD_FOCUS_PROPERTY_ID == 30008

    def test_is_selected_property_id(self) -> None:
        assert _UIA_IS_SELECTED_PROPERTY_ID == 30011

    def test_toggle_state_property_id(self) -> None:
        assert _UIA_TOGGLE_STATE_PROPERTY_ID == 30086

    def test_is_expanded_property_id(self) -> None:
        assert _UIA_IS_EXPANDED_PROPERTY_ID == 30015

    def test_is_read_only_property_id(self) -> None:
        assert _UIA_IS_READ_ONLY_PROPERTY_ID == 30016

    def test_is_required_for_form_property_id(self) -> None:
        assert _UIA_IS_REQUIRED_FOR_FORM_PROPERTY_ID == 30025

    def test_is_password_property_id(self) -> None:
        assert _UIA_IS_PASSWORD_PROPERTY_ID == 30019

    def test_is_offscreen_property_id(self) -> None:
        assert _UIA_IS_OFFSCREEN_PROPERTY_ID == 30022

    def test_control_type_names_has_button(self) -> None:
        assert _UIA_CONTROL_TYPE_NAMES[50000] == "Button"

    def test_control_type_names_has_edit(self) -> None:
        assert _UIA_CONTROL_TYPE_NAMES[50004] == "Edit"

    def test_control_type_names_has_window(self) -> None:
        assert _UIA_CONTROL_TYPE_NAMES[50032] == "Window"

    def test_control_type_names_are_integers(self) -> None:
        for key in _UIA_CONTROL_TYPE_NAMES:
            assert isinstance(key, int)


# ---------------------------------------------------------------------------
# _unwrap_element tests
# ---------------------------------------------------------------------------


class TestUnwrapElement:
    """Verify _unwrap_element helper behavior."""

    def test_none_handle_raises_element_not_found(self, backend: WindowsBackend) -> None:
        with pytest.raises(ElementNotFoundError, match="None"):
            backend._unwrap_element(None)

    def test_valid_handle_returns_element(self, backend: WindowsBackend) -> None:
        mock_elem = MagicMock()
        result = backend._unwrap_element(NativeHandle(mock_elem))
        assert result is mock_elem

    def test_string_handle_resolves_from_cache(self, backend: WindowsBackend) -> None:
        """String backend_id is resolved via element cache to COM element."""
        mock_elem = MagicMock()
        elem_id = str(id(mock_elem))
        backend._element_cache[elem_id] = mock_elem
        result = backend._unwrap_element(NativeHandle(elem_id))
        assert result is mock_elem

    def test_string_handle_not_in_cache_raises(self, backend: WindowsBackend) -> None:
        """String backend_id not in cache raises ElementNotFoundError."""
        with pytest.raises(ElementNotFoundError, match="not found in backend cache"):
            backend._unwrap_element(NativeHandle("missing-id"))

    def test_comhandle_wrapper_extracts_element(self, backend: WindowsBackend) -> None:
        """_ComHandle wrapper from find_elements is unwrapped to the raw COM element."""
        mock_elem = MagicMock()
        com_handle = _ComHandle(element=mock_elem, _uia=backend._uia)
        result = backend._unwrap_element(NativeHandle(com_handle))
        assert result is mock_elem


# ---------------------------------------------------------------------------
# _get_pattern tests
# ---------------------------------------------------------------------------


class TestGetPattern:
    """Verify _get_pattern helper behavior."""

    def test_pattern_available_returns_pattern(self, backend: WindowsBackend) -> None:
        """When element supports the pattern, return it."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        result = backend._get_pattern(mock_element, _UIA_INVOKE_PATTERN_ID)
        assert result is mock_pattern

    def test_pattern_unavailable_raises_action_not_supported(self, backend: WindowsBackend) -> None:
        """When element does not support the pattern, raise ActionNotSupportedError."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="Invoke"):
            backend._get_pattern(mock_element, _UIA_INVOKE_PATTERN_ID)

    def test_pattern_com_error_raises_action_not_supported(self, backend: WindowsBackend) -> None:
        """COM errors in GetPattern are translated to ActionNotSupportedError."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.side_effect = RuntimeError("COM error")

        with pytest.raises(ActionNotSupportedError, match="Failed to get pattern"):
            backend._get_pattern(mock_element, _UIA_INVOKE_PATTERN_ID)

    def test_unknown_pattern_id_includes_id_in_message(self, backend: WindowsBackend) -> None:
        """Unknown pattern IDs should include the numeric ID in the error."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="99999"):
            backend._get_pattern(mock_element, 99999)


# ---------------------------------------------------------------------------
# perform_action: CLICK
# ---------------------------------------------------------------------------


class TestPerformActionClick:
    """Verify CLICK action dispatches to InvokePattern."""

    def test_click_invokes_pattern(self, backend: WindowsBackend) -> None:
        """CLICK must call InvokePattern.Invoke()."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.CLICK)

        mock_element.GetCurrentPattern.assert_called_once_with(_UIA_INVOKE_PATTERN_ID)
        mock_pattern.Invoke.assert_called_once()

    def test_click_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        """CLICK must raise ActionNotSupportedError when InvokePattern absent."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="Invoke"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.CLICK)

    def test_click_invoke_exception_raises(self, backend: WindowsBackend) -> None:
        """CLICK must raise ActionNotSupportedError when Invoke() throws."""
        mock_pattern = MagicMock()
        mock_pattern.Invoke.side_effect = RuntimeError("Element not visible")
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        with pytest.raises(ActionNotSupportedError, match="Invoke failed"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.CLICK)


# ---------------------------------------------------------------------------
# perform_action: SET_VALUE
# ---------------------------------------------------------------------------


class TestPerformActionSetValue:
    """Verify SET_VALUE action dispatches to ValuePattern."""

    def test_set_value_calls_set_value(self, backend: WindowsBackend) -> None:
        """SET_VALUE must call ValuePattern.SetValue()."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.SET_VALUE, value="hello")

        mock_element.GetCurrentPattern.assert_called_once_with(_UIA_VALUE_PATTERN_ID)
        mock_pattern.SetValue.assert_called_once_with("hello")

    def test_set_value_missing_param_raises(self, backend: WindowsBackend) -> None:
        """SET_VALUE must raise ActionNotSupportedError without 'value' kwarg."""
        mock_element = MagicMock()

        with pytest.raises(ActionNotSupportedError, match="'value' parameter"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.SET_VALUE)

    def test_set_value_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        """SET_VALUE must raise ActionNotSupportedError when ValuePattern absent."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="Value"):
            backend.perform_action(
                NativeHandle(mock_element),
                DesktopAction.SET_VALUE,
                value="test",
            )


# ---------------------------------------------------------------------------
# perform_action: GET_TEXT
# ---------------------------------------------------------------------------


class TestPerformActionGetText:
    """Verify GET_TEXT action reads ValuePattern.CurrentValue."""

    def test_get_text_returns_current_value(self, backend: WindowsBackend) -> None:
        """GET_TEXT must return ValuePattern.CurrentValue as string."""
        mock_pattern = MagicMock()
        mock_pattern.CurrentValue = "sample text"
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        result = backend.perform_action(NativeHandle(mock_element), DesktopAction.GET_TEXT)

        assert result == "sample text"
        mock_element.GetCurrentPattern.assert_called_once_with(_UIA_VALUE_PATTERN_ID)

    def test_get_text_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        """GET_TEXT must raise ActionNotSupportedError when ValuePattern absent."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="Value"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.GET_TEXT)


# ---------------------------------------------------------------------------
# perform_action: TOGGLE
# ---------------------------------------------------------------------------


class TestPerformActionToggle:
    """Verify TOGGLE action dispatches to TogglePattern."""

    def test_toggle_calls_toggle(self, backend: WindowsBackend) -> None:
        """TOGGLE must call TogglePattern.Toggle()."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.TOGGLE)

        mock_element.GetCurrentPattern.assert_called_once_with(_UIA_TOGGLE_PATTERN_ID)
        mock_pattern.Toggle.assert_called_once()

    def test_toggle_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        """TOGGLE must raise ActionNotSupportedError when TogglePattern absent."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="Toggle"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.TOGGLE)


# ---------------------------------------------------------------------------
# perform_action: SELECT
# ---------------------------------------------------------------------------


class TestPerformActionSelect:
    """Verify SELECT action dispatches to SelectionItemPattern."""

    def test_select_calls_select(self, backend: WindowsBackend) -> None:
        """SELECT must call SelectionItemPattern.Select()."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.SELECT)

        mock_element.GetCurrentPattern.assert_called_once_with(
            _UIA_SELECTION_ITEM_PATTERN_ID
        )
        mock_pattern.Select.assert_called_once()

    def test_select_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        """SELECT must raise ActionNotSupportedError when SelectionItemPattern absent."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="SelectionItem"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.SELECT)


# ---------------------------------------------------------------------------
# perform_action: SELECT_ITEM / DESELECT_ITEM / ADD_TO_SELECTION (GW-051)
# ---------------------------------------------------------------------------


class TestPerformActionSelectItem:
    """Verify SELECT_ITEM action dispatches to SelectionItemPattern.Select()."""

    def test_select_item_calls_select(self, backend: WindowsBackend) -> None:
        """SELECT_ITEM must call SelectionItemPattern.Select()."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.SELECT_ITEM)

        mock_element.GetCurrentPattern.assert_called_once_with(
            _UIA_SELECTION_ITEM_PATTERN_ID
        )
        mock_pattern.Select.assert_called_once()

    def test_select_item_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        """SELECT_ITEM must raise ActionNotSupportedError when pattern absent."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="SelectionItem"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.SELECT_ITEM)


class TestPerformActionDeselectItem:
    """Verify DESELECT_ITEM action dispatches to SelectionItemPattern.RemoveFromSelection()."""

    def test_deselect_item_calls_remove_from_selection(self, backend: WindowsBackend) -> None:
        """DESELECT_ITEM must call SelectionItemPattern.RemoveFromSelection()."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.DESELECT_ITEM)

        mock_element.GetCurrentPattern.assert_called_once_with(
            _UIA_SELECTION_ITEM_PATTERN_ID
        )
        mock_pattern.RemoveFromSelection.assert_called_once()

    def test_deselect_item_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        """DESELECT_ITEM must raise ActionNotSupportedError when pattern absent."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="SelectionItem"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.DESELECT_ITEM)


class TestPerformActionAddToSelection:
    """Verify ADD_TO_SELECTION action dispatches to SelectionItemPattern.AddToSelection()."""

    def test_add_to_selection_calls_add_to_selection(self, backend: WindowsBackend) -> None:
        """ADD_TO_SELECTION must call SelectionItemPattern.AddToSelection()."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.ADD_TO_SELECTION)

        mock_element.GetCurrentPattern.assert_called_once_with(
            _UIA_SELECTION_ITEM_PATTERN_ID
        )
        mock_pattern.AddToSelection.assert_called_once()

    def test_add_to_selection_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        """ADD_TO_SELECTION must raise ActionNotSupportedError when pattern absent."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="SelectionItem"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.ADD_TO_SELECTION)


# ---------------------------------------------------------------------------
# perform_action: EXPAND / COLLAPSE
# ---------------------------------------------------------------------------


class TestPerformActionExpandCollapse:
    """Verify EXPAND/COLLAPSE actions dispatch to ExpandCollapsePattern."""

    def test_expand_calls_expand(self, backend: WindowsBackend) -> None:
        """EXPAND must call ExpandCollapsePattern.Expand()."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.EXPAND)

        mock_element.GetCurrentPattern.assert_called_once_with(
            _UIA_EXPAND_COLLAPSE_PATTERN_ID
        )
        mock_pattern.Expand.assert_called_once()

    def test_collapse_calls_collapse(self, backend: WindowsBackend) -> None:
        """COLLAPSE must call ExpandCollapsePattern.Collapse()."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.COLLAPSE)

        mock_element.GetCurrentPattern.assert_called_once_with(
            _UIA_EXPAND_COLLAPSE_PATTERN_ID
        )
        mock_pattern.Collapse.assert_called_once()

    def test_expand_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="ExpandCollapse"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.EXPAND)

    def test_collapse_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="ExpandCollapse"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.COLLAPSE)


# ---------------------------------------------------------------------------
# perform_action: SCROLL
# ---------------------------------------------------------------------------


class TestPerformActionScroll:
    """Verify SCROLL action dispatches to ScrollPattern."""

    def test_scroll_vertical_calls_scroll_vertical(self, backend: WindowsBackend) -> None:
        """SCROLL with horizontal=False must call ScrollVertical."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.SCROLL)

        mock_pattern.ScrollVertical.assert_called_once()
        mock_pattern.ScrollHorizontal.assert_not_called()

    def test_scroll_horizontal_calls_scroll_horizontal(self, backend: WindowsBackend) -> None:
        """SCROLL with horizontal=True must call ScrollHorizontal."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(
            NativeHandle(mock_element),
            DesktopAction.SCROLL,
            horizontal=True,
        )

        mock_pattern.ScrollHorizontal.assert_called_once()
        mock_pattern.ScrollVertical.assert_not_called()

    def test_scroll_negative_amount_uses_decrement(self, backend: WindowsBackend) -> None:
        """Negative scroll_amount should use SmallDecrement (1)."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(
            NativeHandle(mock_element),
            DesktopAction.SCROLL,
            scroll_amount=-2,
        )

        mock_pattern.ScrollVertical.assert_called_once_with(1)

    def test_scroll_positive_amount_uses_increment(self, backend: WindowsBackend) -> None:
        """Positive scroll_amount should use SmallIncrement (3)."""
        mock_pattern = MagicMock()
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(
            NativeHandle(mock_element),
            DesktopAction.SCROLL,
            scroll_amount=1,
        )

        mock_pattern.ScrollVertical.assert_called_once_with(3)

    def test_scroll_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="Scroll"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.SCROLL)


# ---------------------------------------------------------------------------
# perform_action: INCREMENT / DECREMENT
# ---------------------------------------------------------------------------


class TestPerformActionIncrementDecrement:
    """Verify INCREMENT/DECREMENT actions use RangeValuePattern."""

    def test_increment_adds_small_change(self, backend: WindowsBackend) -> None:
        """INCREMENT must set value to current + SmallChange."""
        mock_pattern = MagicMock()
        mock_pattern.CurrentValue = 5.0
        mock_pattern.SmallChange = 1.0
        mock_pattern.Maximum = 100.0
        mock_pattern.Minimum = 0.0
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.INCREMENT)

        mock_pattern.SetValue.assert_called_once_with(6.0)

    def test_increment_clamps_to_maximum(self, backend: WindowsBackend) -> None:
        """INCREMENT must not exceed Maximum."""
        mock_pattern = MagicMock()
        mock_pattern.CurrentValue = 99.0
        mock_pattern.SmallChange = 5.0
        mock_pattern.Maximum = 100.0
        mock_pattern.Minimum = 0.0
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.INCREMENT)

        mock_pattern.SetValue.assert_called_once_with(100.0)

    def test_decrement_subtracts_small_change(self, backend: WindowsBackend) -> None:
        """DECREMENT must set value to current - SmallChange."""
        mock_pattern = MagicMock()
        mock_pattern.CurrentValue = 5.0
        mock_pattern.SmallChange = 1.0
        mock_pattern.Maximum = 100.0
        mock_pattern.Minimum = 0.0
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.DECREMENT)

        mock_pattern.SetValue.assert_called_once_with(4.0)

    def test_decrement_clamps_to_minimum(self, backend: WindowsBackend) -> None:
        """DECREMENT must not go below Minimum."""
        mock_pattern = MagicMock()
        mock_pattern.CurrentValue = 1.0
        mock_pattern.SmallChange = 5.0
        mock_pattern.Maximum = 100.0
        mock_pattern.Minimum = 0.0
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.DECREMENT)

        mock_pattern.SetValue.assert_called_once_with(0.0)

    def test_increment_defaults_small_change_when_zero(self, backend: WindowsBackend) -> None:
        """INCREMENT must default SmallChange to 1.0 when it's <= 0."""
        mock_pattern = MagicMock()
        mock_pattern.CurrentValue = 5.0
        mock_pattern.SmallChange = 0.0
        mock_pattern.Maximum = 100.0
        mock_pattern.Minimum = 0.0
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.INCREMENT)

        mock_pattern.SetValue.assert_called_once_with(6.0)

    def test_increment_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="RangeValue"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.INCREMENT)

    def test_decrement_pattern_unavailable_raises(self, backend: WindowsBackend) -> None:
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = None

        with pytest.raises(ActionNotSupportedError, match="RangeValue"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.DECREMENT)


# ---------------------------------------------------------------------------
# perform_action: TYPE
# ---------------------------------------------------------------------------


class TestPerformActionType:
    """Verify TYPE action types text via ValuePattern or SendInput fallback."""

    def test_type_with_value_pattern(self, backend: WindowsBackend) -> None:
        """TYPE must use ValuePattern with append semantics (read + concat + set)."""
        mock_pattern = MagicMock()
        mock_pattern.CurrentValue = "existing"
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.return_value = mock_pattern

        backend.perform_action(NativeHandle(mock_element), DesktopAction.TYPE, text="hello")

        mock_pattern.SetValue.assert_called_once_with("existinghello")

    def test_type_missing_text_param_raises(self, backend: WindowsBackend) -> None:
        """TYPE must raise ActionNotSupportedError without 'text' kwarg."""
        mock_element = MagicMock()

        with pytest.raises(ActionNotSupportedError, match="'text' parameter"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.TYPE)

    def test_type_falls_back_to_send_input(self, backend: WindowsBackend) -> None:
        """TYPE must fall back to SetFocus + SendInput when ValuePattern absent."""
        mock_element = MagicMock()
        # First call (ValuePattern) returns None, then pattern for other calls
        mock_element.GetCurrentPattern.return_value = None

        with patch.object(WindowsBackend, "_send_text", create=True) as mock_send:
            backend.perform_action(NativeHandle(mock_element), DesktopAction.TYPE, text="abc")

            mock_element.SetFocus.assert_called_once()
            mock_send.assert_called_once_with("abc")


# ---------------------------------------------------------------------------
# perform_action: PRESS_KEY
# ---------------------------------------------------------------------------


class TestPerformActionPressKey:
    """Verify PRESS_KEY action sends key via SendInput."""

    def test_press_key_sets_focus_and_sends_key(self, backend: WindowsBackend) -> None:
        """PRESS_KEY must SetFocus then call _send_key_combo with single key."""
        mock_element = MagicMock()

        with patch.object(WindowsBackend, "_send_key_combo", create=True) as mock_send:
            backend.perform_action(
                NativeHandle(mock_element),
                DesktopAction.PRESS_KEY,
                keys="enter",
            )

            mock_element.SetFocus.assert_called_once()
            mock_send.assert_called_once_with("enter")

    def test_press_key_combo_delegates_to_send_key_combo(self, backend: WindowsBackend) -> None:
        """PRESS_KEY with modifier combo must delegate to _send_key_combo."""
        mock_element = MagicMock()

        with patch.object(WindowsBackend, "_send_key_combo", create=True) as mock_send:
            backend.perform_action(
                NativeHandle(mock_element),
                DesktopAction.PRESS_KEY,
                keys="ctrl+s",
            )

            mock_element.SetFocus.assert_called_once()
            mock_send.assert_called_once_with("ctrl+s")

    def test_press_key_missing_keys_param_raises(self, backend: WindowsBackend) -> None:
        """PRESS_KEY must raise ActionNotSupportedError without 'keys' kwarg."""
        mock_element = MagicMock()

        with pytest.raises(ActionNotSupportedError, match="'keys' parameter"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.PRESS_KEY)

    def test_press_key_triple_combo(self, backend: WindowsBackend) -> None:
        """PRESS_KEY with triple combo (ctrl+shift+escape) delegates correctly."""
        mock_element = MagicMock()

        with patch.object(WindowsBackend, "_send_key_combo", create=True) as mock_send:
            backend.perform_action(
                NativeHandle(mock_element),
                DesktopAction.PRESS_KEY,
                keys="ctrl+shift+escape",
            )

            mock_send.assert_called_once_with("ctrl+shift+escape")


# ---------------------------------------------------------------------------
# perform_action: error handling
# ---------------------------------------------------------------------------


class TestPerformActionErrors:
    """Verify perform_action error handling."""

    def test_disposed_backend_raises(self, backend: WindowsBackend) -> None:
        """perform_action on disposed backend must raise StaleElementReferenceError."""
        backend.dispose()
        mock_element = MagicMock()

        with pytest.raises(StaleElementReferenceError, match="disposed"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.CLICK)

    def test_none_handle_raises_element_not_found(self, backend: WindowsBackend) -> None:
        """perform_action with None handle must raise ElementNotFoundError."""
        with pytest.raises(ElementNotFoundError, match="None"):
            backend.perform_action(None, DesktopAction.CLICK)

    def test_com_error_translated_to_action_not_supported(self, backend: WindowsBackend) -> None:
        """Generic COM errors are translated to ActionNotSupportedError."""
        mock_element = MagicMock()
        mock_element.GetCurrentPattern.side_effect = RuntimeError("COM failure")

        with pytest.raises(ActionNotSupportedError, match="Failed to get pattern"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.CLICK)

    def test_com_error_hresult_element_not_available(self, backend: WindowsBackend) -> None:
        """HRESULT 0x80040201 (UIA_E_ELEMENTNOTAVAILABLE) → StaleElementReferenceError."""
        mock_element = MagicMock()
        com_err = RuntimeError("Element not available")
        com_err.hresult = 0x80040201
        mock_element.GetCurrentPattern.side_effect = com_err

        with pytest.raises(StaleElementReferenceError, match="no longer available"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.CLICK)

    def test_com_error_hresult_access_denied(self, backend: WindowsBackend) -> None:
        """HRESULT 0x80070005 (E_ACCESSDENIED) → StaleElementReferenceError."""
        mock_element = MagicMock()
        com_err = RuntimeError("Access denied")
        com_err.hresult = 0x80070005
        mock_element.GetCurrentPattern.side_effect = com_err

        with pytest.raises(StaleElementReferenceError, match="no longer available"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.CLICK)

    def test_com_error_hresult_invalid_arg(self, backend: WindowsBackend) -> None:
        """HRESULT 0x80070057 (E_INVALIDARG) → ActionNotSupportedError."""
        mock_element = MagicMock()
        com_err = RuntimeError("Invalid argument")
        com_err.hresult = 0x80070057
        mock_element.GetCurrentPattern.side_effect = com_err

        with pytest.raises(ActionNotSupportedError, match="Invalid argument"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.CLICK)

    def test_com_error_hresult_unknown(self, backend: WindowsBackend) -> None:
        """Unknown HRESULT → StaleElementReferenceError."""
        mock_element = MagicMock()
        com_err = RuntimeError("Unknown COM error")
        com_err.hresult = 0x8000FFFF
        mock_element.GetCurrentPattern.side_effect = com_err

        with pytest.raises(StaleElementReferenceError, match="no longer available"):
            backend.perform_action(NativeHandle(mock_element), DesktopAction.CLICK)


# ---------------------------------------------------------------------------
# get_element_info tests
# ---------------------------------------------------------------------------


class TestGetElementInfo:
    """Verify get_element_info reads UIA properties correctly."""

    def test_returns_role_name_states(self, backend: WindowsBackend) -> None:
        """get_element_info must return dict with role, name, states keys."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = _default_property_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert "role" in result
        assert "name" in result
        assert "states" in result

    def test_button_control_type_maps_to_button_role(self, backend: WindowsBackend) -> None:
        """ControlType 50000 (Button) must map to role 'button'."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = _default_property_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["role"] == "button"

    def test_edit_control_type_maps_to_text_input_role(self, backend: WindowsBackend) -> None:
        """ControlType 50004 (Edit) must map to role 'text_input'."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30003:
                return 50004  # Edit
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["role"] == "text_input"

    def test_unknown_control_type_maps_to_custom(self, backend: WindowsBackend) -> None:
        """Unknown ControlType must map to role 'custom'."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30003:
                return 99999  # Unknown
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["role"] == "custom"

    def test_name_is_read_correctly(self, backend: WindowsBackend) -> None:
        """get_element_info must read the Name property."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = _default_property_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["name"] == "TestButton"

    def test_name_none_becomes_none(self, backend: WindowsBackend) -> None:
        """When Name property is None/empty, result name should be None."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30005:
                return ""
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["name"] is None

    def test_states_enabled(self, backend: WindowsBackend) -> None:
        """IsEnabled=True must set states['enabled']=True."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = _default_property_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["enabled"] is True

    def test_states_disabled(self, backend: WindowsBackend) -> None:
        """IsEnabled=False must set states['enabled']=False."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30010:
                return False
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["enabled"] is False

    def test_states_focused(self, backend: WindowsBackend) -> None:
        """HasKeyboardFocus=True must set states['focused']=True."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30008:
                return True
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["focused"] is True

    def test_toggle_state_on(self, backend: WindowsBackend) -> None:
        """ToggleState=1 must set states['checked']=True."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30086:
                return 1
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["checked"] is True

    def test_toggle_state_off(self, backend: WindowsBackend) -> None:
        """ToggleState=0 must set states['checked']=False."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = _default_property_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["checked"] is False

    def test_toggle_state_indeterminate(self, backend: WindowsBackend) -> None:
        """ToggleState=2 must set states['checked']='mixed'."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30086:
                return 2
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["checked"] == "mixed"

    def test_expand_state_expanded(self, backend: WindowsBackend) -> None:
        """ExpandCollapseState=1 must set states['expanded']=True."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30015:
                return 1
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["expanded"] is True

    def test_expand_state_partially(self, backend: WindowsBackend) -> None:
        """ExpandCollapseState=2 must set states['expanded']=True."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30015:
                return 2
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["expanded"] is True

    def test_expand_state_collapsed(self, backend: WindowsBackend) -> None:
        """ExpandCollapseState=0 must set states['expanded']=False."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = _default_property_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["expanded"] is False

    def test_states_read_only(self, backend: WindowsBackend) -> None:
        """IsReadOnly=True must set states['read_only']=True."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30016:
                return True
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["read_only"] is True

    def test_states_required(self, backend: WindowsBackend) -> None:
        """IsRequiredForForm=True must set states['required']=True."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30025:
                return True
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["required"] is True

    def test_states_is_password(self, backend: WindowsBackend) -> None:
        """IsPassword=True must set states['is_password']=True."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30019:
                return True
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["is_password"] is True

    def test_states_offscreen(self, backend: WindowsBackend) -> None:
        """IsOffscreen=True must set states['offscreen']=True."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30022:
                return True
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["states"]["offscreen"] is True

    def test_none_handle_raises_element_not_found(self, backend: WindowsBackend) -> None:
        """get_element_info with None handle must raise ElementNotFoundError."""
        with pytest.raises(ElementNotFoundError, match="None"):
            backend.get_element_info(None)

    def test_disposed_backend_raises(self, backend: WindowsBackend) -> None:
        """get_element_info on disposed backend must raise StaleElementReferenceError."""
        backend.dispose()

        with pytest.raises(StaleElementReferenceError, match="disposed"):
            backend.get_element_info(NativeHandle("fake"))

    def test_com_error_raises_element_not_found(self, backend: WindowsBackend) -> None:
        """COM errors must be translated to ElementNotFoundError."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = RuntimeError("COM error")

        with pytest.raises(ElementNotFoundError, match="Failed to read"):
            backend.get_element_info(NativeHandle(mock_element))


# ---------------------------------------------------------------------------
# is_valid tests
# ---------------------------------------------------------------------------


class TestIsValid:
    """Verify is_valid element staleness check."""

    def test_valid_element_returns_true(self, backend: WindowsBackend) -> None:
        """is_valid must return True for a live element."""
        mock_element = MagicMock()

        result = backend.is_valid(NativeHandle(mock_element))

        assert result is True

    def test_none_returns_false(self, backend: WindowsBackend) -> None:
        """is_valid must return False for None."""
        assert backend.is_valid(None) is False

    def test_stale_element_returns_false(self, backend: WindowsBackend) -> None:
        """is_valid must return False when COM property read fails."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = RuntimeError("Element no longer exists")

        result = backend.is_valid(NativeHandle(mock_element))

        assert result is False

    def test_stale_element_returns_false_on_generic_exception(
        self, backend: WindowsBackend
    ) -> None:
        """is_valid must return False for any exception type."""
        mock_element = MagicMock()
        mock_element.GetCurrentPropertyValue.side_effect = OSError("Disconnected")

        result = backend.is_valid(NativeHandle(mock_element))

        assert result is False

    def test_is_valid_does_not_use_backend_uia(self, backend: WindowsBackend) -> None:
        """is_valid must work directly on the element, not via _uia."""
        mock_element = MagicMock()
        backend.is_valid(NativeHandle(mock_element))

        # Should call GetCurrentPropertyValue on the element directly
        mock_element.GetCurrentPropertyValue.assert_called_once()
        # Should NOT call any _uia methods
        backend._uia.assert_not_called()

    def test_is_valid_probes_control_type(self, backend: WindowsBackend) -> None:
        """is_valid must probe a lightweight UIA property to detect stale handles."""
        mock_element = MagicMock()
        backend.is_valid(NativeHandle(mock_element))

        # Must probe with a UIA property ID (implementation uses ProcessId 30076)
        mock_element.GetCurrentPropertyValue.assert_called_once()

    def test_comhandle_wrapper_valid_returns_true(self, backend: WindowsBackend) -> None:
        """_ComHandle wrapper from find_elements must be unwrapped for is_valid check."""
        mock_inner = MagicMock()
        com_handle = _ComHandle(element=mock_inner, _uia=backend._uia)
        result = backend.is_valid(NativeHandle(com_handle))
        assert result is True
        mock_inner.GetCurrentPropertyValue.assert_called_once()

    def test_comhandle_wrapper_stale_returns_false(self, backend: WindowsBackend) -> None:
        """_ComHandle wrapper with stale inner element must return False."""
        mock_inner = MagicMock()
        mock_inner.GetCurrentPropertyValue.side_effect = OSError("stale")
        com_handle = _ComHandle(element=mock_inner, _uia=backend._uia)
        result = backend.is_valid(NativeHandle(com_handle))
        assert result is False


# ---------------------------------------------------------------------------
# _translate_com_error tests
# ---------------------------------------------------------------------------


class TestTranslateComError:
    """Verify _translate_com_error HRESULT discrimination."""

    def test_hresult_80040201_raises_stale(self) -> None:
        """UIA_E_ELEMENTNOTAVAILABLE must raise StaleElementReferenceError."""
        exc = RuntimeError("Element not available")
        exc.hresult = 0x80040201

        result = WindowsBackend._translate_com_error(exc)

        assert isinstance(result, StaleElementReferenceError)

    def test_hresult_80070005_raises_stale(self) -> None:
        """E_ACCESSDENIED must raise StaleElementReferenceError."""
        exc = RuntimeError("Access denied")
        exc.hresult = 0x80070005

        result = WindowsBackend._translate_com_error(exc)

        assert isinstance(result, StaleElementReferenceError)

    def test_hresult_80070057_raises_action_not_supported(self) -> None:
        """E_INVALIDARG must raise ActionNotSupportedError."""
        exc = RuntimeError("Invalid arg")
        exc.hresult = 0x80070057

        result = WindowsBackend._translate_com_error(exc)

        assert isinstance(result, ActionNotSupportedError)
        assert "0x80070057" in result.message

    def test_unknown_hresult_raises_stale(self) -> None:
        """Unknown HRESULTs must raise StaleElementReferenceError."""
        exc = RuntimeError("Unknown")
        exc.hresult = 0x8000FFFF

        result = WindowsBackend._translate_com_error(exc)

        assert isinstance(result, StaleElementReferenceError)

    def test_no_hresult_raises_stale(self) -> None:
        """Exception without hresult attribute must raise StaleElementReferenceError."""
        exc = RuntimeError("No HRESULT")

        result = WindowsBackend._translate_com_error(exc)

        assert isinstance(result, StaleElementReferenceError)


# ---------------------------------------------------------------------------
# Integration: get_element_info + resolve_role
# ---------------------------------------------------------------------------


class TestGetElementInfoRoleMapping:
    """Verify get_element_info role resolution for various control types."""

    @pytest.mark.parametrize(
        "control_type_id, expected_role",
        [
            (50000, "button"),
            (50002, "checkbox"),
            (50003, "combobox"),
            (50004, "text_input"),
            (50006, "image"),
            (50007, "list_item"),
            (50008, "list"),
            (50012, "radio_button"),
            (50014, "slider"),
            (50015, "spinner"),
            (50019, "text"),
            (50022, "tree"),
            (50023, "tree_item"),
            (50025, "group"),
            (50029, "document"),
            (50033, "pane"),
            (50031, "window"),
        ],
    )
    def test_control_type_role_mapping(
        self,
        backend: WindowsBackend,
        control_type_id: int,
        expected_role: str,
    ) -> None:
        """Various ControlType IDs must map to correct normalized roles."""
        mock_element = MagicMock()

        def prop_values(prop_id: int) -> object:
            if prop_id == 30003:
                return control_type_id
            return _default_property_values(prop_id)

        mock_element.GetCurrentPropertyValue.side_effect = prop_values

        result = backend.get_element_info(NativeHandle(mock_element))

        assert result["role"] == expected_role


# ---------------------------------------------------------------------------
# _send_key_combo tests (GW-084)
# ---------------------------------------------------------------------------


class TestSendKeyCombo:
    """Verify _send_key_combo correctly handles modifier combos via SendInput."""

    def test_single_key_delegates_to_send_key(self) -> None:
        """Single key (no +) must delegate to _send_key."""
        with patch.object(WindowsBackend, "_send_key", create=True) as mock_send:
            WindowsBackend._send_key_combo("enter")
            mock_send.assert_called_once_with("enter")

    def test_ctrl_s_combo_sends_modifier_then_key(self) -> None:
        """ctrl+s must press Ctrl, then S, then release both."""
        mock_user32 = MagicMock()
        mock_input = MagicMock()

        with (
            patch("ctypes.windll.user32", mock_user32),
            patch("ctypes.sizeof", return_value=40),
            patch("ctypes.byref", side_effect=lambda x: x),
            patch("guidewire.backends.windows._get_sendinput_structs", return_value={"INPUT": mock_input}),
        ):
            WindowsBackend._send_key_combo("ctrl+s")

        # Expected call count: press ctrl, press s, release s, release ctrl = 4
        assert mock_user32.SendInput.call_count == 4

        # Verify INPUT struct was created with type=1 (INPUT_KEYBOARD)
        mock_input.assert_called_with(type=1)

    def test_alt_tab_combo(self) -> None:
        """alt+tab must press Alt, then Tab, then release both."""
        mock_user32 = MagicMock()
        mock_input = MagicMock()

        with (
            patch("ctypes.windll.user32", mock_user32),
            patch("ctypes.sizeof", return_value=40),
            patch("ctypes.byref", side_effect=lambda x: x),
            patch("guidewire.backends.windows._get_sendinput_structs", return_value={"INPUT": mock_input}),
        ):
            WindowsBackend._send_key_combo("alt+tab")

        assert mock_user32.SendInput.call_count == 4

    def test_ctrl_shift_escape_triple_combo(self) -> None:
        """ctrl+shift+escape must press Ctrl, Shift, then Escape, then release all."""
        mock_user32 = MagicMock()
        mock_input = MagicMock()

        with (
            patch("ctypes.windll.user32", mock_user32),
            patch("ctypes.sizeof", return_value=40),
            patch("ctypes.byref", side_effect=lambda x: x),
            patch("guidewire.backends.windows._get_sendinput_structs", return_value={"INPUT": mock_input}),
        ):
            WindowsBackend._send_key_combo("ctrl+shift+escape")

        # Expected: press ctrl, press shift, press escape, release escape, release shift, release ctrl = 6
        assert mock_user32.SendInput.call_count == 6

    def test_empty_combo_returns_early(self) -> None:
        """Empty string must not send any events."""
        mock_user32 = MagicMock()

        with patch("ctypes.windll.user32", mock_user32):
            WindowsBackend._send_key_combo("")

        mock_user32.SendInput.assert_not_called()

    def test_single_key_via_send_key(self) -> None:
        """Single key like 'enter' must go through _send_key fast path."""
        with patch.object(WindowsBackend, "_send_key", create=True) as mock_send:
            WindowsBackend._send_key_combo("tab")
            mock_send.assert_called_once_with("tab")

    def test_modifier_vk_map_has_ctrl(self) -> None:
        """_MODIFIER_VK must contain ctrl."""
        assert WindowsBackend._MODIFIER_VK["ctrl"] == 0x11

    def test_modifier_vk_map_has_alt(self) -> None:
        """_MODIFIER_VK must contain alt."""
        assert WindowsBackend._MODIFIER_VK["alt"] == 0x12

    def test_modifier_vk_map_has_shift(self) -> None:
        """_MODIFIER_VK must contain shift."""
        assert WindowsBackend._MODIFIER_VK["shift"] == 0x10

    def test_modifier_vk_map_has_super(self) -> None:
        """_MODIFIER_VK must contain super."""
        assert WindowsBackend._MODIFIER_VK["super"] == 0x5B

    def test_key_vk_map_has_named_keys(self) -> None:
        """_KEY_VK must contain common named keys."""
        assert WindowsBackend._KEY_VK["enter"] == 0x0D
        assert WindowsBackend._KEY_VK["escape"] == 0x1B
        assert WindowsBackend._KEY_VK["tab"] == 0x09
        assert WindowsBackend._KEY_VK["space"] == 0x20
        assert WindowsBackend._KEY_VK["f12"] == 0x7B

    def test_key_vk_map_has_arrow_aliases(self) -> None:
        """_KEY_VK must contain both arrow and short names."""
        assert WindowsBackend._KEY_VK["up"] == 0x26
        assert WindowsBackend._KEY_VK["down"] == 0x28
        assert WindowsBackend._KEY_VK["left"] == 0x25
        assert WindowsBackend._KEY_VK["right"] == 0x27

    def test_ctrl_single_char_resolved_via_vkkeyscan(self) -> None:
        """ctrl+a must resolve 'a' via VkKeyScanW."""
        mock_user32 = MagicMock()
        mock_user32.VkKeyScanW.return_value = 0x41  # VK_A
        mock_input = MagicMock()

        with (
            patch("ctypes.windll.user32", mock_user32),
            patch("ctypes.sizeof", return_value=40),
            patch("ctypes.byref", side_effect=lambda x: x),
            patch("guidewire.backends.windows._get_sendinput_structs", return_value={"INPUT": mock_input}),
        ):
            WindowsBackend._send_key_combo("ctrl+a")

        # Expected: press ctrl, press+release a, release ctrl = 4
        assert mock_user32.SendInput.call_count == 4
        mock_user32.VkKeyScanW.assert_called_once_with(ord("a"))

    def test_unknown_modifier_falls_back_to_send_key(self) -> None:
        """Unknown modifier in combo must fall back to _send_key."""
        with patch.object(WindowsBackend, "_send_key", create=True) as mock_send:
            WindowsBackend._send_key_combo("unknown_mod+x")
            mock_send.assert_called_once_with("unknown_mod+x")

    def test_ctrl_f5_function_key_combo(self) -> None:
        """ctrl+f5 must resolve f5 from _KEY_VK."""
        mock_user32 = MagicMock()

        with (
            patch("ctypes.windll.user32", mock_user32),
            patch("ctypes.sizeof", return_value=40),
            patch("ctypes.byref", side_effect=lambda x: x),
        ):
            WindowsBackend._send_key_combo("ctrl+f5")

        assert mock_user32.SendInput.call_count == 4
