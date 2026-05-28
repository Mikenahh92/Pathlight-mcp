"""Tests for the cross-platform mapping tables."""

from pathlight_mcp.models.mappings import (
    ACTION_MAP,
    ROLE_MAP,
    STATE_MAP,
    resolve_action,
    resolve_role,
    resolve_state,
)

# ---------------------------------------------------------------------------
# Windows UIA role mappings
# ---------------------------------------------------------------------------


class TestWindowsRoles:
    """Windows UIA ControlType → normalized role."""

    def test_button(self) -> None:
        assert ROLE_MAP[("windows", "Button")] == "button"
        assert ROLE_MAP[("windows", "ControlType.Button")] == "button"

    def test_edit(self) -> None:
        assert ROLE_MAP[("windows", "Edit")] == "text_input"
        assert ROLE_MAP[("windows", "ControlType.Edit")] == "text_input"

    def test_checkbox(self) -> None:
        assert ROLE_MAP[("windows", "CheckBox")] == "checkbox"
        assert ROLE_MAP[("windows", "ControlType.CheckBox")] == "checkbox"

    def test_window(self) -> None:
        assert ROLE_MAP[("windows", "Window")] == "window"
        assert ROLE_MAP[("windows", "ControlType.Window")] == "window"

    def test_menu_item(self) -> None:
        assert ROLE_MAP[("windows", "MenuItem")] == "menu_item"

    def test_document(self) -> None:
        assert ROLE_MAP[("windows", "Document")] == "document"

    def test_list(self) -> None:
        assert ROLE_MAP[("windows", "List")] == "list"
        assert ROLE_MAP[("windows", "ListItem")] == "list_item"

    def test_tree(self) -> None:
        assert ROLE_MAP[("windows", "Tree")] == "tree"
        assert ROLE_MAP[("windows", "TreeItem")] == "tree_item"

    def test_tab(self) -> None:
        assert ROLE_MAP[("windows", "Tab")] == "tab"
        assert ROLE_MAP[("windows", "TabItem")] == "tab_item"

    def test_slider(self) -> None:
        assert ROLE_MAP[("windows", "Slider")] == "slider"

    def test_progress_bar(self) -> None:
        assert ROLE_MAP[("windows", "ProgressBar")] == "progress_bar"

    def test_hyperlink(self) -> None:
        assert ROLE_MAP[("windows", "Hyperlink")] == "link"

    def test_image(self) -> None:
        assert ROLE_MAP[("windows", "Image")] == "image"

    def test_custom(self) -> None:
        assert ROLE_MAP[("windows", "Custom")] == "custom"


# ---------------------------------------------------------------------------
# Linux AT-SPI role mappings
# ---------------------------------------------------------------------------


class TestLinuxRoles:
    """Linux AT-SPI role → normalized role."""

    def test_button(self) -> None:
        assert ROLE_MAP[("linux", "push button")] == "button"

    def test_checkbox(self) -> None:
        assert ROLE_MAP[("linux", "check box")] == "checkbox"

    def test_entry(self) -> None:
        assert ROLE_MAP[("linux", "entry")] == "text_input"
        assert ROLE_MAP[("linux", "editable text")] == "text_input"
        assert ROLE_MAP[("linux", "password text")] == "text_input"

    def test_radio_button(self) -> None:
        assert ROLE_MAP[("linux", "radio button")] == "radio_button"

    def test_window(self) -> None:
        assert ROLE_MAP[("linux", "window")] == "window"
        assert ROLE_MAP[("linux", "dialog")] == "dialog"

    def test_menu(self) -> None:
        assert ROLE_MAP[("linux", "menu bar")] == "menu_bar"
        assert ROLE_MAP[("linux", "menu item")] == "menu_item"

    def test_list(self) -> None:
        assert ROLE_MAP[("linux", "list")] == "list"
        assert ROLE_MAP[("linux", "list item")] == "list_item"

    def test_tree(self) -> None:
        assert ROLE_MAP[("linux", "tree")] == "tree"
        assert ROLE_MAP[("linux", "tree item")] == "tree_item"

    def test_table(self) -> None:
        assert ROLE_MAP[("linux", "table")] == "table"

    def test_tab(self) -> None:
        assert ROLE_MAP[("linux", "page tab")] == "tab_item"
        assert ROLE_MAP[("linux", "page tab list")] == "tab"

    def test_link(self) -> None:
        assert ROLE_MAP[("linux", "link")] == "link"

    def test_image(self) -> None:
        assert ROLE_MAP[("linux", "image")] == "image"

    def test_unknown(self) -> None:
        assert ROLE_MAP[("linux", "unknown")] == "custom"


# ---------------------------------------------------------------------------
# macOS mappings are out of scope
# ---------------------------------------------------------------------------


class TestNoMacOSMappings:
    """macOS mappings are not in scope for this epic."""

    def test_no_macos_roles(self) -> None:
        assert ("macos", "AXButton") not in ROLE_MAP

    def test_no_macos_states(self) -> None:
        assert ("macos", "AXEnabled") not in STATE_MAP

    def test_no_macos_actions(self) -> None:
        assert ("macos", "AXPress") not in ACTION_MAP


# ---------------------------------------------------------------------------
# Windows UIA state mappings
# ---------------------------------------------------------------------------


class TestWindowsStates:
    """Windows UIA properties → ElementStates fields."""

    def test_is_enabled(self) -> None:
        field, value = STATE_MAP[("windows", "IsEnabled")]
        assert field == "enabled"
        assert value(True) is True
        assert value(False) is False

    def test_has_keyboard_focus(self) -> None:
        field, _ = STATE_MAP[("windows", "HasKeyboardFocus")]
        assert field == "focused"

    def test_is_selected(self) -> None:
        field, _ = STATE_MAP[("windows", "IsSelected")]
        assert field == "selected"

    def test_toggle_state(self) -> None:
        field, transform = STATE_MAP[("windows", "ToggleState")]
        assert field == "checked"
        assert transform(0) is False
        assert transform(1) is True
        assert transform(2) == "mixed"

    def test_is_expanded(self) -> None:
        field, _ = STATE_MAP[("windows", "IsExpanded")]
        assert field == "expanded"

    def test_is_offscreen(self) -> None:
        field, _ = STATE_MAP[("windows", "IsOffscreen")]
        assert field == "offscreen"

    def test_is_read_only(self) -> None:
        field, _ = STATE_MAP[("windows", "IsReadOnly")]
        assert field == "read_only"

    def test_is_required(self) -> None:
        field, _ = STATE_MAP[("windows", "IsRequiredForForm")]
        assert field == "required"

    def test_visibility(self) -> None:
        field, transform = STATE_MAP[("windows", "Visibility")]
        assert field == "visible"
        assert transform(0) is True  # FullyVisible
        assert transform(1) is False  # Hidden
        assert transform(3) is True  # PartiallyVisible


# ---------------------------------------------------------------------------
# Linux AT-SPI state mappings
# ---------------------------------------------------------------------------


class TestLinuxStates:
    """Linux AT-SPI states → ElementStates fields."""

    def test_enabled(self) -> None:
        field, _ = STATE_MAP[("linux", "enabled")]
        assert field == "enabled"

    def test_focused(self) -> None:
        field, _ = STATE_MAP[("linux", "focused")]
        assert field == "focused"

    def test_selected(self) -> None:
        field, _ = STATE_MAP[("linux", "selected")]
        assert field == "selected"

    def test_checked(self) -> None:
        field, transform = STATE_MAP[("linux", "checked")]
        assert field == "checked"
        assert transform(0) is False
        assert transform(1) is True
        assert transform(2) == "mixed"

    def test_checked_with_bool_input(self) -> None:
        """_atspi_bool_or_mixed passes through Python bool values directly."""
        _, transform = STATE_MAP[("linux", "checked")]
        assert transform(True) is True
        assert transform(False) is False

    def test_indeterminate(self) -> None:
        field, transform = STATE_MAP[("linux", "indeterminate")]
        assert field == "checked"
        # With the contains()-based approach, indeterminate=True means
        # the state is present → checked="mixed".  False means absent →
        # None (skip, don't overwrite).
        assert transform(True) == "mixed"
        assert transform(1) == "mixed"
        assert transform(False) is None
        assert transform(0) is None

    def test_expanded(self) -> None:
        field, _ = STATE_MAP[("linux", "expanded")]
        assert field == "expanded"

    def test_visible_and_showing(self) -> None:
        assert STATE_MAP[("linux", "visible")][0] == "visible"
        assert STATE_MAP[("linux", "showing")][0] == "visible"

    def test_offscreen(self) -> None:
        field, _ = STATE_MAP[("linux", "offscreen")]
        assert field == "offscreen"

    def test_read_only_and_editable(self) -> None:
        assert STATE_MAP[("linux", "read-only")][0] == "read_only"
        editable_field, editable_transform = STATE_MAP[("linux", "editable")]
        assert editable_field == "read_only"
        assert editable_transform(True) is False
        assert editable_transform(False) is True

    def test_required(self) -> None:
        field, _ = STATE_MAP[("linux", "required")]
        assert field == "required"

    def test_modal(self) -> None:
        field, _ = STATE_MAP[("linux", "modal")]
        assert field == "modal"

    def test_focusable_selectable(self) -> None:
        assert STATE_MAP[("linux", "focusable")][0] == "focusable"
        assert STATE_MAP[("linux", "selectable")][0] == "selectable"


# ---------------------------------------------------------------------------
# Windows UIA action mappings
# ---------------------------------------------------------------------------


class TestWindowsActions:
    """Windows UIA patterns → normalized action string."""

    def test_invoke(self) -> None:
        assert ACTION_MAP[("windows", "InvokePattern")] == "invoke"

    def test_toggle(self) -> None:
        assert ACTION_MAP[("windows", "TogglePattern")] == "toggle"

    def test_select(self) -> None:
        assert ACTION_MAP[("windows", "SelectionItemPattern")] == "select_item"
        assert ACTION_MAP[("windows", "SelectionPattern")] == "select"

    def test_expand_collapse(self) -> None:
        assert ACTION_MAP[("windows", "ExpandCollapsePattern")] == "expand"

    def test_scroll(self) -> None:
        assert ACTION_MAP[("windows", "ScrollPattern")] == "scroll"

    def test_set_value(self) -> None:
        assert ACTION_MAP[("windows", "ValuePattern")] == "set_value"
        assert ACTION_MAP[("windows", "RangeValuePattern")] == "set_value"

    def test_type(self) -> None:
        assert ACTION_MAP[("windows", "TextPattern")] == "type"

    def test_convenience_aliases(self) -> None:
        assert ACTION_MAP[("windows", "Click")] == "click"
        assert ACTION_MAP[("windows", "Focus")] == "focus"
        assert ACTION_MAP[("windows", "Type")] == "type"
        assert ACTION_MAP[("windows", "SetValue")] == "set_value"
        assert ACTION_MAP[("windows", "Select")] == "select"
        assert ACTION_MAP[("windows", "Scroll")] == "scroll"
        assert ACTION_MAP[("windows", "Expand")] == "expand"
        assert ACTION_MAP[("windows", "Collapse")] == "collapse"
        assert ACTION_MAP[("windows", "Increment")] == "increment"
        assert ACTION_MAP[("windows", "Decrement")] == "decrement"


# ---------------------------------------------------------------------------
# Linux AT-SPI action mappings
# ---------------------------------------------------------------------------


class TestLinuxActions:
    """Linux AT-SPI actions → normalized action string."""

    def test_click(self) -> None:
        assert ACTION_MAP[("linux", "click")] == "click"
        assert ACTION_MAP[("linux", "press")] == "click"

    def test_activate(self) -> None:
        assert ACTION_MAP[("linux", "activate")] == "invoke"
        assert ACTION_MAP[("linux", "doAction")] == "invoke"

    def test_toggle(self) -> None:
        assert ACTION_MAP[("linux", "toggle")] == "toggle"

    def test_select(self) -> None:
        assert ACTION_MAP[("linux", "select")] == "select_item"
        assert ACTION_MAP[("linux", "deselect")] == "deselect_item"

    def test_scroll(self) -> None:
        assert ACTION_MAP[("linux", "scroll")] == "scroll"
        assert ACTION_MAP[("linux", "scrollUp")] == "scroll"
        assert ACTION_MAP[("linux", "scrollDown")] == "scroll"
        assert ACTION_MAP[("linux", "scrollLeft")] == "scroll"
        assert ACTION_MAP[("linux", "scrollRight")] == "scroll"

    def test_expand_collapse(self) -> None:
        assert ACTION_MAP[("linux", "expand")] == "expand"
        assert ACTION_MAP[("linux", "collapse")] == "collapse"

    def test_increment_decrement(self) -> None:
        assert ACTION_MAP[("linux", "increment")] == "increment"
        assert ACTION_MAP[("linux", "decrement")] == "decrement"

    def test_edit(self) -> None:
        assert ACTION_MAP[("linux", "edit")] == "type"
        assert ACTION_MAP[("linux", "insert")] == "type"


# ---------------------------------------------------------------------------
# Resolver helpers
# ---------------------------------------------------------------------------


class TestResolveRole:
    """Tests for the resolve_role() function."""

    def test_windows_resolve(self) -> None:
        assert resolve_role("windows", "Button") == "button"
        assert resolve_role("windows", "ControlType.Edit") == "text_input"

    def test_linux_resolve(self) -> None:
        assert resolve_role("linux", "push button") == "button"
        assert resolve_role("linux", "entry") == "text_input"

    def test_unknown_platform(self) -> None:
        assert resolve_role("freebsd", "Button") is None

    def test_unknown_role(self) -> None:
        assert resolve_role("windows", "NonExistentType") is None

    def test_case_insensitive_platform(self) -> None:
        assert resolve_role("Windows", "Button") == "button"
        assert resolve_role("LINUX", "push button") == "button"


class TestResolveState:
    """Tests for the resolve_state() function."""

    def test_windows_enabled(self) -> None:
        result = resolve_state("windows", "IsEnabled", True)
        assert result == ("enabled", True)

    def test_windows_toggle(self) -> None:
        result = resolve_state("windows", "ToggleState", 2)
        assert result == ("checked", "mixed")

    def test_linux_checked(self) -> None:
        result = resolve_state("linux", "checked", 0)
        assert result == ("checked", False)

    def test_unknown_platform(self) -> None:
        assert resolve_state("freebsd", "enabled", True) is None

    def test_unknown_state(self) -> None:
        assert resolve_state("windows", "NonExistentProp", True) is None

    def test_case_insensitive_platform(self) -> None:
        assert resolve_state("Windows", "IsEnabled", False) == ("enabled", False)
        assert resolve_state("LINUX", "enabled", True) == ("enabled", True)

    def test_state_with_no_transform(self) -> None:
        result = resolve_state("linux", "value", 42)
        assert result == ("value", 42)


class TestResolveAction:
    """Tests for the resolve_action() function."""

    def test_windows_resolve(self) -> None:
        assert resolve_action("windows", "InvokePattern") == "invoke"
        assert resolve_action("windows", "Click") == "click"

    def test_linux_resolve(self) -> None:
        assert resolve_action("linux", "click") == "click"
        assert resolve_action("linux", "toggle") == "toggle"

    def test_unknown_platform(self) -> None:
        assert resolve_action("freebsd", "click") is None

    def test_unknown_action(self) -> None:
        assert resolve_action("windows", "NonExistentPattern") is None

    def test_case_insensitive_platform(self) -> None:
        assert resolve_action("Windows", "Click") == "click"
        assert resolve_action("LINUX", "click") == "click"


# ---------------------------------------------------------------------------
# Web / CDP AX role mappings
# ---------------------------------------------------------------------------


class TestWebRoles:
    """Web / CDP AX role strings → normalized role."""

    def test_button(self) -> None:
        assert ROLE_MAP[("web", "button")] == "button"

    def test_checkbox(self) -> None:
        assert ROLE_MAP[("web", "checkbox")] == "checkbox"

    def test_combobox(self) -> None:
        assert ROLE_MAP[("web", "combobox")] == "combobox"

    def test_textbox(self) -> None:
        assert ROLE_MAP[("web", "textbox")] == "text_input"

    def test_link(self) -> None:
        assert ROLE_MAP[("web", "link")] == "link"

    def test_image(self) -> None:
        assert ROLE_MAP[("web", "img")] == "image"
        assert ROLE_MAP[("web", "image")] == "image"

    def test_list(self) -> None:
        assert ROLE_MAP[("web", "list")] == "list"
        assert ROLE_MAP[("web", "listbox")] == "list"
        assert ROLE_MAP[("web", "listitem")] == "list_item"
        assert ROLE_MAP[("web", "option")] == "list_item"

    def test_table(self) -> None:
        assert ROLE_MAP[("web", "table")] == "table"
        assert ROLE_MAP[("web", "grid")] == "table"
        assert ROLE_MAP[("web", "gridcell")] == "table_cell"
        assert ROLE_MAP[("web", "cell")] == "table_cell"
        assert ROLE_MAP[("web", "columnheader")] == "header_item"
        assert ROLE_MAP[("web", "rowheader")] == "header_item"

    def test_tab(self) -> None:
        assert ROLE_MAP[("web", "tablist")] == "tab"
        assert ROLE_MAP[("web", "tab")] == "tab_item"
        assert ROLE_MAP[("web", "tabpanel")] == "pane"

    def test_tree(self) -> None:
        assert ROLE_MAP[("web", "tree")] == "tree"
        assert ROLE_MAP[("web", "treegrid")] == "tree"
        assert ROLE_MAP[("web", "treeitem")] == "tree_item"

    def test_dialog(self) -> None:
        assert ROLE_MAP[("web", "dialog")] == "dialog"
        assert ROLE_MAP[("web", "alertdialog")] == "dialog"
        assert ROLE_MAP[("web", "alert")] == "dialog"

    def test_menu(self) -> None:
        assert ROLE_MAP[("web", "menu")] == "menu_bar"
        assert ROLE_MAP[("web", "menubar")] == "menu_bar"
        assert ROLE_MAP[("web", "menuitem")] == "menu_item"
        assert ROLE_MAP[("web", "menuitemcheckbox")] == "menu_item"
        assert ROLE_MAP[("web", "menuitemradio")] == "menu_item"

    def test_slider(self) -> None:
        assert ROLE_MAP[("web", "slider")] == "slider"

    def test_progress_bar(self) -> None:
        assert ROLE_MAP[("web", "progressbar")] == "progress_bar"

    def test_radio(self) -> None:
        assert ROLE_MAP[("web", "radio")] == "radio_button"
        assert ROLE_MAP[("web", "radiogroup")] == "group"

    def test_toolbar(self) -> None:
        assert ROLE_MAP[("web", "toolbar")] == "toolbar"

    def test_tooltip(self) -> None:
        assert ROLE_MAP[("web", "tooltip")] == "tooltip"

    def test_scrollbar(self) -> None:
        assert ROLE_MAP[("web", "scrollbar")] == "scroll_bar"

    def test_separator(self) -> None:
        assert ROLE_MAP[("web", "separator")] == "separator"

    def test_spinner(self) -> None:
        assert ROLE_MAP[("web", "spinbutton")] == "spinner"

    def test_document(self) -> None:
        assert ROLE_MAP[("web", "document")] == "document"

    def test_landmark_roles(self) -> None:
        assert ROLE_MAP[("web", "main")] == "pane"
        assert ROLE_MAP[("web", "navigation")] == "pane"
        assert ROLE_MAP[("web", "banner")] == "pane"
        assert ROLE_MAP[("web", "contentinfo")] == "pane"
        assert ROLE_MAP[("web", "region")] == "pane"

    def test_chrome_specific_roles(self) -> None:
        assert ROLE_MAP[("web", "generic")] == "pane"
        assert ROLE_MAP[("web", "staticText")] == "text"
        assert ROLE_MAP[("web", "inlineTextBox")] == "text"
        assert ROLE_MAP[("web", "webArea")] == "window"
        assert ROLE_MAP[("web", "iframe")] == "window"
        assert ROLE_MAP[("web", "titleBar")] == "title_bar"

    def test_input_types(self) -> None:
        assert ROLE_MAP[("web", "input")] == "text_input"
        assert ROLE_MAP[("web", "input#password")] == "text_input"
        assert ROLE_MAP[("web", "input#checkbox")] == "checkbox"
        assert ROLE_MAP[("web", "input#radio")] == "radio_button"
        assert ROLE_MAP[("web", "input#range")] == "slider"
        assert ROLE_MAP[("web", "input#submit")] == "button"
        assert ROLE_MAP[("web", "input#file")] == "button"

    def test_hidden_roles(self) -> None:
        assert ROLE_MAP[("web", "none")] == "custom"
        assert ROLE_MAP[("web", "presentation")] == "custom"

    def test_status(self) -> None:
        assert ROLE_MAP[("web", "status")] == "status_bar"

    def test_application(self) -> None:
        assert ROLE_MAP[("web", "application")] == "window"


# ---------------------------------------------------------------------------
# Web / CDP AX state mappings
# ---------------------------------------------------------------------------


class TestWebStates:
    """Web / CDP AX state properties → ElementStates fields."""

    def test_disabled_maps_to_enabled(self) -> None:
        field, transform = STATE_MAP[("web", "disabled")]
        assert field == "enabled"
        assert transform(True) is False
        assert transform(False) is True

    def test_focused(self) -> None:
        field, _ = STATE_MAP[("web", "focused")]
        assert field == "focused"

    def test_selected(self) -> None:
        field, _ = STATE_MAP[("web", "selected")]
        assert field == "selected"

    def test_checked_string_values(self) -> None:
        field, transform = STATE_MAP[("web", "checked")]
        assert field == "checked"
        assert transform("true") is True
        assert transform("false") is False
        assert transform("mixed") == "mixed"

    def test_checked_bool_values(self) -> None:
        _, transform = STATE_MAP[("web", "checked")]
        assert transform(True) is True
        assert transform(False) is False

    def test_expanded(self) -> None:
        field, _ = STATE_MAP[("web", "expanded")]
        assert field == "expanded"

    def test_readonly(self) -> None:
        field, _ = STATE_MAP[("web", "readonly")]
        assert field == "read_only"

    def test_required(self) -> None:
        field, _ = STATE_MAP[("web", "required")]
        assert field == "required"

    def test_visible(self) -> None:
        field, _ = STATE_MAP[("web", "visible")]
        assert field == "visible"

    def test_offscreen(self) -> None:
        field, _ = STATE_MAP[("web", "offscreen")]
        assert field == "offscreen"

    def test_focusable(self) -> None:
        field, _ = STATE_MAP[("web", "focusable")]
        assert field == "focusable"

    def test_editable(self) -> None:
        field, transform = STATE_MAP[("web", "editable")]
        assert field == "read_only"
        assert transform(True) is False
        assert transform(False) is True

    def test_modal(self) -> None:
        field, _ = STATE_MAP[("web", "modal")]
        assert field == "modal"

    def test_multiselectable(self) -> None:
        field, _ = STATE_MAP[("web", "multiselectable")]
        assert field == "multi_selectable"

    def test_is_password(self) -> None:
        field, _ = STATE_MAP[("web", "is_password")]
        assert field == "is_password"

    def test_aria_checked(self) -> None:
        field, transform = STATE_MAP[("web", "aria-checked")]
        assert field == "checked"
        assert transform("true") is True
        assert transform("false") is False
        assert transform("mixed") == "mixed"

    def test_aria_disabled(self) -> None:
        field, transform = STATE_MAP[("web", "aria-disabled")]
        assert field == "enabled"
        assert transform(True) is False
        assert transform(False) is True

    def test_aria_expanded(self) -> None:
        field, _ = STATE_MAP[("web", "aria-expanded")]
        assert field == "expanded"

    def test_aria_selected(self) -> None:
        field, _ = STATE_MAP[("web", "aria-selected")]
        assert field == "selected"

    def test_aria_readonly(self) -> None:
        field, _ = STATE_MAP[("web", "aria-readonly")]
        assert field == "read_only"

    def test_aria_required(self) -> None:
        field, _ = STATE_MAP[("web", "aria-required")]
        assert field == "required"

    def test_aria_modal(self) -> None:
        field, _ = STATE_MAP[("web", "aria-modal")]
        assert field == "modal"

    def test_aria_hidden(self) -> None:
        field, transform = STATE_MAP[("web", "aria-hidden")]
        assert field == "visible"
        assert transform(True) is False
        assert transform(False) is True

    def test_aria_multiselectable(self) -> None:
        field, _ = STATE_MAP[("web", "aria-multiselectable")]
        assert field == "multi_selectable"

    def test_hidden(self) -> None:
        field, transform = STATE_MAP[("web", "hidden")]
        assert field == "visible"
        assert transform(True) is False
        assert transform(False) is True


# ---------------------------------------------------------------------------
# Web / CDP AX action mappings
# ---------------------------------------------------------------------------


class TestWebActions:
    """Web / CDP AX actions → normalized action string."""

    def test_click(self) -> None:
        assert ACTION_MAP[("web", "click")] == "click"
        assert ACTION_MAP[("web", "press")] == "click"

    def test_invoke(self) -> None:
        assert ACTION_MAP[("web", "invoke")] == "invoke"

    def test_toggle(self) -> None:
        assert ACTION_MAP[("web", "toggle")] == "toggle"

    def test_select(self) -> None:
        assert ACTION_MAP[("web", "select")] == "select_item"
        assert ACTION_MAP[("web", "deselect")] == "deselect_item"

    def test_add_to_selection(self) -> None:
        assert ACTION_MAP[("web", "add_to_selection")] == "add_to_selection"
        assert ACTION_MAP[("web", "extend_selection")] == "add_to_selection"

    def test_expand_collapse(self) -> None:
        assert ACTION_MAP[("web", "expand")] == "expand"
        assert ACTION_MAP[("web", "collapse")] == "collapse"

    def test_set_value(self) -> None:
        assert ACTION_MAP[("web", "set_value")] == "set_value"

    def test_increment_decrement(self) -> None:
        assert ACTION_MAP[("web", "increment")] == "increment"
        assert ACTION_MAP[("web", "decrement")] == "decrement"

    def test_type(self) -> None:
        assert ACTION_MAP[("web", "type")] == "type"

    def test_focus(self) -> None:
        assert ACTION_MAP[("web", "focus")] == "focus"

    def test_scroll(self) -> None:
        assert ACTION_MAP[("web", "scroll")] == "scroll"
        assert ACTION_MAP[("web", "scroll_up")] == "scroll"
        assert ACTION_MAP[("web", "scroll_down")] == "scroll"
        assert ACTION_MAP[("web", "scroll_left")] == "scroll"
        assert ACTION_MAP[("web", "scroll_right")] == "scroll"


# ---------------------------------------------------------------------------
# Web resolver helper tests
# ---------------------------------------------------------------------------


class TestWebResolveRole:
    """Tests for resolve_role() with web platform."""

    def test_web_resolve_button(self) -> None:
        assert resolve_role("web", "button") == "button"

    def test_web_resolve_textbox(self) -> None:
        assert resolve_role("web", "textbox") == "text_input"

    def test_web_resolve_checkbox(self) -> None:
        assert resolve_role("web", "checkbox") == "checkbox"

    def test_web_resolve_web_area(self) -> None:
        assert resolve_role("web", "webArea") == "window"

    def test_web_resolve_unknown(self) -> None:
        assert resolve_role("web", "nonexistentRole") is None

    def test_web_case_insensitive_platform(self) -> None:
        assert resolve_role("Web", "button") == "button"
        assert resolve_role("WEB", "link") == "link"


class TestWebResolveState:
    """Tests for resolve_state() with web platform."""

    def test_web_checked(self) -> None:
        result = resolve_state("web", "checked", "true")
        assert result == ("checked", True)

    def test_web_checked_mixed(self) -> None:
        result = resolve_state("web", "checked", "mixed")
        assert result == ("checked", "mixed")

    def test_web_disabled(self) -> None:
        result = resolve_state("web", "disabled", True)
        assert result == ("enabled", False)

    def test_web_expanded(self) -> None:
        result = resolve_state("web", "expanded", True)
        assert result == ("expanded", True)

    def test_web_unknown_state(self) -> None:
        assert resolve_state("web", "nonexistent", True) is None

    def test_web_case_insensitive_platform(self) -> None:
        assert resolve_state("Web", "disabled", True) == ("enabled", False)
        assert resolve_state("WEB", "focused", True) == ("focused", True)


class TestWebResolveAction:
    """Tests for resolve_action() with web platform."""

    def test_web_resolve_click(self) -> None:
        assert resolve_action("web", "click") == "click"

    def test_web_resolve_toggle(self) -> None:
        assert resolve_action("web", "toggle") == "toggle"

    def test_web_resolve_unknown(self) -> None:
        assert resolve_action("web", "nonexistentAction") is None

    def test_web_case_insensitive_platform(self) -> None:
        assert resolve_action("Web", "click") == "click"
        assert resolve_action("WEB", "toggle") == "toggle"
