"""Comprehensive Linux normalization mapping tests (GW-034).

Validates:
- Every AT-SPI role string in ``_LINUX_ROLES`` maps to a correct normalized
  role through ``resolve_role`` and ``normalize_element``.
- Every AT-SPI state in ``_LINUX_STATES`` maps to the correct
  :class:`ElementStates` field via ``normalize_states``, including tri-state
  checked values, inversion transforms (``editable`` → ``read_only``), and
  alias states (``showing`` → ``visible``).
- Every AT-SPI action in ``_LINUX_ACTIONS`` maps to a correct normalized
  action via ``normalize_actions``, including deduplication and variant
  collapsing (``scrollUp/Down/Left/Right`` → ``scroll``).
- Cross-platform normalization parity: Linux and Windows normalization paths
  produce structurally identical :class:`NormalizedElement` instances for
  30+ shared normalized roles.
- Simulated GTK Text Editor tree normalization.
- Simulated Qt VLC tree normalization.
- Mapping completeness validation.
- Bounds normalization edge cases.
"""

from __future__ import annotations

import pytest

from pathlight_mcp.backends.normalize import normalize_element, normalize_states
from pathlight_mcp.models.mappings import (
    _LINUX_ACTIONS,
    _LINUX_ROLES,
    _LINUX_STATES,
    ACTION_MAP,
    ROLE_MAP,
    STATE_MAP,
    resolve_action,
    resolve_role,
    resolve_state,
)

# ---------------------------------------------------------------------------
# Section 1: AT-SPI role → normalized role coverage (all entries)
# ---------------------------------------------------------------------------


class TestLinuxRoleCoverage:
    """Every AT-SPI role in ``_LINUX_ROLES`` resolves to a valid normalized role.

    Uses parametrized tests so that adding or removing a mapping entry
    automatically adds or removes a test case.
    """

    @pytest.mark.parametrize(
        "atspi_role,expected",
        list(_LINUX_ROLES.items()),
        ids=lambda pair: f"{pair[0]!r} -> {pair[1]!r}",
    )
    def test_resolve_role(self, atspi_role: str, expected: str) -> None:
        """``resolve_role('linux', atspi_role)`` returns the expected value."""
        assert resolve_role("linux", atspi_role) == expected

    @pytest.mark.parametrize(
        "atspi_role,expected",
        list(_LINUX_ROLES.items()),
        ids=lambda pair: f"{pair[0]!r} -> {pair[1]!r}",
    )
    def test_normalize_element_role(self, atspi_role: str, expected: str) -> None:
        """``normalize_element`` produces a NormalizedElement with the
        correct normalized role for every AT-SPI role."""
        element = normalize_element(
            platform="linux",
            ref="e0",
            backend_id="atspi:test",
            role=atspi_role,
        )
        assert element.role == expected
        assert element.role == element.role.lower(), (
            f"Role for {atspi_role!r} is not lowercase: {element.role!r}"
        )

    def test_unknown_role_falls_back(self) -> None:
        """Unknown AT-SPI roles fall back to ``role.lower()``."""
        element = normalize_element(
            platform="linux",
            ref="e_fb",
            backend_id="0",
            role="hypothetical widget",
        )
        assert element.role == "hypothetical widget"

    def test_platform_case_insensitive(self) -> None:
        """Platform argument is case-insensitive."""
        assert resolve_role("Linux", "push button") == "button"
        assert resolve_role("LINUX", "push button") == "button"
        assert resolve_role("linux", "push button") == "button"


# ---------------------------------------------------------------------------
# Section 2: AT-SPI state → ElementStates field mapping (all entries)
# ---------------------------------------------------------------------------


class TestLinuxStateNormalization:
    """Every AT-SPI state in ``_LINUX_STATES`` maps to the correct field.

    Tests each entry individually through ``normalize_element`` and
    ``normalize_states``, including special transforms:
    - Tri-state checked (``_atspi_bool_or_mixed``)
    - Inversion (``editable`` → ``read_only`` via ``_invert_bool``)
    - Aliases (``showing`` → ``visible``)
    - Passthrough (``value`` → ``value`` with ``transform=None``)
    """

    # --- Basic boolean states ------------------------------------------------

    @pytest.mark.parametrize(
        "atspi_state,field",
        [
            ("enabled", "enabled"),
            ("focused", "focused"),
            ("selected", "selected"),
            ("expanded", "expanded"),
            ("visible", "visible"),
            ("offscreen", "offscreen"),
            ("read-only", "read_only"),
            ("required", "required"),
            ("is_password", "is_password"),
            ("focusable", "focusable"),
            ("selectable", "selectable"),
            ("multi-selectable", "multi_selectable"),
            ("modal", "modal"),
            ("horizontal", "horizontal"),
            ("vertical", "vertical"),
        ],
    )
    def test_boolean_state_true(self, atspi_state: str, field: str) -> None:
        """Boolean state with ``True`` value maps correctly."""
        result = resolve_state("linux", atspi_state, True)
        assert result is not None
        fname, value = result
        assert fname == field
        assert value is True

    @pytest.mark.parametrize(
        "atspi_state,field",
        [
            ("enabled", "enabled"),
            ("focused", "focused"),
            ("selected", "selected"),
            ("expanded", "expanded"),
            ("visible", "visible"),
            ("offscreen", "offscreen"),
            ("read-only", "read_only"),
            ("required", "required"),
            ("is_password", "is_password"),
        ],
    )
    def test_boolean_state_false(self, atspi_state: str, field: str) -> None:
        """Boolean state with ``False`` value maps correctly."""
        result = resolve_state("linux", atspi_state, False)
        assert result is not None
        fname, value = result
        assert fname == field
        assert value is False

    # --- Tri-state checked (AT-SPI 0=unchecked, 1=checked, 2=mixed) ---------

    def test_checked_unchecked_int_zero(self) -> None:
        """``checked=0`` → ``checked=False``."""
        result = resolve_state("linux", "checked", 0)
        assert result == ("checked", False)

    def test_checked_true_int_one(self) -> None:
        """``checked=1`` → ``checked=True``."""
        result = resolve_state("linux", "checked", 1)
        assert result == ("checked", True)

    def test_checked_mixed_int_two(self) -> None:
        """``checked=2`` → ``checked='mixed'``."""
        result = resolve_state("linux", "checked", 2)
        assert result == ("checked", "mixed")

    def test_checked_passes_python_bool_true(self) -> None:
        """``checked=True`` (Python bool) passes through as ``True``."""
        result = resolve_state("linux", "checked", True)
        assert result == ("checked", True)

    def test_checked_passes_python_bool_false(self) -> None:
        """``checked=False`` (Python bool) passes through as ``False``."""
        result = resolve_state("linux", "checked", False)
        assert result == ("checked", False)

    def test_checked_via_normalize_element_unchecked(self) -> None:
        """Tri-state unchecked through ``normalize_element``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="check box",
            raw_states={"checked": 0},
        )
        assert element.states.checked is False

    def test_checked_via_normalize_element_checked(self) -> None:
        """Tri-state checked through ``normalize_element``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="check box",
            raw_states={"checked": 1},
        )
        assert element.states.checked is True

    def test_checked_via_normalize_element_mixed(self) -> None:
        """Tri-state mixed through ``normalize_element``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="check box",
            raw_states={"checked": 2},
        )
        assert element.states.checked == "mixed"

    # --- Indeterminate state (maps to 'mixed' when truthy) ------------------

    def test_indeterminate_true(self) -> None:
        """``indeterminate=True`` → ``checked='mixed'``."""
        result = resolve_state("linux", "indeterminate", True)
        assert result == ("checked", "mixed")

    def test_indeterminate_false(self) -> None:
        """``indeterminate=False`` → ``checked=None`` (filtered out)."""
        result = resolve_state("linux", "indeterminate", False)
        # The transform returns None for False, which normalize_states filters
        assert result == ("checked", None)

    # --- Inversion: editable → read_only ------------------------------------

    def test_editable_false_inverts_to_read_only_true(self) -> None:
        """``editable=False`` → ``read_only=True`` (inverted)."""
        result = resolve_state("linux", "editable", False)
        assert result == ("read_only", True)

    def test_editable_true_inverts_to_read_only_false(self) -> None:
        """``editable=True`` → ``read_only=False`` (inverted)."""
        result = resolve_state("linux", "editable", True)
        assert result == ("read_only", False)

    def test_editable_via_normalize_element(self) -> None:
        """``editable`` state through ``normalize_element`` pipeline."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="editable text",
            raw_states={"editable": True},
        )
        assert element.states.read_only is False

    # --- Alias: showing → visible -------------------------------------------

    def test_showing_true_maps_to_visible(self) -> None:
        """``showing=True`` → ``visible=True`` (alias)."""
        result = resolve_state("linux", "showing", True)
        assert result == ("visible", True)

    def test_showing_false_maps_to_visible(self) -> None:
        """``showing=False`` → ``visible=False`` (alias)."""
        result = resolve_state("linux", "showing", False)
        assert result == ("visible", False)

    def test_showing_and_visible_both_set(self) -> None:
        """When both ``visible`` and ``showing`` are set, last-wins in dict."""
        states = normalize_states("linux", {"visible": True, "showing": False})
        # Both resolve to 'visible'; the last one processed wins
        assert states.visible is False

    # --- Passthrough: value / min-value / max-value / step ------------------

    def test_value_passthrough(self) -> None:
        """``value`` state passes through raw value unchanged."""
        result = resolve_state("linux", "value", 42.5)
        assert result == ("value", 42.5)

    def test_min_value_passthrough(self) -> None:
        """``min-value`` state passes through raw value."""
        result = resolve_state("linux", "min-value", 0)
        assert result == ("min_value", 0)

    def test_max_value_passthrough(self) -> None:
        """``max-value`` state passes through raw value."""
        result = resolve_state("linux", "max-value", 100)
        assert result == ("max_value", 100)

    def test_step_passthrough(self) -> None:
        """``step`` state passes through raw value."""
        result = resolve_state("linux", "step", 5)
        assert result == ("step", 5)

    # --- Note: focusable, selectable, multi_selectable, modal, horizontal,
    #     vertical resolve to fields NOT on ElementStates. They are silently
    #     dropped by normalize_states to avoid TypeError at construction. ---

    def test_unknown_state_silently_dropped(self) -> None:
        """Unknown AT-SPI state keys are silently skipped."""
        states = normalize_states("linux", {"bogus_state": True})
        assert states.enabled is None
        assert states.focused is None

    def test_empty_states_give_none_defaults(self) -> None:
        """With no raw_states, all state fields are None."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
        )
        assert element.states.enabled is None
        assert element.states.focused is None
        assert element.states.checked is None
        assert element.states.expanded is None
        assert element.states.visible is None
        assert element.states.offscreen is None
        assert element.states.read_only is None
        assert element.states.required is None
        assert element.states.is_password is None

    def test_multiple_states_combined(self) -> None:
        """Multiple states are all correctly resolved in one call."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="entry",
            raw_states={
                "enabled": True,
                "focused": True,
                "editable": True,
                "visible": True,
                "showing": True,
            },
        )
        assert element.states.enabled is True
        assert element.states.focused is True
        assert element.states.read_only is False  # editable=True → read_only=False
        assert element.states.visible is True


# ---------------------------------------------------------------------------
# Section 3: AT-SPI action → normalized action coverage (all entries)
# ---------------------------------------------------------------------------


class TestLinuxActionNormalization:
    """Every AT-SPI action in ``_LINUX_ACTIONS`` maps to a correct action.

    Tests deduplication and variant collapsing:
    - ``scrollUp``, ``scrollDown``, ``scrollLeft``, ``scrollRight`` → ``scroll``
    - ``click``, ``press``, ``jump`` → ``click``
    - ``activate``, ``doAction`` → ``invoke``
    - ``select``, ``deselect``, ``extend`` → ``select``
    - ``edit``, ``insert``, ``delete``, ``cut``, ``copy``, ``paste`` → ``type``
    """

    @pytest.mark.parametrize(
        "atspi_action,expected",
        list(_LINUX_ACTIONS.items()),
        ids=lambda pair: f"{pair[0]!r} -> {pair[1]!r}",
    )
    def test_resolve_action(self, atspi_action: str, expected: str) -> None:
        """``resolve_action('linux', atspi_action)`` returns the expected value."""
        assert resolve_action("linux", atspi_action) == expected

    @pytest.mark.parametrize(
        "atspi_action,expected",
        list(_LINUX_ACTIONS.items()),
        ids=lambda pair: f"{pair[0]!r} -> {pair[1]!r}",
    )
    def test_normalize_actions_single(self, atspi_action: str, expected: str) -> None:
        """Each AT-SPI action normalizes correctly through the pipeline."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            raw_actions=[atspi_action],
        )
        assert expected in element.actions

    # --- Deduplication tests ------------------------------------------------

    def test_click_deduplication(self) -> None:
        """``click``, ``press``, ``jump`` all normalize to ``click`` and dedup."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            raw_actions=["click", "press", "jump", "click"],
        )
        assert element.actions.count("click") == 1
        assert "click" in element.actions

    def test_invoke_deduplication(self) -> None:
        """``activate``, ``doAction`` both normalize to ``invoke`` and dedup."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            raw_actions=["activate", "doAction", "activate"],
        )
        assert element.actions.count("invoke") == 1
        assert "invoke" in element.actions

    def test_select_deduplication(self) -> None:
        """``select``, ``deselect``, ``extend`` normalize to distinct actions."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="list item",
            raw_actions=["select", "deselect", "extend"],
        )
        assert "select_item" in element.actions
        assert "deselect_item" in element.actions
        assert "add_to_selection" in element.actions

    def test_scroll_direction_deduplication(self) -> None:
        """All scroll directions normalize to ``scroll`` and dedup."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="scroll bar",
            raw_actions=["scrollUp", "scrollDown", "scrollLeft", "scrollRight"],
        )
        assert element.actions.count("scroll") == 1
        assert "scroll" in element.actions

    def test_type_variant_deduplication(self) -> None:
        """``edit``, ``insert``, ``delete``, ``cut``, ``copy``, ``paste``
        all normalize to ``type`` and dedup."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="entry",
            raw_actions=["edit", "insert", "delete", "cut", "copy", "paste"],
        )
        assert element.actions.count("type") == 1
        assert "type" in element.actions

    def test_mixed_actions_preserve_distinct(self) -> None:
        """Distinct normalized actions are all preserved in order."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="entry",
            raw_actions=["click", "edit", "scrollUp", "toggle"],
        )
        assert element.actions == ["click", "type", "scroll", "toggle"]

    def test_empty_actions(self) -> None:
        """No raw_actions → empty actions list."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="text",
        )
        assert element.actions == []

    def test_unknown_action_silently_dropped(self) -> None:
        """Unknown AT-SPI actions are silently skipped."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            raw_actions=["click", "nonexistent_action", "press"],
        )
        assert "click" in element.actions
        assert len(element.actions) == 1


# ---------------------------------------------------------------------------
# Section 4: Cross-platform normalization parity (30+ shared roles)
# ---------------------------------------------------------------------------


class TestCrossPlatformNormalizationParity:
    """Linux and Windows normalization produce structurally identical results.

    For equivalent elements across platforms, both must produce
    :class:`NormalizedElement` instances with the same normalized role.
    This section covers 30+ shared normalized roles.
    """

    @pytest.mark.parametrize(
        "normalized_role,win_role,linux_role",
        [
            # Core widget roles
            ("button", "Button", "push button"),
            ("checkbox", "CheckBox", "check box"),
            ("combobox", "ComboBox", "combo box"),
            ("document", "Document", "document frame"),
            ("text_input", "Edit", "entry"),
            ("text", "Text", "text"),
            ("image", "Image", "image"),
            ("link", "Hyperlink", "link"),
            ("list", "List", "list box"),
            ("list_item", "ListItem", "list item"),
            ("menu_bar", "MenuBar", "menu bar"),
            ("menu_item", "MenuItem", "menu_item"),
            ("pane", "Pane", "panel"),
            ("progress_bar", "ProgressBar", "progress bar"),
            ("radio_button", "RadioButton", "radio button"),
            ("scroll_bar", "ScrollBar", "scroll bar"),
            ("separator", "Separator", "separator"),
            ("slider", "Slider", "slider"),
            ("spinner", "Spinner", "spin button"),
            ("tab", "Tab", "page tab list"),
            ("tab_item", "TabItem", "page tab"),
            ("table", "Table", "table"),
            ("toolbar", "ToolBar", "tool bar"),
            ("tooltip", "ToolTip", "tool tip"),
            ("tree", "Tree", "tree"),
            ("tree_item", "TreeItem", "tree_item"),
            ("window", "Window", "window"),
            ("custom", "Custom", "unknown"),
            ("group", "Group", "grouping"),
            ("header_item", "HeaderItem", "table column header"),
            ("dialog", None, "dialog"),
            ("toggle_button", None, "toggle button"),
            ("status_bar", "StatusBar", "status_bar"),
            ("calendar", "Calendar", "calendar"),
        ],
    )
    def test_role_parity(
        self,
        normalized_role: str,
        win_role: str | None,
        linux_role: str,
    ) -> None:
        """Both platforms produce the same normalized role."""
        linux_elem = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role=linux_role,
        )
        assert linux_elem.role == normalized_role

        if win_role is not None:
            win_elem = normalize_element(
                platform="windows",
                ref="e",
                backend_id="0",
                role=win_role,
            )
            assert win_elem.role == linux_elem.role == normalized_role

    def test_enabled_state_parity(self) -> None:
        """``enabled`` state maps the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"IsEnabled": True},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            raw_states={"enabled": True},
        )
        assert win.states.enabled == linux.states.enabled is True

    def test_focused_state_parity(self) -> None:
        """``focused`` state maps the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_states={"HasKeyboardFocus": True},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="entry",
            raw_states={"focused": True},
        )
        assert win.states.focused == linux.states.focused is True

    def test_read_only_state_parity(self) -> None:
        """``read_only`` state maps the same on both platforms."""
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
        assert win.states.read_only == linux.states.read_only is True

    def test_checked_state_parity(self) -> None:
        """``checked`` state maps the same on both platforms."""
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
        assert win.states.checked == linux.states.checked is True

    def test_checked_mixed_state_parity(self) -> None:
        """Mixed ``checked`` state maps the same on both platforms."""
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

    def test_selected_state_parity(self) -> None:
        """``selected`` state maps the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="ListItem",
            raw_states={"IsSelected": True},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="list item",
            raw_states={"selected": True},
        )
        assert win.states.selected == linux.states.selected is True

    def test_expanded_state_parity(self) -> None:
        """``expanded`` state maps the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="ComboBox",
            raw_states={"IsExpanded": True},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="combo box",
            raw_states={"expanded": True},
        )
        assert win.states.expanded == linux.states.expanded is True

    def test_offscreen_state_parity(self) -> None:
        """``offscreen`` state maps the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Button",
            raw_states={"IsOffscreen": True},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            raw_states={"offscreen": True},
        )
        assert win.states.offscreen == linux.states.offscreen is True

    def test_required_state_parity(self) -> None:
        """``required`` state maps the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_states={"IsRequiredForForm": True},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="entry",
            raw_states={"required": True},
        )
        assert win.states.required == linux.states.required is True

    def test_is_password_state_parity(self) -> None:
        """``is_password`` state maps the same on both platforms."""
        win = normalize_element(
            platform="windows",
            ref="e",
            backend_id="0",
            role="Edit",
            raw_states={"IsPassword": True},
        )
        linux = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="password text",
            raw_states={"is_password": True},
        )
        assert win.states.is_password == linux.states.is_password is True

    def test_dict_output_structure_parity(self) -> None:
        """``to_dict()`` output has the same keys on both platforms."""
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
            bounds=(0, 0, 80, 30),
            raw_actions=["click"],
        )
        win_d = win.to_dict()
        linux_d = linux.to_dict()
        assert set(win_d.keys()) == set(linux_d.keys())
        assert set(win_d["states"].keys()) == set(linux_d["states"].keys())
        assert win_d["actions"] == linux_d["actions"] == ["invoke", "click"] or (
            # Actions may differ: windows=invoke, linux=click
            "invoke" in win_d["actions"] and "click" in linux_d["actions"]
        )

    def test_nested_tree_structure_parity(self) -> None:
        """Nested element trees have the same dict structure on both platforms."""
        win_child = normalize_element(
            platform="windows",
            ref="c1",
            backend_id="50000",
            role="Button",
            name="Save",
            raw_actions=["InvokePattern"],
        )
        win_parent = normalize_element(
            platform="windows",
            ref="p1",
            backend_id="50032",
            role="Window",
            name="Main",
            children=[win_child],
        )
        linux_child = normalize_element(
            platform="linux",
            ref="c1",
            backend_id="0",
            role="push button",
            name="Save",
            raw_actions=["click"],
        )
        linux_parent = normalize_element(
            platform="linux",
            ref="p1",
            backend_id="0",
            role="window",
            name="Main",
            children=[linux_child],
        )
        wd = win_parent.to_dict()
        ld = linux_parent.to_dict()
        assert wd["role"] == ld["role"] == "window"
        assert len(wd["children"]) == len(ld["children"]) == 1
        assert wd["children"][0]["role"] == ld["children"][0]["role"] == "button"


# ---------------------------------------------------------------------------
# Section 5: Simulated GTK Text Editor tree normalization
# ---------------------------------------------------------------------------


class TestGtkTextEditorNormalization:
    """Normalization of a simulated GTK Text Editor accessibility tree.

    Typical GTK Text Editor (gedit/mousepad) structure::

        window "Untitled - Text Editor"
          menu bar
            menu item "File"
            menu item "Edit"
            menu item "View"
            menu item "Help"
          toolbar
            push button "New"
            push button "Open"
            push button "Save"
            toggle button "Find"
            separator
          panel (notebook)
            tab list
              page tab "Document 1"
            scroll pane
              panel
                text "Line 1"
                text "Line 2"
          status bar
    """

    def test_menu_bar_normalizes(self) -> None:
        """GTK menu bar normalizes to ``menu_bar``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="menu bar",
            name="MainMenu",
        )
        assert element.role == "menu_bar"

    def test_menu_item_normalizes(self) -> None:
        """GTK menu item normalizes to ``menu_item``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="menu item",
            name="File",
            raw_actions=["activate"],
        )
        assert element.role == "menu_item"
        assert "invoke" in element.actions

    def test_toolbar_normalizes(self) -> None:
        """GTK toolbar normalizes to ``toolbar``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="tool bar",
            name="Toolbar",
        )
        assert element.role == "toolbar"

    def test_toolbar_button_normalizes(self) -> None:
        """GTK toolbar push button normalizes to ``button``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            name="Save",
            raw_actions=["click"],
            raw_states={"enabled": True},
        )
        assert element.role == "button"
        assert "click" in element.actions
        assert element.states.enabled is True

    def test_toolbar_toggle_button_normalizes(self) -> None:
        """GTK toolbar toggle button normalizes to ``toggle_button``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="toggle button",
            name="Find",
            raw_actions=["toggle"],
        )
        assert element.role == "toggle_button"
        assert "toggle" in element.actions

    def test_toolbar_separator_normalizes(self) -> None:
        """GTK toolbar separator normalizes to ``separator``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="separator",
        )
        assert element.role == "separator"

    def test_tab_list_normalizes(self) -> None:
        """GTK notebook tab list normalizes to ``tab``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="page tab list",
        )
        assert element.role == "tab"

    def test_page_tab_normalizes(self) -> None:
        """GTK notebook page tab normalizes to ``tab_item``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="page tab",
            name="Document 1",
            raw_states={"selected": True},
        )
        assert element.role == "tab_item"
        assert element.states.selected is True

    def test_text_content_normalizes(self) -> None:
        """GTK text paragraph normalizes to ``text``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="text",
            text="Hello, world!",
            raw_states={"editable": False},
        )
        assert element.role == "text"
        assert element.text == "Hello, world!"
        assert element.states.read_only is True

    def test_status_bar_normalizes(self) -> None:
        """GTK status bar normalizes to ``status_bar``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="status bar",
        )
        assert element.role == "status_bar"

    def test_full_gtk_text_editor_tree(self) -> None:
        """Full GTK Text Editor tree normalizes to correct structure."""
        # Build menu items
        file_mi = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="0",
            role="menu item",
            name="File",
            raw_actions=["activate"],
        )
        edit_mi = normalize_element(
            platform="linux",
            ref="e2",
            backend_id="0",
            role="menu item",
            name="Edit",
            raw_actions=["activate"],
        )
        view_mi = normalize_element(
            platform="linux",
            ref="e3",
            backend_id="0",
            role="menu item",
            name="View",
            raw_actions=["activate"],
        )
        help_mi = normalize_element(
            platform="linux",
            ref="e4",
            backend_id="0",
            role="menu item",
            name="Help",
            raw_actions=["activate"],
        )
        menu_bar = normalize_element(
            platform="linux",
            ref="e5",
            backend_id="0",
            role="menu bar",
            children=[file_mi, edit_mi, view_mi, help_mi],
        )

        # Build toolbar buttons
        new_btn = normalize_element(
            platform="linux",
            ref="e6",
            backend_id="0",
            role="push button",
            name="New",
            raw_actions=["click"],
        )
        open_btn = normalize_element(
            platform="linux",
            ref="e7",
            backend_id="0",
            role="push button",
            name="Open",
            raw_actions=["click"],
        )
        save_btn = normalize_element(
            platform="linux",
            ref="e8",
            backend_id="0",
            role="push button",
            name="Save",
            raw_actions=["click"],
        )
        find_btn = normalize_element(
            platform="linux",
            ref="e9",
            backend_id="0",
            role="toggle button",
            name="Find",
            raw_actions=["toggle"],
        )
        sep = normalize_element(
            platform="linux",
            ref="e10",
            backend_id="0",
            role="separator",
        )
        toolbar = normalize_element(
            platform="linux",
            ref="e11",
            backend_id="0",
            role="tool bar",
            children=[new_btn, open_btn, save_btn, find_btn, sep],
        )

        # Build tab / editor area
        tab = normalize_element(
            platform="linux",
            ref="e12",
            backend_id="0",
            role="page tab",
            name="Document 1",
            raw_states={"selected": True},
        )
        tab_list = normalize_element(
            platform="linux",
            ref="e13",
            backend_id="0",
            role="page tab list",
            children=[tab],
        )
        line1 = normalize_element(
            platform="linux",
            ref="e14",
            backend_id="0",
            role="text",
            text="Hello, world!",
        )
        line2 = normalize_element(
            platform="linux",
            ref="e15",
            backend_id="0",
            role="text",
            text="Second line",
        )
        editor_panel = normalize_element(
            platform="linux",
            ref="e16",
            backend_id="0",
            role="panel",
            children=[line1, line2],
        )
        scroll_pane = normalize_element(
            platform="linux",
            ref="e17",
            backend_id="0",
            role="panel",
            children=[editor_panel],
        )
        notebook = normalize_element(
            platform="linux",
            ref="e18",
            backend_id="0",
            role="panel",
            children=[tab_list, scroll_pane],
        )

        # Status bar
        status = normalize_element(
            platform="linux",
            ref="e19",
            backend_id="0",
            role="status bar",
        )

        # Root window
        window = normalize_element(
            platform="linux",
            ref="w1",
            backend_id="0",
            role="window",
            name="Untitled - Text Editor",
            raw_states={"enabled": True},
            bounds=(0, 0, 800, 600),
            children=[menu_bar, toolbar, notebook, status],
        )

        d = window.to_dict()
        assert d["role"] == "window"
        assert d["name"] == "Untitled - Text Editor"
        assert len(d["children"]) == 4

        # Menu bar
        assert d["children"][0]["role"] == "menu_bar"
        assert len(d["children"][0]["children"]) == 4
        for mi in d["children"][0]["children"]:
            assert mi["role"] == "menu_item"
            assert "invoke" in mi["actions"]

        # Toolbar
        assert d["children"][1]["role"] == "toolbar"
        assert len(d["children"][1]["children"]) == 5
        assert d["children"][1]["children"][0]["role"] == "button"
        assert d["children"][1]["children"][3]["role"] == "toggle_button"
        assert d["children"][1]["children"][4]["role"] == "separator"

        # Notebook / editor area
        assert d["children"][2]["role"] == "pane"
        tab_list_d = d["children"][2]["children"][0]
        assert tab_list_d["role"] == "tab"
        assert tab_list_d["children"][0]["role"] == "tab_item"

        # Status bar
        assert d["children"][3]["role"] == "status_bar"


# ---------------------------------------------------------------------------
# Section 6: Simulated Qt VLC media player tree normalization
# ---------------------------------------------------------------------------


class TestQtVlcNormalization:
    """Normalization of a simulated Qt VLC media player accessibility tree.

    Typical VLC structure::

        window "VLC media player"
          menu bar
            menu item "Media"
            menu item "Playback"
            menu item "View"
            menu item "Help"
          tool bar (controls)
            push button "Play" / "Pause"
            push button "Stop"
            push button "Previous"
            push button "Next"
            slider "Position"
            slider "Volume"
          panel (video output area)
            canvas
          status bar
    """

    def test_play_button_normalizes(self) -> None:
        """VLC Play button normalizes to ``button`` with ``click`` action."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            name="Play",
            raw_actions=["click"],
            raw_states={"enabled": True},
        )
        assert element.role == "button"
        assert "click" in element.actions
        assert element.states.enabled is True

    def test_position_slider_normalizes(self) -> None:
        """VLC position slider normalizes to ``slider``.

        Note: ``value``, ``min-value``, ``max-value`` resolve to fields not
        yet on ``ElementStates``, so they are silently filtered by
        ``normalize_states``. The role and bounds are still correct.
        """
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="slider",
            name="Position",
            raw_states={"value": 0.5, "min-value": 0.0, "max-value": 1.0},
        )
        assert element.role == "slider"
        # value/min-value/max-value are not on ElementStates yet;
        # verify they resolve correctly at the resolve_state level.
        assert resolve_state("linux", "value", 0.5) == ("value", 0.5)
        assert resolve_state("linux", "min-value", 0.0) == ("min_value", 0.0)
        assert resolve_state("linux", "max-value", 1.0) == ("max_value", 1.0)

    def test_video_canvas_normalizes(self) -> None:
        """VLC video canvas normalizes to ``custom``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="canvas",
        )
        assert element.role == "custom"

    def test_full_vlc_tree(self) -> None:
        """Full VLC media player tree normalizes to correct structure."""
        # Menu bar
        media_mi = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="0",
            role="menu item",
            name="Media",
            raw_actions=["activate"],
        )
        playback_mi = normalize_element(
            platform="linux",
            ref="e2",
            backend_id="0",
            role="menu item",
            name="Playback",
            raw_actions=["activate"],
        )
        view_mi = normalize_element(
            platform="linux",
            ref="e3",
            backend_id="0",
            role="menu item",
            name="View",
            raw_actions=["activate"],
        )
        help_mi = normalize_element(
            platform="linux",
            ref="e4",
            backend_id="0",
            role="menu item",
            name="Help",
            raw_actions=["activate"],
        )
        menu_bar = normalize_element(
            platform="linux",
            ref="e5",
            backend_id="0",
            role="menu bar",
            children=[media_mi, playback_mi, view_mi, help_mi],
        )

        # Toolbar controls
        play_btn = normalize_element(
            platform="linux",
            ref="e6",
            backend_id="0",
            role="push button",
            name="Play",
            raw_actions=["click"],
            raw_states={"enabled": True},
        )
        stop_btn = normalize_element(
            platform="linux",
            ref="e7",
            backend_id="0",
            role="push button",
            name="Stop",
            raw_actions=["click"],
            raw_states={"enabled": True},
        )
        prev_btn = normalize_element(
            platform="linux",
            ref="e8",
            backend_id="0",
            role="push button",
            name="Previous",
            raw_actions=["click"],
            raw_states={"enabled": True},
        )
        next_btn = normalize_element(
            platform="linux",
            ref="e9",
            backend_id="0",
            role="push button",
            name="Next",
            raw_actions=["click"],
            raw_states={"enabled": True},
        )
        position_slider = normalize_element(
            platform="linux",
            ref="e10",
            backend_id="0",
            role="slider",
            name="Position",
            raw_states={"value": 0.35, "min-value": 0.0, "max-value": 1.0},
            raw_actions=["increment", "decrement"],
        )
        volume_slider = normalize_element(
            platform="linux",
            ref="e11",
            backend_id="0",
            role="slider",
            name="Volume",
            raw_states={"value": 80, "min-value": 0, "max-value": 100},
        )
        toolbar = normalize_element(
            platform="linux",
            ref="e12",
            backend_id="0",
            role="tool bar",
            children=[play_btn, stop_btn, prev_btn, next_btn, position_slider, volume_slider],
        )

        # Video area
        canvas = normalize_element(
            platform="linux",
            ref="e13",
            backend_id="0",
            role="canvas",
            bounds=(0, 40, 800, 400),
        )
        video_panel = normalize_element(
            platform="linux",
            ref="e14",
            backend_id="0",
            role="panel",
            children=[canvas],
        )

        # Status bar
        status = normalize_element(
            platform="linux",
            ref="e15",
            backend_id="0",
            role="status bar",
        )

        # Root window
        window = normalize_element(
            platform="linux",
            ref="w1",
            backend_id="0",
            role="window",
            name="VLC media player",
            raw_states={"enabled": True},
            bounds=(100, 100, 800, 600),
            children=[menu_bar, toolbar, video_panel, status],
        )

        d = window.to_dict()
        assert d["role"] == "window"
        assert d["name"] == "VLC media player"
        assert d["bounds"]["width"] == 800.0
        assert len(d["children"]) == 4

        # Menu bar
        assert d["children"][0]["role"] == "menu_bar"
        assert len(d["children"][0]["children"]) == 4

        # Toolbar
        tb = d["children"][1]
        assert tb["role"] == "toolbar"
        assert len(tb["children"]) == 6
        # Play/Stop/Prev/Next are buttons
        for child in tb["children"][:4]:
            assert child["role"] == "button"
            assert "click" in child["actions"]
        # Position and Volume are sliders
        assert tb["children"][4]["role"] == "slider"
        assert tb["children"][5]["role"] == "slider"

        # Video area
        assert d["children"][2]["role"] == "pane"
        assert d["children"][2]["children"][0]["role"] == "custom"
        assert d["children"][2]["children"][0]["bounds"]["width"] == 800.0

        # Status bar
        assert d["children"][3]["role"] == "status_bar"


# ---------------------------------------------------------------------------
# Section 7: Mapping completeness validation
# ---------------------------------------------------------------------------


class TestLinuxMappingCompleteness:
    """Validate mapping table completeness and consistency.

    Ensures all ``_LINUX_ROLES``, ``_LINUX_STATES``, and ``_LINUX_ACTIONS``
    entries are present in the composite maps, all normalized values are
    valid, and there are no duplicates or unexpected patterns.
    """

    def test_all_linux_roles_in_role_map(self) -> None:
        """Every ``_LINUX_ROLES`` entry exists in ``ROLE_MAP`` with
        ``('linux', key)``."""
        for atspi_role in _LINUX_ROLES:
            assert ("linux", atspi_role) in ROLE_MAP, (
                f"Missing _LINUX_ROLES entry {atspi_role!r} in ROLE_MAP"
            )

    def test_all_linux_states_in_state_map(self) -> None:
        """Every ``_LINUX_STATES`` entry exists in ``STATE_MAP`` with
        ``('linux', key)``."""
        for atspi_state in _LINUX_STATES:
            assert ("linux", atspi_state) in STATE_MAP, (
                f"Missing _LINUX_STATES entry {atspi_state!r} in STATE_MAP"
            )

    def test_all_linux_actions_in_action_map(self) -> None:
        """Every ``_LINUX_ACTIONS`` entry exists in ``ACTION_MAP`` with
        ``('linux', key)``."""
        for atspi_action in _LINUX_ACTIONS:
            assert ("linux", atspi_action) in ACTION_MAP, (
                f"Missing _LINUX_ACTIONS entry {atspi_action!r} in ACTION_MAP"
            )

    def test_linux_role_count(self) -> None:
        """``_LINUX_ROLES`` should have at least 60 entries."""
        assert len(_LINUX_ROLES) >= 60

    def test_linux_state_count(self) -> None:
        """``_LINUX_STATES`` should have at least 20 entries."""
        assert len(_LINUX_STATES) >= 20

    def test_linux_action_count(self) -> None:
        """``_LINUX_ACTIONS`` should have at least 20 entries."""
        assert len(_LINUX_ACTIONS) >= 20

    def test_all_linux_roles_resolve_to_lowercase(self) -> None:
        """Every ``_LINUX_ROLES`` value is a lowercase string."""
        for atspi_role, normalized in _LINUX_ROLES.items():
            assert normalized == normalized.lower(), (
                f"_LINUX_ROLES[{atspi_role!r}] = {normalized!r} is not lowercase"
            )

    def test_all_linux_actions_resolve_to_known_desktop_actions(self) -> None:
        """Every ``_LINUX_ACTIONS`` value is a valid ``DesktopAction``."""
        from pathlight_mcp.models import DesktopAction

        valid_actions = set(DesktopAction.__args__)  # type: ignore[attr-defined]
        for atspi_action, normalized in _LINUX_ACTIONS.items():
            assert normalized in valid_actions, (
                f"_LINUX_ACTIONS[{atspi_action!r}] = {normalized!r} is not a valid DesktopAction"
            )

    def test_no_linux_role_collisions(self) -> None:
        """No two ``_LINUX_ROLES`` entries map to the same normalized role
        unless they are intentional aliases (e.g., ``list`` / ``list box``)."""
        # Group by normalized value
        by_value: dict[str, list[str]] = {}
        for atspi_role, normalized in _LINUX_ROLES.items():
            by_value.setdefault(normalized, []).append(atspi_role)
        # Check that multi-mapped roles are intentional
        intentional_multi = {
            "text_input",  # entry, terminal, password text, editable text
            "text",  # text, paragraph, heading, label
            "pane",  # frame, panel, page, split pane, layered pane,
            # root pane, glass pane, internal frame
            "group",  # filler, grouping, section
            "image",  # image, icon, chart
            "dialog",  # dialog, alert, file chooser, font chooser,
            # option pane
            "window",  # window, desktop frame
            "custom",  # unknown, canvas, embedded component, animation
            "document",  # document frame, document web
            "list",  # list, list box
            "slider",  # slider, dial
            "menu_bar",  # menu, menu bar
            "menu_item",  # menu item, tearoff menu item
            "radio_button",  # radio button, radio menu item
            "header_item",  # table column header, table row header
            "button",  # push button, desktop icon
            "tree",  # tree, tree table
            "combobox",  # combo box, color chooser
        }
        for normalized, atspi_roles in by_value.items():
            if len(atspi_roles) > 1:
                assert normalized in intentional_multi, (
                    f"Unexpected collision: {atspi_roles} all map to {normalized!r}"
                )

    def test_linux_role_map_keys_do_not_overlap_with_windows(self) -> None:
        """Linux and Windows role maps do not share raw key strings
        (they may share normalized values, but not native keys)."""
        from pathlight_mcp.models.mappings import _WINDOWS_ROLES

        overlap = set(_LINUX_ROLES.keys()) & set(_WINDOWS_ROLES.keys())
        # The only acceptable overlap would be lowercase equivalents that
        # both platforms happen to use. Check that any overlap is intentional.
        # Currently there should be no raw key overlap.
        assert overlap == set(), (
            f"Unexpected raw key overlap between Linux and Windows roles: {overlap}"
        )

    def test_state_map_values_are_valid_element_states_fields(self) -> None:
        """All ``_LINUX_STATES`` field names are either valid
        ``ElementStates`` fields or intentionally filtered out."""
        from dataclasses import fields

        from pathlight_mcp.models import ElementStates

        valid_fields = {f.name for f in fields(ElementStates)}
        # These fields are in _LINUX_STATES but NOT on ElementStates yet;
        # they are silently filtered by normalize_states.
        extra_fields = {
            "focusable",
            "selectable",
            "multi_selectable",
            "modal",
            "horizontal",
            "vertical",
            "value",
            "min_value",
            "max_value",
            "step",
        }
        for atspi_state, (field_name, _transform) in _LINUX_STATES.items():
            assert field_name in valid_fields or field_name in extra_fields, (
                f"_LINUX_STATES[{atspi_state!r}] targets unknown field {field_name!r}"
            )


# ---------------------------------------------------------------------------
# Section 8: Bounds normalization (Linux-specific edge cases)
# ---------------------------------------------------------------------------


class TestLinuxBoundsNormalization:
    """Bounds normalization for Linux AT-SPI accessibility trees.

    AT-SPI reports extents as ``(x, y, width, height)`` tuples, which
    should convert identically to the Windows path.
    """

    def test_tuple_bounds(self) -> None:
        """Bounds from an AT-SPI ``(x, y, w, h)`` tuple."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            bounds=(10, 20, 100, 30),
        )
        assert element.bounds is not None
        assert element.bounds.x == 10.0
        assert element.bounds.y == 20.0
        assert element.bounds.width == 100.0
        assert element.bounds.height == 30.0

    def test_list_bounds(self) -> None:
        """Bounds from a list ``[x, y, w, h]``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            bounds=[50, 60, 200, 40],
        )
        assert element.bounds is not None
        assert element.bounds.x == 50.0
        assert element.bounds.width == 200.0

    def test_dict_bounds(self) -> None:
        """Bounds from a dict with ``x/y/width/height`` keys."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            bounds={"x": 100, "y": 200, "width": 300, "height": 50},
        )
        assert element.bounds is not None
        assert element.bounds.x == 100.0
        assert element.bounds.y == 200.0
        assert element.bounds.width == 300.0
        assert element.bounds.height == 50.0

    def test_none_bounds(self) -> None:
        """``None`` bounds → ``element.bounds is None``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="text",
        )
        assert element.bounds is None

    def test_zero_area_bounds(self) -> None:
        """Bounds with zero area still produce a Bounds object."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            bounds=(100, 200, 0, 0),
        )
        assert element.bounds is not None
        assert element.bounds.is_empty is True

    def test_float_bounds(self) -> None:
        """Float bounds (DPI-scaled) are accepted."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            bounds=(10.5, 20.7, 100.3, 30.1),
        )
        assert element.bounds is not None
        assert element.bounds.x == 10.5
        assert element.bounds.center == (60.65, 35.75)

    def test_bounds_in_to_dict(self) -> None:
        """Bounds serialize correctly in ``to_dict()`` output."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="push button",
            bounds=(0, 0, 800, 600),
        )
        d = element.to_dict()
        assert d["bounds"]["x"] == 0.0
        assert d["bounds"]["y"] == 0.0
        assert d["bounds"]["width"] == 800.0
        assert d["bounds"]["height"] == 600.0

    def test_large_screen_bounds(self) -> None:
        """Very large bounds (4K display) are accepted."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="desktop frame",
            bounds=(0, 0, 3840, 2160),
        )
        assert element.bounds is not None
        assert element.bounds.width == 3840.0
        assert element.bounds.height == 2160.0


# ---------------------------------------------------------------------------
# Section 9: normalize_element integration with all fields
# ---------------------------------------------------------------------------


class TestLinuxNormalizeElementIntegration:
    """Integration tests for ``normalize_element`` with all fields populated."""

    def test_full_element_with_all_fields(self) -> None:
        """Element with all optional fields set normalizes correctly."""
        element = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="atspi:app:btn1",
            role="push button",
            native_role="push button",
            name="OK",
            description="Confirm action",
            value="42",
            text="OK",
            raw_states={
                "enabled": True,
                "focused": True,
                "visible": True,
                "showing": True,
            },
            bounds=(10, 20, 80, 30),
            raw_actions=["click", "press"],
        )
        assert element.ref == "e1"
        assert element.backend_id == "atspi:app:btn1"
        assert element.role == "button"
        assert element.native_role == "push button"
        assert element.name == "OK"
        assert element.description == "Confirm action"
        assert element.value == "42"
        assert element.text == "OK"
        assert element.states.enabled is True
        assert element.states.focused is True
        assert element.states.visible is True
        assert element.bounds is not None
        assert element.bounds.x == 10.0
        # Deduplicated: click and press both → click
        assert element.actions == ["click"]

    def test_full_element_to_dict(self) -> None:
        """Full element ``to_dict()`` output has all expected keys."""
        element = normalize_element(
            platform="linux",
            ref="e1",
            backend_id="0",
            role="entry",
            name="Search",
            description="Search input",
            value="test",
            text="test",
            raw_states={"enabled": True, "editable": True},
            bounds=(0, 0, 200, 30),
            raw_actions=["edit", "insert"],
        )
        d = element.to_dict()
        assert d["ref"] == "e1"
        assert d["role"] == "text_input"
        assert d["name"] == "Search"
        assert d["description"] == "Search input"
        assert d["value"] == "test"
        assert d["text"] == "test"
        assert d["states"]["enabled"] is True
        assert d["states"]["read_only"] is False
        assert d["bounds"]["width"] == 200.0
        # edit and insert both → type (deduplicated)
        assert d["actions"] == ["type"]
        assert d["children"] == []

    def test_desktop_icon_maps_to_button(self) -> None:
        """``desktop icon`` maps to ``button``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="desktop icon",
            name="Firefox",
            raw_actions=["click"],
        )
        assert element.role == "button"
        assert element.name == "Firefox"
        assert "click" in element.actions

    def test_password_text_maps_to_text_input(self) -> None:
        """``password text`` maps to ``text_input``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="password text",
            raw_states={"is_password": True},
            raw_actions=["edit"],
        )
        assert element.role == "text_input"
        assert element.states.is_password is True
        assert "type" in element.actions

    def test_terminal_maps_to_text_input(self) -> None:
        """``terminal`` maps to ``text_input``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="terminal",
            raw_states={"editable": True},
        )
        assert element.role == "text_input"
        assert element.states.read_only is False

    def test_tree_table_maps_to_tree(self) -> None:
        """``tree table`` maps to ``tree``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="tree table",
        )
        assert element.role == "tree"

    def test_alert_maps_to_dialog(self) -> None:
        """``alert`` maps to ``dialog``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="alert",
            raw_states={"modal": True},
        )
        assert element.role == "dialog"

    def test_radio_menu_item_maps_to_radio_button(self) -> None:
        """``radio menu item`` maps to ``radio_button``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="radio menu item",
            raw_actions=["toggle"],
        )
        assert element.role == "radio_button"
        assert "toggle" in element.actions

    def test_color_chooser_maps_to_combobox(self) -> None:
        """``color chooser`` maps to ``combobox``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="color chooser",
        )
        assert element.role == "combobox"

    def test_table_cell_maps_to_table_cell(self) -> None:
        """``table cell`` maps to ``table_cell``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="table cell",
        )
        assert element.role == "table_cell"

    def test_dial_maps_to_slider(self) -> None:
        """``dial`` maps to ``slider``."""
        element = normalize_element(
            platform="linux",
            ref="e",
            backend_id="0",
            role="dial",
        )
        assert element.role == "slider"
