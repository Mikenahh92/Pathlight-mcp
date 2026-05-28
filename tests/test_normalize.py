"""Tests for normalization functions (AC-7).

Verifies the resolver helpers in pathlight_mcp.models.mappings:
- resolve_role: platform-specific role → normalized role
- resolve_state: platform-specific state → (field, value) pair
- resolve_action: platform-specific action → normalized action
"""

from pathlight_mcp.models.mappings import (
    ACTION_MAP,
    ROLE_MAP,
    STATE_MAP,
    resolve_action,
    resolve_role,
    resolve_state,
)

# ---------------------------------------------------------------------------
# resolve_role
# ---------------------------------------------------------------------------


class TestResolveRole:
    """Verify cross-platform role normalization (resolve_role)."""

    # -- Windows UIA roles --

    def test_windows_button(self) -> None:
        assert resolve_role("windows", "Button") == "button"

    def test_windows_edit(self) -> None:
        assert resolve_role("windows", "Edit") == "text_input"

    def test_windows_window(self) -> None:
        assert resolve_role("windows", "Window") == "window"

    def test_windows_list(self) -> None:
        assert resolve_role("windows", "List") == "list"

    def test_windows_list_item(self) -> None:
        assert resolve_role("windows", "ListItem") == "list_item"

    def test_windows_combo_box(self) -> None:
        assert resolve_role("windows", "ComboBox") == "combobox"

    def test_windows_fully_qualified(self) -> None:
        """ControlType.Button format also resolves."""
        assert resolve_role("windows", "ControlType.Button") == "button"

    def test_windows_fully_qualified_edit(self) -> None:
        assert resolve_role("windows", "ControlType.Edit") == "text_input"

    def test_windows_hyperlink(self) -> None:
        assert resolve_role("windows", "Hyperlink") == "link"

    def test_windows_image(self) -> None:
        assert resolve_role("windows", "Image") == "image"

    def test_windows_separator(self) -> None:
        assert resolve_role("windows", "Separator") == "separator"

    def test_windows_slider(self) -> None:
        assert resolve_role("windows", "Slider") == "slider"

    def test_windows_custom(self) -> None:
        assert resolve_role("windows", "Custom") == "custom"

    # -- Linux AT-SPI roles --

    def test_linux_push_button(self) -> None:
        assert resolve_role("linux", "push button") == "button"

    def test_linux_entry(self) -> None:
        assert resolve_role("linux", "entry") == "text_input"

    def test_linux_password_text(self) -> None:
        assert resolve_role("linux", "password text") == "text_input"

    def test_linux_check_box(self) -> None:
        assert resolve_role("linux", "check box") == "checkbox"

    def test_linux_combo_box(self) -> None:
        assert resolve_role("linux", "combo box") == "combobox"

    def test_linux_window(self) -> None:
        assert resolve_role("linux", "window") == "window"

    def test_linux_dialog(self) -> None:
        assert resolve_role("linux", "dialog") == "dialog"

    def test_linux_alert(self) -> None:
        assert resolve_role("linux", "alert") == "dialog"

    def test_linux_list(self) -> None:
        assert resolve_role("linux", "list") == "list"

    def test_linux_list_item(self) -> None:
        assert resolve_role("linux", "list item") == "list_item"

    def test_linux_table(self) -> None:
        assert resolve_role("linux", "table") == "table"

    def test_linux_tree(self) -> None:
        assert resolve_role("linux", "tree") == "tree"

    def test_linux_tree_item(self) -> None:
        assert resolve_role("linux", "tree item") == "tree_item"

    def test_linux_radio_button(self) -> None:
        assert resolve_role("linux", "radio button") == "radio_button"

    def test_linux_toggle_button(self) -> None:
        assert resolve_role("linux", "toggle button") == "toggle_button"

    def test_linux_menu_bar(self) -> None:
        assert resolve_role("linux", "menu bar") == "menu_bar"

    def test_linux_menu_item(self) -> None:
        assert resolve_role("linux", "menu item") == "menu_item"

    def test_linux_editable_text(self) -> None:
        assert resolve_role("linux", "editable text") == "text_input"

    def test_linux_terminal(self) -> None:
        assert resolve_role("linux", "terminal") == "text_input"

    def test_linux_unknown(self) -> None:
        assert resolve_role("linux", "unknown") == "custom"

    def test_linux_canvas(self) -> None:
        assert resolve_role("linux", "canvas") == "custom"

    def test_linux_desktop_icon(self) -> None:
        assert resolve_role("linux", "desktop icon") == "button"

    def test_linux_dial(self) -> None:
        assert resolve_role("linux", "dial") == "slider"

    # -- Unknown / error cases --

    def test_unknown_role_returns_none(self) -> None:
        assert resolve_role("windows", "NonexistentRole") is None

    def test_unknown_platform_returns_none(self) -> None:
        assert resolve_role("macos", "Button") is None

    def test_case_sensitive(self) -> None:
        """Windows role lookup is case-sensitive."""
        assert resolve_role("windows", "button") is None
        assert resolve_role("windows", "Button") == "button"

    def test_platform_case_insensitive(self) -> None:
        """Platform argument is case-insensitive."""
        assert resolve_role("Windows", "Button") == "button"
        assert resolve_role("WINDOWS", "Button") == "button"
        assert resolve_role("Linux", "push button") == "button"


# ---------------------------------------------------------------------------
# resolve_state
# ---------------------------------------------------------------------------


class TestResolveState:
    """Verify cross-platform state normalization (resolve_state)."""

    # -- Windows states --

    def test_windows_is_enabled_true(self) -> None:
        assert resolve_state("windows", "IsEnabled", 1) == ("enabled", True)

    def test_windows_is_enabled_false(self) -> None:
        assert resolve_state("windows", "IsEnabled", 0) == ("enabled", False)

    def test_windows_has_keyboard_focus(self) -> None:
        assert resolve_state("windows", "HasKeyboardFocus", True) == (
            "focused",
            True,
        )

    def test_windows_is_selected(self) -> None:
        assert resolve_state("windows", "IsSelected", True) == ("selected", True)

    def test_windows_toggle_state_on(self) -> None:
        assert resolve_state("windows", "ToggleState", 1) == ("checked", True)

    def test_windows_toggle_state_off(self) -> None:
        assert resolve_state("windows", "ToggleState", 0) == ("checked", False)

    def test_windows_toggle_state_mixed(self) -> None:
        assert resolve_state("windows", "ToggleState", 2) == ("checked", "mixed")

    def test_windows_is_expanded(self) -> None:
        assert resolve_state("windows", "IsExpanded", True) == ("expanded", True)

    def test_windows_is_offscreen(self) -> None:
        assert resolve_state("windows", "IsOffscreen", True) == ("offscreen", True)

    def test_windows_is_read_only(self) -> None:
        assert resolve_state("windows", "IsReadOnly", True) == (
            "read_only",
            True,
        )

    def test_windows_is_required(self) -> None:
        assert resolve_state("windows", "IsRequiredForForm", True) == (
            "required",
            True,
        )

    def test_windows_is_password(self) -> None:
        assert resolve_state("windows", "IsPassword", True) == (
            "is_password",
            True,
        )

    def test_windows_visibility_fully_visible(self) -> None:
        assert resolve_state("windows", "Visibility", 0) == ("visible", True)

    def test_windows_visibility_hidden(self) -> None:
        assert resolve_state("windows", "Visibility", 1) == ("visible", False)

    def test_windows_visibility_partially_visible(self) -> None:
        assert resolve_state("windows", "Visibility", 3) == ("visible", True)

    # -- Linux states --

    def test_linux_enabled(self) -> None:
        assert resolve_state("linux", "enabled", True) == ("enabled", True)

    def test_linux_focused(self) -> None:
        assert resolve_state("linux", "focused", True) == ("focused", True)

    def test_linux_selected(self) -> None:
        assert resolve_state("linux", "selected", True) == ("selected", True)

    def test_linux_checked_true(self) -> None:
        assert resolve_state("linux", "checked", 1) == ("checked", True)

    def test_linux_checked_false(self) -> None:
        assert resolve_state("linux", "checked", 0) == ("checked", False)

    def test_linux_checked_mixed(self) -> None:
        assert resolve_state("linux", "checked", 2) == ("checked", "mixed")

    def test_linux_expanded(self) -> None:
        assert resolve_state("linux", "expanded", True) == ("expanded", True)

    def test_linux_visible(self) -> None:
        assert resolve_state("linux", "visible", True) == ("visible", True)

    def test_linux_showing(self) -> None:
        assert resolve_state("linux", "showing", True) == ("visible", True)

    def test_linux_offscreen(self) -> None:
        assert resolve_state("linux", "offscreen", True) == ("offscreen", True)

    def test_linux_read_only(self) -> None:
        assert resolve_state("linux", "read-only", True) == ("read_only", True)

    def test_linux_editable_inverts(self) -> None:
        """editable=true means read_only=false."""
        assert resolve_state("linux", "editable", True) == ("read_only", False)

    def test_linux_editable_false(self) -> None:
        assert resolve_state("linux", "editable", False) == ("read_only", True)

    def test_linux_indeterminate(self) -> None:
        assert resolve_state("linux", "indeterminate", True) == (
            "checked",
            "mixed",
        )

    # -- Unknown / error cases --

    def test_unknown_state_returns_none(self) -> None:
        assert resolve_state("windows", "NonexistentState", True) is None

    def test_unknown_platform_returns_none(self) -> None:
        assert resolve_state("macos", "enabled", True) is None


# ---------------------------------------------------------------------------
# resolve_action
# ---------------------------------------------------------------------------


class TestResolveAction:
    """Verify cross-platform action normalization (resolve_action)."""

    # -- Windows actions --

    def test_windows_invoke(self) -> None:
        assert resolve_action("windows", "InvokePattern") == "invoke"

    def test_windows_toggle(self) -> None:
        assert resolve_action("windows", "TogglePattern") == "toggle"

    def test_windows_select(self) -> None:
        assert resolve_action("windows", "SelectionItemPattern") == "select_item"

    def test_windows_expand(self) -> None:
        assert resolve_action("windows", "ExpandCollapsePattern") == "expand"

    def test_windows_scroll(self) -> None:
        assert resolve_action("windows", "ScrollPattern") == "scroll"

    def test_windows_set_value(self) -> None:
        assert resolve_action("windows", "ValuePattern") == "set_value"

    def test_windows_type(self) -> None:
        assert resolve_action("windows", "TextPattern") == "type"

    def test_windows_click(self) -> None:
        assert resolve_action("windows", "Click") == "click"

    def test_windows_focus(self) -> None:
        assert resolve_action("windows", "Focus") == "focus"

    def test_windows_collapse(self) -> None:
        assert resolve_action("windows", "Collapse") == "collapse"

    def test_windows_increment(self) -> None:
        assert resolve_action("windows", "Increment") == "increment"

    def test_windows_decrement(self) -> None:
        assert resolve_action("windows", "Decrement") == "decrement"

    # -- Linux actions --

    def test_linux_click(self) -> None:
        assert resolve_action("linux", "click") == "click"

    def test_linux_press(self) -> None:
        assert resolve_action("linux", "press") == "click"

    def test_linux_activate(self) -> None:
        assert resolve_action("linux", "activate") == "invoke"

    def test_linux_toggle(self) -> None:
        assert resolve_action("linux", "toggle") == "toggle"

    def test_linux_select(self) -> None:
        assert resolve_action("linux", "select") == "select_item"

    def test_linux_scroll(self) -> None:
        assert resolve_action("linux", "scroll") == "scroll"

    def test_linux_scroll_up(self) -> None:
        assert resolve_action("linux", "scrollUp") == "scroll"

    def test_linux_scroll_down(self) -> None:
        assert resolve_action("linux", "scrollDown") == "scroll"

    def test_linux_expand(self) -> None:
        assert resolve_action("linux", "expand") == "expand"

    def test_linux_collapse(self) -> None:
        assert resolve_action("linux", "collapse") == "collapse"

    def test_linux_increment(self) -> None:
        assert resolve_action("linux", "increment") == "increment"

    def test_linux_decrement(self) -> None:
        assert resolve_action("linux", "decrement") == "decrement"

    def test_linux_edit(self) -> None:
        assert resolve_action("linux", "edit") == "type"

    def test_linux_jump(self) -> None:
        assert resolve_action("linux", "jump") == "click"

    # -- Unknown / error cases --

    def test_unknown_action_returns_none(self) -> None:
        assert resolve_action("windows", "NonexistentPattern") is None

    def test_unknown_platform_returns_none(self) -> None:
        assert resolve_action("macos", "click") is None


# ---------------------------------------------------------------------------
# Mapping table integrity
# ---------------------------------------------------------------------------


class TestMappingTableIntegrity:
    """Verify mapping table structure and consistency."""

    def test_role_map_keys_are_tuples(self) -> None:
        for key in ROLE_MAP:
            assert isinstance(key, tuple)
            assert len(key) == 2

    def test_state_map_keys_are_tuples(self) -> None:
        for key in STATE_MAP:
            assert isinstance(key, tuple)
            assert len(key) == 2

    def test_action_map_keys_are_tuples(self) -> None:
        for key in ACTION_MAP:
            assert isinstance(key, tuple)
            assert len(key) == 2

    def test_role_map_has_both_platforms(self) -> None:
        platforms = {key[0] for key in ROLE_MAP}
        assert "windows" in platforms
        assert "linux" in platforms

    def test_state_map_has_both_platforms(self) -> None:
        platforms = {key[0] for key in STATE_MAP}
        assert "windows" in platforms
        assert "linux" in platforms

    def test_action_map_has_both_platforms(self) -> None:
        platforms = {key[0] for key in ACTION_MAP}
        assert "windows" in platforms
        assert "linux" in platforms

    def test_windows_role_count(self) -> None:
        windows_roles = [k for k in ROLE_MAP if k[0] == "windows"]
        assert len(windows_roles) > 30

    def test_linux_role_count(self) -> None:
        linux_roles = [k for k in ROLE_MAP if k[0] == "linux"]
        assert len(linux_roles) > 40

    def test_no_duplicate_normalized_roles_per_platform(self) -> None:
        """Each platform key should map to exactly one normalized role."""
        for platform in ("windows", "linux"):
            keys = [k[1] for k in ROLE_MAP if k[0] == platform]
            # Multiple native keys can map to same normalized role — that's fine.
            # Just verify no native key is duplicated.
            assert len(keys) == len(set(keys))

    def test_all_normalized_roles_are_lowercase(self) -> None:
        for value in ROLE_MAP.values():
            assert value == value.lower(), f"Role '{value}' is not lowercase"
