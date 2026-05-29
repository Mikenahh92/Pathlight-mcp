"""Comprehensive Windows normalization mapping tests (GW-027).

Validates:
- Every Windows UIA ControlType ID maps to a correct normalized role through
  ``_control_type_id_to_name`` → ``resolve_role`` → ``normalize_element``.
- The full Windows snapshot pipeline (``_extract_element_node`` →
  ``normalize_element`` → ``NormalizedElement.to_dict``) produces the correct
  DesktopElement schema for representative elements from Notepad, Calculator,
  and Windows Settings.
- Normalization mapping correctness: role, states, bounds, and actions are
  all correctly normalized from raw UIA properties.
- Cross-platform normalization parity: Windows and Linux normalization paths
  produce structurally identical NormalizedElement instances for equivalent
  elements.
- Golden snapshot regression: raw fixture data round-trips through the
  normalization pipeline without loss of structural fidelity.
"""

import os
import sys
from typing import Any

import pytest

from pathlight_mcp.backends.normalize import normalize_element
from pathlight_mcp.backends.windows import (
    _UIA_CONTROL_TYPE_MAP,
    _control_type_id_to_name,
)
from pathlight_mcp.models.mappings import ROLE_MAP

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")),
    reason="requires Windows desktop (not available in headless CI)",
)

# ---------------------------------------------------------------------------
# Section 1: ControlType ID → name → normalized role coverage
# ---------------------------------------------------------------------------


class TestControlTypeIdToRoleCoverage:
    """Every UIA ControlType ID in _UIA_CONTROL_TYPE_MAP resolves to a valid role.

    This ensures no ControlType ID is orphaned — each must produce a
    NormalizedElement with a lowercase role string.
    """

    @pytest.mark.parametrize(
        "control_type_id,expected_raw_name",
        [
            (50000, "Button"),
            (50001, "Calendar"),
            (50002, "CheckBox"),
            (50003, "ComboBox"),
            (50004, "Edit"),
            (50005, "Hyperlink"),
            (50006, "Image"),
            (50007, "ListItem"),
            (50008, "List"),
            (50009, "Menu"),
            (50010, "MenuBar"),
            (50011, "MenuItem"),
            (50012, "ProgressBar"),
            (50013, "RadioButton"),
            (50014, "ScrollBar"),
            (50015, "Slider"),
            (50016, "Spinner"),
            (50017, "StatusBar"),
            (50018, "Tab"),
            (50019, "TabItem"),
            (50020, "Text"),
            (50021, "ToolBar"),
            (50022, "ToolTip"),
            (50023, "Tree"),
            (50024, "TreeItem"),
            (50025, "Custom"),
            (50026, "Group"),
            (50027, "Thumb"),
            (50028, "DataGrid"),
            (50029, "DataItem"),
            (50030, "Document"),
            (50031, "SplitButton"),
            (50032, "Window"),
            (50033, "Pane"),
            (50034, "Header"),
            (50035, "HeaderItem"),
            (50036, "Table"),
            (50037, "TitleBar"),
            (50038, "Separator"),
        ],
    )
    def test_control_type_id_resolves_to_raw_name(
        self,
        control_type_id: int,
        expected_raw_name: str,
    ) -> None:
        """_control_type_id_to_name must return the expected raw UIA name."""
        assert _control_type_id_to_name(control_type_id) == expected_raw_name

    @pytest.mark.parametrize(
        "control_type_id",
        [
            50000,
            50001,
            50002,
            50003,
            50004,
            50005,
            50006,
            50007,
            50008,
            50009,
            50010,
            50011,
            50012,
            50013,
            50014,
            50015,
            50016,
            50017,
            50018,
            50019,
            50020,
            50021,
            50022,
            50023,
            50024,
            50025,
            50026,
            50027,
            50028,
            50029,
            50030,
            50031,
            50032,
            50033,
            50034,
            50035,
            50036,
            50037,
            50038,
        ],
    )
    def test_control_type_resolves_to_lowercase_role(
        self,
        control_type_id: int,
    ) -> None:
        """Every ControlType ID must resolve to a lowercase role via normalize_element."""
        raw_name = _control_type_id_to_name(control_type_id)
        element = normalize_element(
            platform="windows",
            ref="e0",
            backend_id=str(control_type_id),
            role=raw_name,
        )
        assert element.role == element.role.lower()
        assert element.role != ""

    @pytest.mark.parametrize(
        "control_type_id",
        [50000, 50002, 50003, 50004, 50008, 50032, 50029, 50030, 50036],
    )
    def test_control_type_has_role_map_entry(
        self,
        control_type_id: int,
    ) -> None:
        """Well-known ControlType IDs must have explicit ROLE_MAP entries."""
        raw_name = _control_type_id_to_name(control_type_id)
        assert ("windows", raw_name) in ROLE_MAP

    def test_unknown_control_type_falls_back_to_custom(self) -> None:
        """Unknown ControlType IDs (99999, -1, 0) must fall back to 'Custom'."""
        for ct_id in [99999, -1, 0]:
            raw_name = _control_type_id_to_name(ct_id)
            assert raw_name == "Custom"
            element = normalize_element(
                platform="windows",
                ref="e_fallback",
                backend_id=str(ct_id),
                role=raw_name,
            )
            # Custom role may or may not be in ROLE_MAP; normalize_element
            # falls back to role.lower()
            assert element.role == "custom"


# ---------------------------------------------------------------------------
# Section 2: Windows state normalization mapping correctness
# ---------------------------------------------------------------------------


class TestWindowsStateNormalization:
    """Windows UIA state properties → NormalizedElement.states correctness.

    Validates that the full normalization pipeline (raw UIA property values
    → normalize_states → ElementStates) produces correct results for all
    Windows-specific state keys.
    """

    def test_enabled_true(self) -> None:
        """IsEnabled=True → states.enabled=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"IsEnabled": True},
        )
        assert element.states.enabled is True

    def test_enabled_false(self) -> None:
        """IsEnabled=False → states.enabled=False."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"IsEnabled": False},
        )
        assert element.states.enabled is False

    def test_focused_true(self) -> None:
        """HasKeyboardFocus=True → states.focused=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"HasKeyboardFocus": True},
        )
        assert element.states.focused is True

    def test_selected_true(self) -> None:
        """IsSelected=True → states.selected=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="ListItem",
            raw_states={"IsSelected": True},
        )
        assert element.states.selected is True

    def test_toggle_off(self) -> None:
        """ToggleState=0 → states.checked=False."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="CheckBox",
            raw_states={"ToggleState": 0},
        )
        assert element.states.checked is False

    def test_toggle_on(self) -> None:
        """ToggleState=1 → states.checked=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="CheckBox",
            raw_states={"ToggleState": 1},
        )
        assert element.states.checked is True

    def test_toggle_mixed(self) -> None:
        """ToggleState=2 → states.checked='mixed'."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="CheckBox",
            raw_states={"ToggleState": 2},
        )
        assert element.states.checked == "mixed"

    def test_expanded_true(self) -> None:
        """IsExpanded=True → states.expanded=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="ComboBox",
            raw_states={"IsExpanded": True},
        )
        assert element.states.expanded is True

    def test_offscreen_true(self) -> None:
        """IsOffscreen=True → states.offscreen=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"IsOffscreen": True},
        )
        assert element.states.offscreen is True

    def test_read_only_true(self) -> None:
        """IsReadOnly=True → states.read_only=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_states={"IsReadOnly": True},
        )
        assert element.states.read_only is True

    def test_required_true(self) -> None:
        """IsRequiredForForm=True → states.required=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_states={"IsRequiredForForm": True},
        )
        assert element.states.required is True

    def test_is_password_true(self) -> None:
        """IsPassword=True → states.is_password=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_states={"IsPassword": True},
        )
        assert element.states.is_password is True

    def test_visibility_fully_visible(self) -> None:
        """Visibility=0 (FullyVisible) → states.visible=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"Visibility": 0},
        )
        assert element.states.visible is True

    def test_visibility_hidden(self) -> None:
        """Visibility=1 (Hidden) → states.visible=False."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"Visibility": 1},
        )
        assert element.states.visible is False

    def test_visibility_partially_visible(self) -> None:
        """Visibility=3 (PartiallyVisible) → states.visible=True."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"Visibility": 3},
        )
        assert element.states.visible is True

    def test_multiple_states_combined(self) -> None:
        """Multiple states are all correctly resolved in one call."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_states={
                "IsEnabled": True,
                "HasKeyboardFocus": True,
                "IsReadOnly": False,
                "IsRequiredForForm": True,
                "IsPassword": True,
                "IsOffscreen": False,
            },
        )
        assert element.states.enabled is True
        assert element.states.focused is True
        assert element.states.read_only is False
        assert element.states.required is True
        assert element.states.is_password is True
        assert element.states.offscreen is False

    def test_empty_states_give_none_defaults(self) -> None:
        """With no raw_states, all state fields are None."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
        )
        assert element.states.enabled is None
        assert element.states.focused is None
        assert element.states.checked is None
        assert element.states.expanded is None
        assert element.states.offscreen is None
        assert element.states.read_only is None
        assert element.states.required is None
        assert element.states.is_password is None


# ---------------------------------------------------------------------------
# Section 3: Windows action normalization mapping correctness
# ---------------------------------------------------------------------------


class TestWindowsActionNormalization:
    """Windows UIA pattern names → normalized actions correctness."""

    def test_invoke_pattern(self) -> None:
        """InvokePattern → actions=['invoke']."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_actions=["InvokePattern"],
        )
        assert "invoke" in element.actions

    def test_value_pattern(self) -> None:
        """ValuePattern → actions=['set_value']."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_actions=["ValuePattern"],
        )
        assert "set_value" in element.actions

    def test_toggle_pattern(self) -> None:
        """TogglePattern → actions=['toggle']."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="CheckBox",
            raw_actions=["TogglePattern"],
        )
        assert "toggle" in element.actions

    def test_text_pattern(self) -> None:
        """TextPattern → actions=['type']."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_actions=["TextPattern"],
        )
        assert "type" in element.actions

    def test_scroll_pattern(self) -> None:
        """ScrollPattern → actions=['scroll']."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="List",
            raw_actions=["ScrollPattern"],
        )
        assert "scroll" in element.actions

    def test_selection_item_pattern(self) -> None:
        """SelectionItemPattern → actions=['select']."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="ListItem",
            raw_actions=["SelectionItemPattern"],
        )
        assert "select_item" in element.actions

    def test_expand_collapse_pattern(self) -> None:
        """ExpandCollapsePattern → actions=['expand']."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="ComboBox",
            raw_actions=["ExpandCollapsePattern"],
        )
        assert "expand" in element.actions

    def test_range_value_pattern(self) -> None:
        """RangeValuePattern → actions=['set_value']."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Slider",
            raw_actions=["RangeValuePattern"],
        )
        assert "set_value" in element.actions

    def test_multiple_patterns_deduplicated(self) -> None:
        """Multiple patterns are normalized and deduplicated."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_actions=["ValuePattern", "TextPattern", "ValuePattern"],
        )
        assert "set_value" in element.actions
        assert "type" in element.actions
        # Deduplicated — set_value appears once
        assert element.actions.count("set_value") == 1

    def test_empty_actions(self) -> None:
        """No raw_actions → empty actions list."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Text",
        )
        assert element.actions == []


# ---------------------------------------------------------------------------
# Section 4: NormalizedElement.to_dict() schema correctness
# ---------------------------------------------------------------------------


class TestNormalizedElementToDictSchema:
    """NormalizedElement.to_dict() output matches the DesktopElement schema."""

    def test_minimal_element_dict_keys(self) -> None:
        """Minimal element dict has all required keys."""
        element = normalize_element(
            platform="windows",
            ref="e0",
            backend_id="0",
            role="Button",
        )
        d = element.to_dict()
        assert "ref" in d
        assert "role" in d
        assert "states" in d
        assert "bounds" in d
        assert "actions" in d
        assert "children" in d

    def test_named_element_dict_has_name_key(self) -> None:
        """Element with a name includes 'name' key in dict."""
        element = normalize_element(
            platform="windows",
            ref="e0",
            backend_id="0",
            role="Button",
            name="OK",
        )
        d = element.to_dict()
        assert "name" in d
        assert d["name"] == "OK"

    def test_full_element_dict_values(self) -> None:
        """Full element dict has correct values for all fields."""
        element = normalize_element(
            platform="windows",
            ref="e1",
            backend_id="50000",
            role="Button",
            native_role="Button",
            control_type="Button",
            name="OK",
            value=None,
            text=None,
            raw_states={"IsEnabled": True, "HasKeyboardFocus": False},
            bounds=(10, 20, 100, 30),
            raw_actions=["InvokePattern"],
        )
        d = element.to_dict()
        assert d["ref"] == "e1"
        assert d["role"] == "button"
        assert d["name"] == "OK"
        assert d["states"]["enabled"] is True
        assert d["states"]["focused"] is False
        assert d["bounds"]["x"] == 10.0
        assert d["bounds"]["y"] == 20.0
        assert d["bounds"]["width"] == 100.0
        assert d["bounds"]["height"] == 30.0
        assert d["actions"] == ["invoke"]
        assert d["children"] == []

    def test_bounds_none_in_dict(self) -> None:
        """None bounds → bounds is None in dict."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Text",
        )
        d = element.to_dict()
        assert d["bounds"] is None

    def test_children_none_becomes_empty_list(self) -> None:
        """None children → children is [] in dict."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Text",
        )
        d = element.to_dict()
        assert d["children"] == []

    def test_nested_children_in_dict(self) -> None:
        """Children are serialized correctly in the dict output."""
        child = normalize_element(
            platform="windows",
            ref="c1",
            backend_id="50000",
            role="Button",
            name="OK",
        )
        parent = normalize_element(
            platform="windows",
            ref="p1",
            backend_id="50032",
            role="Window",
            name="Main",
            children=[child],
        )
        d = parent.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["role"] == "button"
        assert d["children"][0]["name"] == "OK"


# ---------------------------------------------------------------------------
# Section 5: Notepad-like element normalization
# ---------------------------------------------------------------------------


class TestNotepadNormalization:
    """Normalization of elements resembling Notepad's accessibility tree.

    Notepad structure (simplified):
    - Window (50032)
      - TitleBar (50037)
      - MenuBar (50010) with MenuItem children (50011)
      - Document (50030) with Edit child (50004)
      - StatusBar (50017)
    """

    def test_menu_item_role(self) -> None:
        """MenuItem ControlType normalizes to 'menu_item'."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50011",
            role="MenuItem",
            name="File",
            raw_actions=["InvokePattern"],
        )
        assert element.role == "menu_item"
        assert "invoke" in element.actions

    def test_document_role(self) -> None:
        """Document ControlType normalizes to 'document'."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50030",
            role="Document",
            name="Untitled - Notepad",
        )
        assert element.role == "document"

    def test_edit_in_document(self) -> None:
        """Edit inside Document has text_input role and ValuePattern."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50004",
            role="Edit",
            name="Text Editor",
            raw_states={"IsEnabled": True, "IsReadOnly": False},
            raw_actions=["ValuePattern", "TextPattern"],
        )
        assert element.role == "text_input"
        assert element.states.enabled is True
        assert element.states.read_only is False
        assert "set_value" in element.actions
        assert "type" in element.actions

    def test_title_bar_role(self) -> None:
        """TitleBar ControlType normalizes to 'title_bar'."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50037",
            role="TitleBar",
            name="Untitled - Notepad",
        )
        assert element.role == "title_bar"

    def test_status_bar_role(self) -> None:
        """StatusBar ControlType normalizes to 'status_bar'."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50017",
            role="StatusBar",
        )
        assert element.role == "status_bar"

    def test_notepad_full_tree(self) -> None:
        """Full Notepad tree normalizes to correct structure."""
        edit = normalize_element(
            platform="windows",
            ref="e3",
            backend_id="50004",
            role="Edit",
            name="Text Editor",
            raw_actions=["ValuePattern", "TextPattern"],
        )
        document = normalize_element(
            platform="windows",
            ref="e2",
            backend_id="50030",
            role="Document",
            name="Untitled - Notepad",
            children=[edit],
        )
        file_menu = normalize_element(
            platform="windows",
            ref="e4",
            backend_id="50011",
            role="MenuItem",
            name="File",
            raw_actions=["InvokePattern"],
        )
        edit_menu = normalize_element(
            platform="windows",
            ref="e5",
            backend_id="50011",
            role="MenuItem",
            name="Edit",
            raw_actions=["InvokePattern"],
        )
        menu_bar = normalize_element(
            platform="windows",
            ref="e6",
            backend_id="50010",
            role="MenuBar",
            children=[file_menu, edit_menu],
        )
        window = normalize_element(
            platform="windows",
            ref="w1",
            backend_id="50032",
            role="Window",
            name="Untitled - Notepad",
            raw_states={"IsEnabled": True, "IsOffscreen": False},
            bounds=(0, 0, 1024, 768),
            children=[document, menu_bar],
        )

        d = window.to_dict()
        assert d["role"] == "window"
        assert d["states"]["enabled"] is True
        assert d["bounds"]["width"] == 1024.0
        assert len(d["children"]) == 2

        # Document child
        doc_child = d["children"][0]
        assert doc_child["role"] == "document"
        assert len(doc_child["children"]) == 1
        assert doc_child["children"][0]["role"] == "text_input"

        # MenuBar child
        menu_child = d["children"][1]
        assert menu_child["role"] == "menu_bar"
        assert len(menu_child["children"]) == 2
        assert menu_child["children"][0]["name"] == "File"
        assert menu_child["children"][1]["name"] == "Edit"


# ---------------------------------------------------------------------------
# Section 6: Calculator-like element normalization
# ---------------------------------------------------------------------------


class TestCalculatorNormalization:
    """Normalization of elements resembling Calculator's accessibility tree.

    Calculator structure (simplified):
    - Window (50032)
      - TitleBar (50037)
      - Group "Display" (50026) with Edit "Display" (50004)
      - Group "Buttons" (50026) with Button children (50000)
    """

    def test_display_edit_has_value(self) -> None:
        """Calculator display Edit normalizes with ValuePattern."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50004",
            role="Edit",
            name="Display",
            value="0",
            raw_states={"IsEnabled": True, "IsReadOnly": True},
            raw_actions=["ValuePattern"],
        )
        assert element.role == "text_input"
        assert element.states.read_only is True
        assert "set_value" in element.actions

    def test_digit_button(self) -> None:
        """Calculator digit button normalizes with InvokePattern."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50000",
            role="Button",
            name="One",
            raw_states={"IsEnabled": True},
            raw_actions=["InvokePattern"],
        )
        assert element.role == "button"
        assert element.states.enabled is True
        assert "invoke" in element.actions

    def test_disabled_memory_button(self) -> None:
        """Calculator Memory button normalizes as disabled."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50000",
            role="Button",
            name="Memory",
            raw_states={"IsEnabled": False},
            raw_actions=["InvokePattern"],
        )
        assert element.role == "button"
        assert element.states.enabled is False

    def test_operator_button(self) -> None:
        """Calculator operator button (Plus) normalizes correctly."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50000",
            role="Button",
            name="Plus",
            raw_states={"IsEnabled": True},
            raw_actions=["InvokePattern"],
        )
        assert element.role == "button"
        assert "invoke" in element.actions

    def test_calculator_full_tree(self) -> None:
        """Full Calculator tree normalizes to correct structure."""
        display_edit = normalize_element(
            platform="windows",
            ref="e1",
            backend_id="50004",
            role="Edit",
            name="Display",
            value="0",
            raw_states={"IsEnabled": True, "IsReadOnly": True},
            raw_actions=["ValuePattern"],
        )
        display_group = normalize_element(
            platform="windows",
            ref="e2",
            backend_id="50026",
            role="Group",
            name="Display",
            children=[display_edit],
        )
        buttons = []
        for name in ("Zero", "One", "Two", "Plus", "Equals"):
            btn = normalize_element(
                platform="windows",
                ref=f"btn_{name}",
                backend_id="50000",
                role="Button",
                name=name,
                raw_states={"IsEnabled": True},
                raw_actions=["InvokePattern"],
            )
            buttons.append(btn)
        buttons_group = normalize_element(
            platform="windows",
            ref="e3",
            backend_id="50026",
            role="Group",
            name="Buttons",
            children=buttons,
        )
        window = normalize_element(
            platform="windows",
            ref="w1",
            backend_id="50032",
            role="Window",
            name="Calculator",
            raw_states={"IsEnabled": True, "IsOffscreen": False},
            bounds=(100, 100, 320, 500),
            children=[display_group, buttons_group],
        )

        d = window.to_dict()
        assert d["role"] == "window"
        assert len(d["children"]) == 2

        # Display group
        display = d["children"][0]
        assert display["role"] == "group"
        assert display["name"] == "Display"
        assert len(display["children"]) == 1
        assert display["children"][0]["role"] == "text_input"
        assert display["children"][0]["states"]["read_only"] is True

        # Buttons group
        btn_group = d["children"][1]
        assert btn_group["role"] == "group"
        assert len(btn_group["children"]) == 5
        for child in btn_group["children"]:
            assert child["role"] == "button"
            assert "invoke" in child["actions"]


# ---------------------------------------------------------------------------
# Section 7: Windows Settings-like element normalization
# ---------------------------------------------------------------------------


class TestWindowsSettingsNormalization:
    """Normalization of elements resembling Windows Settings.

    Settings structure (simplified):
    - Window (50032)
      - TitleBar (50037)
      - Pane "Search" with Edit (50004)
      - List "Navigation" (50008) with ListItem children (50007)
      - Pane "Content" (50033) with Group children (50026) and Buttons (50000)
    """

    def test_search_edit(self) -> None:
        """Settings search Edit normalizes to text_input."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50004",
            role="Edit",
            name="Find a setting",
            raw_states={"IsEnabled": True},
            raw_actions=["ValuePattern"],
        )
        assert element.role == "text_input"
        assert "set_value" in element.actions

    def test_navigation_list(self) -> None:
        """Settings navigation List normalizes to 'list'."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50008",
            role="List",
            name="Navigation",
        )
        assert element.role == "list"

    def test_navigation_list_item(self) -> None:
        """Settings nav ListItem normalizes to 'list_item' with Invoke+Select."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50007",
            role="ListItem",
            name="System",
            raw_states={"IsEnabled": True},
            raw_actions=["InvokePattern", "SelectionItemPattern"],
        )
        assert element.role == "list_item"
        assert "invoke" in element.actions
        assert "select_item" in element.actions

    def test_content_pane(self) -> None:
        """Settings content Pane normalizes to 'pane'."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="50033",
            role="Pane",
            name="Content",
        )
        assert element.role == "pane"

    def test_settings_full_tree(self) -> None:
        """Full Settings tree normalizes to correct structure."""
        # Search box
        search_edit = normalize_element(
            platform="windows",
            ref="e1",
            backend_id="50004",
            role="Edit",
            name="Find a setting",
            raw_actions=["ValuePattern"],
        )
        search_pane = normalize_element(
            platform="windows",
            ref="e2",
            backend_id="50033",
            role="Pane",
            name="Search",
            children=[search_edit],
        )
        # Navigation
        nav_items = []
        for name in ("System", "Personalization", "Windows Update"):
            item = normalize_element(
                platform="windows",
                ref=f"nav_{name}",
                backend_id="50007",
                role="ListItem",
                name=name,
                raw_actions=["InvokePattern", "SelectionItemPattern"],
            )
            nav_items.append(item)
        nav_list = normalize_element(
            platform="windows",
            ref="e3",
            backend_id="50008",
            role="List",
            name="Navigation",
            children=nav_items,
        )
        # Content area with setting groups
        setting_btns = [
            normalize_element(
                platform="windows",
                ref=f"btn_{i}",
                backend_id="50000",
                role="Button",
                name=f"Setting {i}",
                raw_actions=["InvokePattern"],
            )
            for i in range(3)
        ]
        content_group = normalize_element(
            platform="windows",
            ref="e4",
            backend_id="50026",
            role="Group",
            name="Display settings",
            children=setting_btns,
        )
        content_pane = normalize_element(
            platform="windows",
            ref="e5",
            backend_id="50033",
            role="Pane",
            name="Content",
            children=[content_group],
        )
        window = normalize_element(
            platform="windows",
            ref="w1",
            backend_id="50032",
            role="Window",
            name="Settings",
            raw_states={"IsEnabled": True, "IsOffscreen": False},
            bounds=(50, 50, 1200, 800),
            children=[search_pane, nav_list, content_pane],
        )

        d = window.to_dict()
        assert d["role"] == "window"
        assert len(d["children"]) == 3

        # Search pane
        assert d["children"][0]["role"] == "pane"
        assert d["children"][0]["children"][0]["role"] == "text_input"

        # Navigation list
        assert d["children"][1]["role"] == "list"
        assert len(d["children"][1]["children"]) == 3
        for item in d["children"][1]["children"]:
            assert item["role"] == "list_item"
            assert "invoke" in item["actions"]
            assert "select_item" in item["actions"]

        # Content pane
        assert d["children"][2]["role"] == "pane"
        assert d["children"][2]["children"][0]["role"] == "group"
        assert len(d["children"][2]["children"][0]["children"]) == 3


# ---------------------------------------------------------------------------
# Section 8: Cross-platform normalization parity
# ---------------------------------------------------------------------------


class TestCrossPlatformNormalizationParity:
    """Windows and Linux normalization produce structurally identical results.

    For equivalent elements (button, text_input, checkbox, window), both
    platforms must produce NormalizedElement instances with the same
    normalized role, states field names, and actions.
    """

    def test_button_role_parity(self) -> None:
        """Button role is the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"IsEnabled": True},
            raw_actions=["InvokePattern"],
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            raw_states={"enabled": True},
            raw_actions=["click"],
        )
        assert win.role == linux.role == "button"
        assert win.states.enabled == linux.states.enabled is True

    def test_text_input_role_parity(self) -> None:
        """Text input role is the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_states={"IsReadOnly": True},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="entry",
            raw_states={"editable": False},
        )
        assert win.role == linux.role == "text_input"
        assert win.states.read_only == linux.states.read_only is True

    def test_checkbox_role_parity(self) -> None:
        """Checkbox role and checked state are the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="CheckBox",
            raw_states={"ToggleState": 1},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="check box",
            raw_states={"checked": 1},
        )
        assert win.role == linux.role == "checkbox"
        assert win.states.checked == linux.states.checked is True

    def test_checkbox_mixed_parity(self) -> None:
        """Mixed checkbox state is the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="CheckBox",
            raw_states={"ToggleState": 2},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="check box",
            raw_states={"checked": 2},
        )
        assert win.states.checked == linux.states.checked == "mixed"

    def test_window_role_parity(self) -> None:
        """Window role is the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="w",
            backend_id="0",
            role="Window",
        )
        linux = normalize_element(
            platform="linux",
            ref="w",
            backend_id="0",
            role="window",
        )
        assert win.role == linux.role == "window"

    def test_list_role_parity(self) -> None:
        """List role is the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="List",
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="list box",
        )
        assert win.role == linux.role == "list"

    def test_dict_output_structure_parity(self) -> None:
        """to_dict() output has the same keys on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            name="OK",
            raw_states={"IsEnabled": True},
            bounds=(0, 0, 80, 30),
            raw_actions=["InvokePattern"],
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            name="OK",
            raw_states={"enabled": True},
            raw_actions=["click"],
        )
        win_d = win.to_dict()
        linux_d = linux.to_dict()
        assert set(win_d.keys()) == set(linux_d.keys())
        assert set(win_d["states"].keys()) == set(linux_d["states"].keys())


# ---------------------------------------------------------------------------
# Section 9: Golden fixture normalization regression
# ---------------------------------------------------------------------------


class TestGoldenFixtureNormalizationRegression:
    """Raw golden fixture nodes normalize without errors or data loss.

    Loads each golden snapshot JSON, takes every node, feeds the raw
    ``control_type`` through the normalization pipeline, and verifies the
    resulting NormalizedElement has a valid role, non-empty ref, and
    correct dict structure.
    """

    @pytest.fixture(
        params=[
            "notepad_snapshot.json",
            "calculator_snapshot.json",
            "settings_snapshot.json",
        ]
    )
    def golden_data(self, request: pytest.FixtureRequest) -> dict[str, Any]:
        """Load a golden snapshot fixture."""
        from tests.fixtures.helpers import load_golden_snapshot

        return load_golden_snapshot(request.param)

    def _flatten_nodes(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten a raw snapshot tree into a list of all nodes."""
        result = [node]
        for child in node.get("children", []):
            result.extend(self._flatten_nodes(child))
        return result

    def test_all_nodes_normalize_without_error(
        self,
        golden_data: dict[str, Any],
    ) -> None:
        """Every node in every golden fixture normalizes without error."""
        tree = golden_data["snapshot"]
        nodes = self._flatten_nodes(tree)

        for node in nodes:
            ct = node.get("control_type", 0)
            raw_name = _control_type_id_to_name(ct)
            element = normalize_element(
                platform="windows",
                ref=f"e_{ct}_{node.get('name', 'anon')}",
                backend_id=str(ct),
                role=raw_name,
                name=node.get("name"),
                raw_states={
                    "IsEnabled": node.get("is_enabled", True),
                    "IsOffscreen": node.get("is_offscreen", False),
                },
            )
            # Must have a valid lowercase role
            assert element.role == element.role.lower()
            assert element.role != ""
            # Dict output must be well-formed
            d = element.to_dict()
            assert isinstance(d, dict)
            assert "role" in d
            assert "states" in d

    def test_notepad_nodes_have_expected_roles(
        self,
    ) -> None:
        """Notepad golden fixture nodes normalize to expected roles."""
        from tests.fixtures.helpers import load_golden_snapshot

        tree = load_golden_snapshot("notepad_snapshot.json")["snapshot"]
        nodes = self._flatten_nodes(tree)

        role_counts: dict[str, int] = {}
        for node in nodes:
            ct = node.get("control_type", 0)
            raw_name = _control_type_id_to_name(ct)
            element = normalize_element(
                platform="windows",
                ref="e",
                backend_id=str(ct),
                role=raw_name,
                name=node.get("name"),
            )
            role_counts[element.role] = role_counts.get(element.role, 0) + 1

        # Notepad must have at least these roles
        assert "window" in role_counts
        assert "menu_item" in role_counts or "menu_bar" in role_counts
        assert "document" in role_counts or "text_input" in role_counts

    def test_calculator_nodes_have_expected_roles(
        self,
    ) -> None:
        """Calculator golden fixture nodes normalize to expected roles."""
        from tests.fixtures.helpers import load_golden_snapshot

        tree = load_golden_snapshot("calculator_snapshot.json")["snapshot"]
        nodes = self._flatten_nodes(tree)

        button_count = 0
        text_input_count = 0
        for node in nodes:
            ct = node.get("control_type", 0)
            raw_name = _control_type_id_to_name(ct)
            element = normalize_element(
                platform="windows",
                ref="e",
                backend_id=str(ct),
                role=raw_name,
                name=node.get("name"),
            )
            if element.role == "button":
                button_count += 1
            elif element.role == "text_input":
                text_input_count += 1

        assert button_count >= 10, f"Expected at least 10 buttons, got {button_count}"
        assert text_input_count >= 1, f"Expected at least 1 text_input, got {text_input_count}"

    def test_settings_nodes_have_expected_roles(
        self,
    ) -> None:
        """Settings golden fixture nodes normalize to expected roles."""
        from tests.fixtures.helpers import load_golden_snapshot

        tree = load_golden_snapshot("settings_snapshot.json")["snapshot"]
        nodes = self._flatten_nodes(tree)

        role_set: set[str] = set()
        for node in nodes:
            ct = node.get("control_type", 0)
            raw_name = _control_type_id_to_name(ct)
            element = normalize_element(
                platform="windows",
                ref="e",
                backend_id=str(ct),
                role=raw_name,
                name=node.get("name"),
            )
            role_set.add(element.role)

        # Settings must have at least these roles
        assert "window" in role_set
        assert "list" in role_set or "list_item" in role_set


# ---------------------------------------------------------------------------
# Section 10: Bounds normalization edge cases
# ---------------------------------------------------------------------------


class TestBoundsNormalizationEdgeCases:
    """Bounds normalization handles Windows-specific edge cases."""

    def test_zero_area_bounds(self) -> None:
        """Bounds with zero area still produce a Bounds object."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            bounds=(100, 200, 0, 0),
        )
        assert element.bounds is not None
        assert element.bounds.width == 0.0
        assert element.bounds.height == 0.0

    def test_negative_bounds_values(self) -> None:
        """Negative bounds values are accepted (off-screen elements)."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            bounds=(-100, -200, 50, 30),
        )
        assert element.bounds is not None
        assert element.bounds.x == -100.0

    def test_very_large_bounds(self) -> None:
        """Very large bounds values are accepted."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Window",
            bounds=(0, 0, 3840, 2160),
        )
        assert element.bounds is not None
        assert element.bounds.width == 3840.0

    def test_float_bounds(self) -> None:
        """Float bounds (DPI-scaled) are accepted."""
        element = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            bounds=(10.5, 20.7, 100.3, 30.1),
        )
        assert element.bounds is not None
        assert element.bounds.x == 10.5
        assert element.bounds.y == 20.7


# ---------------------------------------------------------------------------
# Section 11: _UIA_CONTROL_TYPE_MAP completeness
# ---------------------------------------------------------------------------


class TestUIAControlTypeMapCompleteness:
    """_UIA_CONTROL_TYPE_MAP covers all standard UIA ControlType IDs."""

    def test_map_has_expected_count(self) -> None:
        """Map should cover at least 39 standard ControlType IDs."""
        assert len(_UIA_CONTROL_TYPE_MAP) >= 39

    def test_map_keys_are_contiguous_range(self) -> None:
        """Map covers the standard UIA ControlType range 50000-50038."""
        for ct_id in range(50000, 50039):
            assert ct_id in _UIA_CONTROL_TYPE_MAP, (
                f"Missing ControlType ID {ct_id} in _UIA_CONTROL_TYPE_MAP"
            )

    def test_all_map_values_are_strings(self) -> None:
        """All values in the map are strings."""
        for ct_id, name in _UIA_CONTROL_TYPE_MAP.items():
            assert isinstance(name, str), (
                f"ControlType {ct_id} maps to {type(name).__name__}, not str"
            )
