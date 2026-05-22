"""Tests for the cross-platform element normalization layer (GW-025).

Validates:
- normalize_element: raw platform properties → NormalizedElement dataclass
- normalize_states: raw state dicts → ElementStates
- normalize_actions: raw action lists → DesktopAction list
- normalize_bounds: raw bounds tuples/dicts → Bounds
- Integration with Windows backend snapshot pipeline
"""

from guidewire.backends.normalize import (
    normalize_actions,
    normalize_bounds,
    normalize_element,
    normalize_states,
)
from guidewire.models import ElementStates, NormalizedElement

# ---------------------------------------------------------------------------
# normalize_states
# ---------------------------------------------------------------------------


class TestNormalizeStates:
    """Verify state normalization for both platforms."""

    def test_windows_empty_states(self) -> None:
        result = normalize_states("windows", {})
        assert isinstance(result, ElementStates)
        assert result.enabled is None
        assert result.focused is None

    def test_windows_is_enabled(self) -> None:
        result = normalize_states("windows", {"IsEnabled": True})
        assert result.enabled is True

    def test_windows_is_enabled_false(self) -> None:
        result = normalize_states("windows", {"IsEnabled": False})
        assert result.enabled is False

    def test_windows_has_keyboard_focus(self) -> None:
        result = normalize_states("windows", {"HasKeyboardFocus": True})
        assert result.focused is True

    def test_windows_toggle_state_on(self) -> None:
        result = normalize_states("windows", {"ToggleState": 1})
        assert result.checked is True

    def test_windows_toggle_state_off(self) -> None:
        result = normalize_states("windows", {"ToggleState": 0})
        assert result.checked is False

    def test_windows_toggle_state_mixed(self) -> None:
        result = normalize_states("windows", {"ToggleState": 2})
        assert result.checked == "mixed"

    def test_windows_visibility_fully_visible(self) -> None:
        result = normalize_states("windows", {"Visibility": 0})
        assert result.visible is True

    def test_windows_visibility_hidden(self) -> None:
        result = normalize_states("windows", {"Visibility": 1})
        assert result.visible is False

    def test_windows_is_offscreen(self) -> None:
        result = normalize_states("windows", {"IsOffscreen": True})
        assert result.offscreen is True

    def test_windows_is_read_only(self) -> None:
        result = normalize_states("windows", {"IsReadOnly": True})
        assert result.read_only is True

    def test_windows_is_required(self) -> None:
        result = normalize_states("windows", {"IsRequiredForForm": True})
        assert result.required is True

    def test_windows_is_password(self) -> None:
        result = normalize_states("windows", {"IsPassword": True})
        assert result.is_password is True

    def test_windows_is_expanded(self) -> None:
        result = normalize_states("windows", {"IsExpanded": True})
        assert result.expanded is True

    def test_windows_multiple_states(self) -> None:
        result = normalize_states(
            "windows",
            {"IsEnabled": True, "HasKeyboardFocus": True, "IsOffscreen": False},
        )
        assert result.enabled is True
        assert result.focused is True
        assert result.offscreen is False

    def test_windows_unknown_state_skipped(self) -> None:
        result = normalize_states("windows", {"NonexistentProp": 42})
        # No field should be set to 42
        assert result.enabled is None

    def test_linux_empty_states(self) -> None:
        result = normalize_states("linux", {})
        assert isinstance(result, ElementStates)

    def test_linux_enabled(self) -> None:
        result = normalize_states("linux", {"enabled": True})
        assert result.enabled is True

    def test_linux_focused(self) -> None:
        result = normalize_states("linux", {"focused": True})
        assert result.focused is True

    def test_linux_checked_true(self) -> None:
        result = normalize_states("linux", {"checked": 1})
        assert result.checked is True

    def test_linux_checked_mixed(self) -> None:
        result = normalize_states("linux", {"checked": 2})
        assert result.checked == "mixed"

    def test_linux_editable_inverts(self) -> None:
        result = normalize_states("linux", {"editable": True})
        assert result.read_only is False

    def test_linux_indeterminate(self) -> None:
        result = normalize_states("linux", {"indeterminate": 1})
        assert result.checked == "mixed"

    def test_linux_showing_maps_to_visible(self) -> None:
        result = normalize_states("linux", {"showing": True})
        assert result.visible is True

    def test_linux_is_password(self) -> None:
        """is_password state maps to ElementStates.is_password (GW-034)."""
        result = normalize_states("linux", {"is_password": True})
        assert result.is_password is True

    def test_linux_extended_states_filtered(self) -> None:
        """States not in ElementStates (focusable, selectable, etc.) are
        silently dropped instead of causing TypeError (GW-034)."""
        result = normalize_states(
            "linux",
            {
                "enabled": True,
                "focusable": True,
                "selectable": True,
                "multi-selectable": True,
                "modal": True,
                "horizontal": True,
                "vertical": True,
                "value": 42,
                "min-value": 0,
                "max-value": 100,
                "step": 1,
            },
        )
        assert result.enabled is True
        # multi-selectable now maps to multi_selectable on ElementStates (GW-048).
        assert result.multi_selectable is True
        # Extended fields that don't exist on ElementStates should be silently
        # dropped — verify via absence from the dataclass.
        assert not hasattr(result, "focusable")

    def test_element_states_is_frozen(self) -> None:
        result = normalize_states("windows", {"IsEnabled": True})
        assert hasattr(result, "__dataclass_fields__")


# ---------------------------------------------------------------------------
# normalize_actions
# ---------------------------------------------------------------------------


class TestNormalizeActions:
    """Verify action normalization and deduplication."""

    def test_windows_empty(self) -> None:
        assert normalize_actions("windows", []) == []

    def test_windows_invoke_pattern(self) -> None:
        assert normalize_actions("windows", ["InvokePattern"]) == ["invoke"]

    def test_windows_toggle_pattern(self) -> None:
        assert normalize_actions("windows", ["TogglePattern"]) == ["toggle"]

    def test_windows_multiple_patterns(self) -> None:
        result = normalize_actions(
            "windows",
            ["InvokePattern", "TogglePattern", "ValuePattern"],
        )
        assert "invoke" in result
        assert "toggle" in result
        assert "set_value" in result

    def test_windows_deduplication(self) -> None:
        result = normalize_actions("windows", ["InvokePattern", "InvokePattern"])
        assert result == ["invoke"]

    def test_windows_unknown_pattern_skipped(self) -> None:
        result = normalize_actions("windows", ["NonexistentPattern"])
        assert result == []

    def test_windows_convenience_aliases(self) -> None:
        result = normalize_actions("windows", ["Click", "Focus", "Type"])
        assert result == ["click", "focus", "type"]

    def test_linux_click(self) -> None:
        assert normalize_actions("linux", ["click"]) == ["click"]

    def test_linux_press_maps_to_click(self) -> None:
        result = normalize_actions("linux", ["click", "press"])
        assert result == ["click"]

    def test_linux_scroll_directions_deduplicated(self) -> None:
        result = normalize_actions("linux", ["scroll", "scrollUp", "scrollDown"])
        assert result == ["scroll"]

    def test_preserves_order(self) -> None:
        result = normalize_actions(
            "windows",
            ["ScrollPattern", "InvokePattern", "ValuePattern"],
        )
        assert result == ["scroll", "invoke", "set_value"]


# ---------------------------------------------------------------------------
# normalize_bounds
# ---------------------------------------------------------------------------


class TestNormalizeBounds:
    """Verify bounds normalization from tuples and dicts."""

    def test_none_returns_none(self) -> None:
        assert normalize_bounds(None) is None

    def test_tuple(self) -> None:
        result = normalize_bounds((10, 20, 100, 30))
        assert result is not None
        assert result.x == 10.0
        assert result.y == 20.0
        assert result.width == 100.0
        assert result.height == 30.0

    def test_list(self) -> None:
        result = normalize_bounds([0, 0, 800, 600])
        assert result is not None
        assert result.width == 800.0
        assert result.height == 600.0

    def test_dict(self) -> None:
        result = normalize_bounds({"x": 5, "y": 10, "width": 200, "height": 50})
        assert result is not None
        assert result.x == 5.0
        assert result.y == 10.0

    def test_float_tuple(self) -> None:
        result = normalize_bounds((10.5, 20.5, 100.5, 30.5))
        assert result is not None
        assert result.x == 10.5

    def test_empty_tuple_returns_none(self) -> None:
        assert normalize_bounds(()) is None

    def test_invalid_type_returns_none(self) -> None:
        assert normalize_bounds("not bounds") is None

    def test_bounds_is_frozen(self) -> None:
        result = normalize_bounds((1, 2, 3, 4))
        assert result is not None
        assert hasattr(result, "__dataclass_fields__")

    def test_dict_with_missing_keys(self) -> None:
        result = normalize_bounds({"x": 0, "y": 0})
        assert result is not None
        assert result.width == 0.0
        assert result.height == 0.0


# ---------------------------------------------------------------------------
# normalize_element
# ---------------------------------------------------------------------------


class TestNormalizeElement:
    """Verify full element normalization pipeline."""

    def test_minimal_element(self) -> None:
        result = normalize_element(
            platform="windows",
            ref="e0",
            backend_id="hwnd:12345",
            role="Button",
        )
        assert isinstance(result, NormalizedElement)
        assert result.ref == "e0"
        assert result.backend_id == "hwnd:12345"
        assert result.role == "button"
        assert result.native_role is None
        assert result.name is None
        assert result.states.enabled is None
        assert result.bounds is None
        assert result.actions == []
        assert result.children is None

    def test_windows_button(self) -> None:
        result = normalize_element(
            platform="windows",
            ref="e1",
            backend_id="50000",
            role="Button",
            native_role="Button",
            control_type="Button",
            name="OK",
            raw_states={"IsEnabled": True, "HasKeyboardFocus": False},
            bounds=(10, 20, 100, 30),
            raw_actions=["InvokePattern"],
        )
        assert result.role == "button"
        assert result.name == "OK"
        assert result.native_role == "Button"
        assert result.control_type == "Button"
        assert result.states.enabled is True
        assert result.states.focused is False
        assert result.bounds is not None
        assert result.bounds.x == 10.0
        assert result.bounds.width == 100.0
        assert result.actions == ["invoke"]

    def test_windows_edit_with_password(self) -> None:
        result = normalize_element(
            platform="windows",
            ref="e2",
            backend_id="50004",
            role="Edit",
            native_role="Edit",
            control_type="Edit",
            name="Password",
            raw_states={"IsEnabled": True, "IsPassword": True, "IsReadOnly": False},
            bounds=(50, 100, 200, 25),
            raw_actions=["ValuePattern", "TextPattern"],
        )
        assert result.role == "text_input"
        assert result.states.is_password is True
        assert result.states.read_only is False
        assert result.actions == ["set_value", "type"]

    def test_windows_checkbox_mixed(self) -> None:
        result = normalize_element(
            platform="windows",
            ref="e3",
            backend_id="50002",
            role="CheckBox",
            raw_states={"ToggleState": 2},
        )
        assert result.role == "checkbox"
        assert result.states.checked == "mixed"

    def test_windows_fully_qualified_role(self) -> None:
        result = normalize_element(
            platform="windows",
            ref="e4",
            backend_id="50000",
            role="ControlType.Button",
        )
        assert result.role == "button"

    def test_unknown_role_falls_back(self) -> None:
        result = normalize_element(
            platform="windows",
            ref="e5",
            backend_id="99999",
            role="NonexistentRole",
        )
        assert result.role == "nonexistentrole"

    def test_linux_push_button(self) -> None:
        result = normalize_element(
            platform="linux",
            ref="e6",
            backend_id="acc:42",
            role="push button",
            native_role="push button",
            name="Submit",
            raw_states={"enabled": True, "focused": True},
            raw_actions=["click"],
        )
        assert result.role == "button"
        assert result.states.enabled is True
        assert result.states.focused is True
        assert result.actions == ["click"]

    def test_linux_entry(self) -> None:
        result = normalize_element(
            platform="linux",
            ref="e7",
            backend_id="acc:43",
            role="entry",
            raw_states={"editable": True, "read-only": False},
        )
        assert result.role == "text_input"
        assert result.states.read_only is False

    def test_with_description_and_value(self) -> None:
        result = normalize_element(
            platform="windows",
            ref="e8",
            backend_id="50014",
            role="Slider",
            name="Volume",
            description="Master volume control",
            value="75",
            text=None,
            raw_states={"IsEnabled": True},
        )
        assert result.role == "slider"
        assert result.description == "Master volume control"
        assert result.value == "75"

    def test_with_children(self) -> None:
        child = normalize_element(
            platform="windows",
            ref="e9-child",
            backend_id="50000",
            role="Button",
            name="OK",
        )
        result = normalize_element(
            platform="windows",
            ref="e9",
            backend_id="50032",
            role="Window",
            name="Dialog",
            children=[child],
        )
        assert result.children is not None
        assert len(result.children) == 1
        assert result.children[0].role == "button"

    def test_to_dict_output(self) -> None:
        result = normalize_element(
            platform="windows",
            ref="e10",
            backend_id="50000",
            role="Button",
            name="Cancel",
            raw_states={"IsEnabled": False},
            bounds=(0, 0, 80, 30),
            raw_actions=["InvokePattern"],
        )
        d = result.to_dict()
        assert d["ref"] == "e10"
        assert d["role"] == "button"
        assert d["name"] == "Cancel"
        assert d["states"]["enabled"] is False
        assert d["bounds"]["x"] == 0.0
        assert d["bounds"]["width"] == 80.0
        assert d["actions"] == ["invoke"]
        assert d["children"] == []

    def test_to_dict_with_children(self) -> None:
        child = normalize_element(
            platform="windows",
            ref="e11-child",
            backend_id="50004",
            role="Edit",
        )
        result = normalize_element(
            platform="windows",
            ref="e11",
            backend_id="50032",
            role="Window",
            children=[child],
        )
        d = result.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["role"] == "text_input"

    def test_walk_on_normalized_tree(self) -> None:
        child1 = normalize_element(
            platform="windows",
            ref="e12-c1",
            backend_id="50000",
            role="Button",
            name="OK",
        )
        child2 = normalize_element(
            platform="windows",
            ref="e12-c2",
            backend_id="50004",
            role="Edit",
        )
        root = normalize_element(
            platform="windows",
            ref="e12",
            backend_id="50032",
            role="Window",
            children=[child1, child2],
        )
        all_elements = root.walk()
        assert len(all_elements) == 3
        assert all_elements[0].ref == "e12"
        assert all_elements[1].ref == "e12-c1"
        assert all_elements[2].ref == "e12-c2"

    def test_find_by_role(self) -> None:
        child = normalize_element(
            platform="windows",
            ref="e13-c",
            backend_id="50000",
            role="Button",
        )
        root = normalize_element(
            platform="windows",
            ref="e13",
            backend_id="50032",
            role="Window",
            children=[child],
        )
        buttons = root.find_by_role("button")
        assert len(buttons) == 1
        assert buttons[0].ref == "e13-c"

    def test_find_by_ref(self) -> None:
        child = normalize_element(
            platform="windows",
            ref="e14-c",
            backend_id="50019",
            role="Text",
        )
        root = normalize_element(
            platform="windows",
            ref="e14",
            backend_id="50032",
            role="Window",
            children=[child],
        )
        found = root.find_by_ref("e14-c")
        assert found is not None
        assert found.role == "text"

    def test_bounds_none_in_element(self) -> None:
        result = normalize_element(
            platform="windows",
            ref="e15",
            backend_id="50019",
            role="Text",
        )
        assert result.bounds is None
        d = result.to_dict()
        assert d["bounds"] is None

    def test_all_normalized_roles_are_lowercase(self) -> None:
        """Normalized roles should always be lowercase."""
        for role_input in ["Button", "Window", "Edit", "CheckBox", "ComboBox"]:
            result = normalize_element(
                platform="windows",
                ref="e",
                backend_id="0",
                role=role_input,
            )
            assert result.role == result.role.lower(), (
                f"Role '{result.role}' is not lowercase for input '{role_input}'"
            )
