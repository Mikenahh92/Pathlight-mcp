"""Cross-platform mapping tables.

Maps platform-specific role/state/action identifiers to their normalized
forms using ``(platform, native_key)`` tuple keys for single-dict lookup.

Usage::

    from guidewire.models.mappings import ROLE_MAP, STATE_MAP, ACTION_MAP

    norm_role = ROLE_MAP[("windows", "Button")]
    assert norm_role == "button"
    norm_role = ROLE_MAP[("web", "button")]
    assert norm_role == "button"
"""

from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# Type alias for state mapping value: (field_name, optional_transform)
# ---------------------------------------------------------------------------

StateMapping = tuple[str, Callable[[Any], Any] | None]

# ---------------------------------------------------------------------------
# Value transform helpers
# ---------------------------------------------------------------------------


def _uia_toggle_to_checked(value: Any) -> bool | str:
    """Convert UIA ToggleState (0=Off, 1=On, 2=Indeterminate) to checked."""
    mapping = {0: False, 1: True, 2: "mixed"}
    return mapping.get(int(value), False)


def _uia_visibility_to_visible(value: Any) -> bool:
    """Convert UIA Visibility enum to visible boolean."""
    # UIA: FullyVisible=0, Hidden=1, Collapsed=2, PartiallyVisible=3, Offscreen=4
    return int(value) in (0, 3)


def _invert_bool(value: Any) -> bool:
    """Invert a boolean value."""
    return not bool(value)


def _atspi_bool_or_mixed(value: Any) -> bool | str:
    """Convert AT-SPI checked state (0=unchecked, 1=checked, 2=mixed)."""
    if isinstance(value, bool):
        return value
    mapping = {0: False, 1: True, 2: "mixed"}
    return mapping.get(int(value), bool(value))


def _aria_checked_to_checked(value: Any) -> bool | str:
    """Convert ARIA ``aria-checked`` / CDP ``checked`` property value.

    CDP AX nodes report ``checked`` as a string:
    ``"false"`` → ``False``, ``"true"`` → ``True``, ``"mixed"`` → ``"mixed"``.
    Boolean and mixed values are also handled.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        mapping = {"false": False, "true": True, "mixed": "mixed"}
        return mapping.get(value.lower(), False)
    return bool(value)


def _aria_disabled_to_enabled(value: Any) -> bool:
    """Convert ARIA ``disabled`` / CDP ``disabled`` to ``enabled`` (inverted)."""
    return not bool(value)


# ---------------------------------------------------------------------------
# Windows UIA ControlType strings  →  normalized role
# ---------------------------------------------------------------------------

_WINDOWS_ROLES: dict[str, str] = {
    "Button": "button",
    "Calendar": "calendar",
    "CheckBox": "checkbox",
    "ComboBox": "combobox",
    "DataGrid": "datagrid",
    "DataItem": "data_item",
    "Document": "document",
    "Edit": "text_input",
    "Group": "group",
    "Header": "header",
    "HeaderItem": "header_item",
    "Hyperlink": "link",
    "Image": "image",
    "List": "list",
    "ListItem": "list_item",
    "MenuBar": "menu_bar",
    "MenuItem": "menu_item",
    "Pane": "pane",
    "ProgressBar": "progress_bar",
    "RadioButton": "radio_button",
    "ScrollBar": "scroll_bar",
    "Separator": "separator",
    "Slider": "slider",
    "Spinner": "spinner",
    "SplitButton": "split_button",
    "StatusBar": "status_bar",
    "Tab": "tab",
    "TabItem": "tab_item",
    "Table": "table",
    "Text": "text",
    "Thumb": "thumb",
    "TitleBar": "title_bar",
    "ToolBar": "toolbar",
    "ToolTip": "tooltip",
    "Tree": "tree",
    "TreeItem": "tree_item",
    "Window": "window",
    "Custom": "custom",
    # Fully-qualified aliases (UIA ControlType enum strings)
    "ControlType.Button": "button",
    "ControlType.Calendar": "calendar",
    "ControlType.CheckBox": "checkbox",
    "ControlType.ComboBox": "combobox",
    "ControlType.Edit": "text_input",
    "ControlType.List": "list",
    "ControlType.ListItem": "list_item",
    "ControlType.MenuItem": "menu_item",
    "ControlType.RadioButton": "radio_button",
    "ControlType.Tab": "tab",
    "ControlType.TabItem": "tab_item",
    "ControlType.Text": "text",
    "ControlType.Window": "window",
    "ControlType.Document": "document",
    "ControlType.Image": "image",
    "ControlType.Hyperlink": "link",
    "ControlType.ScrollBar": "scroll_bar",
    "ControlType.Slider": "slider",
    "ControlType.ProgressBar": "progress_bar",
    "ControlType.Spinner": "spinner",
    "ControlType.ToolTip": "tooltip",
    "ControlType.Tree": "tree",
    "ControlType.TreeItem": "tree_item",
    "ControlType.Table": "table",
    "ControlType.DataGrid": "datagrid",
    "ControlType.DataItem": "data_item",
    "ControlType.Group": "group",
    "ControlType.Pane": "pane",
    "ControlType.Header": "header",
    "ControlType.HeaderItem": "header_item",
    "ControlType.Separator": "separator",
    "ControlType.ToolBar": "toolbar",
    "ControlType.StatusBar": "status_bar",
    "ControlType.TitleBar": "title_bar",
    "ControlType.MenuBar": "menu_bar",
    "ControlType.SplitButton": "split_button",
    "ControlType.Thumb": "thumb",
    "ControlType.Custom": "custom",
}

# ---------------------------------------------------------------------------
# Linux AT-SPI role strings  →  normalized role
# ---------------------------------------------------------------------------

_LINUX_ROLES: dict[str, str] = {
    "push button": "button",
    "check box": "checkbox",
    "combo box": "combobox",
    "document frame": "document",
    "document web": "document",
    "entry": "text_input",
    "text": "text",
    "terminal": "text_input",
    "paragraph": "text",
    "heading": "text",
    "filler": "group",
    "frame": "pane",
    "grouping": "group",
    "image": "image",
    "label": "text",
    "link": "link",
    "list": "list",
    "list box": "list",
    "list item": "list_item",
    "menu": "menu_bar",
    "menu bar": "menu_bar",
    "menu item": "menu_item",
    "page tab": "tab_item",
    "page tab list": "tab",
    "panel": "pane",
    "progress bar": "progress_bar",
    "radio button": "radio_button",
    "radio menu item": "radio_button",
    "scroll bar": "scroll_bar",
    "section": "group",
    "separator": "separator",
    "slider": "slider",
    "spin button": "spinner",
    "split pane": "pane",
    "status bar": "status_bar",
    "table": "table",
    "table cell": "table_cell",
    "table column header": "header_item",
    "table row header": "header_item",
    "tearoff menu item": "menu_item",
    "toggle button": "toggle_button",
    "tool bar": "toolbar",
    "tool tip": "tooltip",
    "tree": "tree",
    "tree table": "tree",
    "tree item": "tree_item",
    "window": "window",
    "dialog": "dialog",
    "alert": "dialog",
    "unknown": "custom",
    "page": "pane",
    "canvas": "custom",
    "embedded component": "custom",
    "icon": "image",
    "animation": "custom",
    "chart": "image",
    "dial": "slider",
    "password text": "text_input",
    "calendar": "calendar",
    "color chooser": "combobox",
    "file chooser": "dialog",
    "font chooser": "dialog",
    "layered pane": "pane",
    "option pane": "dialog",
    "root pane": "pane",
    "glass pane": "pane",
    "internal frame": "pane",
    "desktop frame": "window",
    "desktop icon": "button",
    "editable text": "text_input",
}

# ---------------------------------------------------------------------------
# Web / CDP AX role strings  →  normalized role
#
# CDP Accessibility domain uses ARIA role names and Chrome AX role strings.
# These are the ``role.value`` field from CDP AX nodes.
# See: https://chromedevtools.github.io/devtools-protocol/tot/Accuracy/
# See: https://www.w3.org/TR/wai-aria-1.2/#role_definitions
# ---------------------------------------------------------------------------

_WEB_ROLES: dict[str, str] = {
    # ARIA landmark / structural roles
    "main": "pane",
    "navigation": "pane",
    "banner": "pane",
    "contentinfo": "pane",
    "complementary": "pane",
    "region": "pane",
    "search": "pane",
    "form": "pane",
    # ARIA widget roles
    "alert": "dialog",
    "alertdialog": "dialog",
    "button": "button",
    "checkbox": "checkbox",
    "combobox": "combobox",
    "dialog": "dialog",
    "grid": "table",
    "gridcell": "table_cell",
    "link": "link",
    "list": "list",
    "listbox": "list",
    "listitem": "list_item",
    "option": "list_item",
    "log": "pane",
    "marquee": "pane",
    "menu": "menu_bar",
    "menubar": "menu_bar",
    "menuitem": "menu_item",
    "menuitemcheckbox": "menu_item",
    "menuitemradio": "menu_item",
    "note": "pane",
    "progressbar": "progress_bar",
    "radio": "radio_button",
    "radiogroup": "group",
    "row": "list_item",
    "rowgroup": "group",
    "rowheader": "header_item",
    "scrollbar": "scroll_bar",
    "separator": "separator",
    "slider": "slider",
    "spinbutton": "spinner",
    "status": "status_bar",
    "tab": "tab_item",
    "table": "table",
    "tablist": "tab",
    "tabpanel": "pane",
    "textbox": "text_input",
    "timer": "pane",
    "toolbar": "toolbar",
    "tooltip": "tooltip",
    "tree": "tree",
    "treegrid": "tree",
    "treeitem": "tree_item",
    # ARIA document structure roles
    "application": "window",
    "article": "group",
    "cell": "table_cell",
    "columnheader": "header_item",
    "definition": "text",
    "directory": "list",
    "document": "document",
    "feed": "list",
    "figure": "group",
    "group": "group",
    "heading": "text",
    "img": "image",
    "math": "custom",
    "none": "custom",
    "presentation": "custom",
    "term": "text",
    # Chrome-specific AX role strings
    # (reported by CDP AX tree for HTML elements without explicit ARIA)
    "generic": "pane",
    "text": "text",
    "inlineTextBox": "text",
    "layoutTable": "table",
    "layoutTableCell": "table_cell",
    "layoutTableColumn": "header_item",
    "layoutTableRow": "list_item",
    "lineBreak": "separator",
    "paragraph": "text",
    "staticText": "text",
    "image": "image",
    "imageMap": "image",
    "svgRoot": "image",
    "svg": "image",
    "popupButton": "combobox",
    "details": "group",
    "summary": "button",
    "input": "text_input",
    "input#password": "text_input",
    "input#email": "text_input",
    "input#tel": "text_input",
    "input#number": "text_input",
    "input#search": "text_input",
    "input#url": "text_input",
    "input#date": "text_input",
    "input#time": "text_input",
    "input#datetime-local": "text_input",
    "input#month": "text_input",
    "input#week": "text_input",
    "input#checkbox": "checkbox",
    "input#radio": "radio_button",
    "input#range": "slider",
    "input#color": "button",
    "input#file": "button",
    "input#submit": "button",
    "input#reset": "button",
    "input#button": "button",
    "input#image": "button",
    "titleBar": "title_bar",
    "webArea": "window",
    "iframe": "window",
    "frame": "window",
}

# ---------------------------------------------------------------------------
# Composite ROLE_MAP: (platform, native_key) → normalized role
# ---------------------------------------------------------------------------

ROLE_MAP: dict[tuple[str, str], str] = {}
for _key, _val in _WINDOWS_ROLES.items():
    ROLE_MAP[("windows", _key)] = _val
for _key, _val in _LINUX_ROLES.items():
    ROLE_MAP[("linux", _key)] = _val
for _key, _val in _WEB_ROLES.items():
    ROLE_MAP[("web", _key)] = _val

# ---------------------------------------------------------------------------
# Windows UIA property / pattern state names  →  (ElementStates field, transform)
# ---------------------------------------------------------------------------

_WINDOWS_STATES: dict[str, StateMapping] = {
    "IsEnabled": ("enabled", bool),
    "HasKeyboardFocus": ("focused", bool),
    "IsSelected": ("selected", bool),
    "ToggleState": ("checked", _uia_toggle_to_checked),
    "IsExpanded": ("expanded", bool),
    "IsOffscreen": ("offscreen", bool),
    "IsReadOnly": ("read_only", bool),
    "IsRequiredForForm": ("required", bool),
    "IsPassword": ("is_password", bool),
    "Visibility": ("visible", _uia_visibility_to_visible),
    # Pattern availability states
    "IsKeyboardFocusable": ("focusable", bool),
    # Legacy / convenience aliases
    "enabled": ("enabled", bool),
    "focused": ("focused", bool),
    "selected": ("selected", bool),
    "expanded": ("expanded", bool),
    "offscreen": ("offscreen", bool),
    "readonly": ("read_only", bool),
    "required": ("required", bool),
    "visible": ("visible", bool),
}

# ---------------------------------------------------------------------------
# Linux AT-SPI state strings  →  (ElementStates field, transform)
# ---------------------------------------------------------------------------

_LINUX_STATES: dict[str, StateMapping] = {
    "enabled": ("enabled", bool),
    "focused": ("focused", bool),
    "selected": ("selected", bool),
    "checked": ("checked", _atspi_bool_or_mixed),
    "expanded": ("expanded", bool),
    "visible": ("visible", bool),
    "showing": ("visible", bool),
    "offscreen": ("offscreen", bool),
    "read-only": ("read_only", bool),
    "required": ("required", bool),
    "editable": ("read_only", _invert_bool),
    "is_password": ("is_password", bool),
    "focusable": ("focusable", bool),
    "selectable": ("selectable", bool),
    "multi-selectable": ("multi_selectable", bool),
    "modal": ("modal", bool),
    "horizontal": ("horizontal", bool),
    "vertical": ("vertical", bool),
    "indeterminate": ("checked", lambda v: "mixed" if v else None),
    # Numeric / value states
    "value": ("value", None),
    "min-value": ("min_value", None),
    "max-value": ("max_value", None),
    "step": ("step", None),
}

# ---------------------------------------------------------------------------
# Web / CDP AX state properties  →  (ElementStates field, transform)
#
# CDP AX nodes expose states as properties in the ``properties`` list.
# Each property has ``name`` and ``value`` fields.
# See: https://chromedevtools.github.io/devtools-protocol/tot/Accability/
# ---------------------------------------------------------------------------

_WEB_STATES: dict[str, StateMapping] = {
    # CDP AX property names
    "disabled": ("enabled", _aria_disabled_to_enabled),
    "focused": ("focused", bool),
    "selected": ("selected", bool),
    "checked": ("checked", _aria_checked_to_checked),
    "expanded": ("expanded", bool),
    "modal": ("modal", bool),
    "readonly": ("read_only", bool),
    "required": ("required", bool),
    "visible": ("visible", bool),
    "offscreen": ("offscreen", bool),
    "focusable": ("focusable", bool),
    "editable": ("read_only", _invert_bool),
    "multiselectable": ("multi_selectable", bool),
    # ARIA attribute names (may appear in properties)
    "aria-disabled": ("enabled", _aria_disabled_to_enabled),
    "aria-expanded": ("expanded", bool),
    "aria-checked": ("checked", _aria_checked_to_checked),
    "aria-selected": ("selected", bool),
    "aria-readonly": ("read_only", bool),
    "aria-required": ("required", bool),
    "aria-modal": ("modal", bool),
    "aria-multiselectable": ("multi_selectable", bool),
    "aria-hidden": ("visible", _invert_bool),
    # HTML attribute equivalents
    "disabled_attr": ("enabled", _aria_disabled_to_enabled),
    "hidden": ("visible", _invert_bool),
    "is_password": ("is_password", bool),
}

# ---------------------------------------------------------------------------
# Composite STATE_MAP: (platform, native_key) → (field, transform)
# ---------------------------------------------------------------------------

STATE_MAP: dict[tuple[str, str], StateMapping] = {}
for _key, _val in _WINDOWS_STATES.items():
    STATE_MAP[("windows", _key)] = _val
for _key, _val in _LINUX_STATES.items():
    STATE_MAP[("linux", _key)] = _val
for _key, _val in _WEB_STATES.items():
    STATE_MAP[("web", _key)] = _val

# ---------------------------------------------------------------------------
# Windows UIA patterns / actions  →  normalized action string
# ---------------------------------------------------------------------------

_WINDOWS_ACTIONS: dict[str, str] = {
    # Interaction patterns
    "InvokePattern": "invoke",
    "TogglePattern": "toggle",
    "SelectionItemPattern": "select_item",
    "SelectionPattern": "select",
    "ExpandCollapsePattern": "expand",
    "ScrollPattern": "scroll",
    "RangeValuePattern": "set_value",
    "ValuePattern": "set_value",
    "TextPattern": "type",
    # Legacy / convenience aliases
    "Invoke": "invoke",
    "Toggle": "toggle",
    "Click": "click",
    "Press": "click",
    "Focus": "focus",
    "Type": "type",
    "SetValue": "set_value",
    "Select": "select",
    "SelectItem": "select_item",
    "DeselectItem": "deselect_item",
    "AddToSelection": "add_to_selection",
    "Scroll": "scroll",
    "Expand": "expand",
    "Collapse": "collapse",
    "Increment": "increment",
    "Decrement": "decrement",
}

# ---------------------------------------------------------------------------
# Linux AT-SPI actions  →  normalized action string
# ---------------------------------------------------------------------------

_LINUX_ACTIONS: dict[str, str] = {
    "click": "click",
    "press": "click",
    "activate": "invoke",
    "doAction": "invoke",
    "jump": "click",
    "toggle": "toggle",
    "select": "select_item",
    "deselect": "deselect_item",
    "extend": "add_to_selection",
    "scroll": "scroll",
    "scrollUp": "scroll",
    "scrollDown": "scroll",
    "scrollLeft": "scroll",
    "scrollRight": "scroll",
    "expand": "expand",
    "collapse": "collapse",
    "increment": "increment",
    "decrement": "decrement",
    "edit": "type",
    "insert": "type",
    "delete": "type",
    "cut": "type",
    "copy": "type",
    "paste": "type",
}

# ---------------------------------------------------------------------------
# Web / CDP AX actions  →  normalized action string
#
# Web elements support actions based on their ARIA roles and HTML semantics.
# These represent the actions a web element can perform, derived from
# CDP AX node properties and ARIA role capabilities.
# ---------------------------------------------------------------------------

_WEB_ACTIONS: dict[str, str] = {
    # Click / invoke
    "click": "click",
    "invoke": "invoke",
    "press": "click",
    # Toggle (checkbox, switch)
    "toggle": "toggle",
    # Selection
    "select": "select_item",
    "deselect": "deselect_item",
    "add_to_selection": "add_to_selection",
    "extend_selection": "add_to_selection",
    # Expand / collapse
    "expand": "expand",
    "collapse": "collapse",
    # Value manipulation
    "set_value": "set_value",
    "increment": "increment",
    "decrement": "decrement",
    # Text input
    "type": "type",
    "focus": "focus",
    # Scroll
    "scroll": "scroll",
    "scroll_up": "scroll",
    "scroll_down": "scroll",
    "scroll_left": "scroll",
    "scroll_right": "scroll",
}

# ---------------------------------------------------------------------------
# Composite ACTION_MAP: (platform, native_key) → normalized action string
# ---------------------------------------------------------------------------

ACTION_MAP: dict[tuple[str, str], str] = {}
for _key, _val in _WINDOWS_ACTIONS.items():
    ACTION_MAP[("windows", _key)] = _val
for _key, _val in _LINUX_ACTIONS.items():
    ACTION_MAP[("linux", _key)] = _val
for _key, _val in _WEB_ACTIONS.items():
    ACTION_MAP[("web", _key)] = _val


# ---------------------------------------------------------------------------
# Resolver helpers
# ---------------------------------------------------------------------------


def resolve_role(platform: str, native_role: str) -> str | None:
    """Resolve a platform-specific role string to a normalized role.

    Args:
        platform: One of ``"windows"``, ``"linux"``, ``"web"``.
        native_role: The platform-specific role or control-type identifier.

    Returns:
        The normalized role string, or ``None`` if unknown.
    """
    return ROLE_MAP.get((platform.lower(), native_role))


def resolve_state(
    platform: str,
    native_state_key: str,
    native_value: Any,
) -> tuple[str, Any] | None:
    """Resolve a single platform-specific state to a normalized field/value pair.

    Args:
        platform: One of ``"windows"``, ``"linux"``, ``"web"``.
        native_state_key: The platform-specific state or property name.
        native_value: The raw value from the accessibility API.

    Returns:
        A ``(field_name, normalized_value)`` tuple, or ``None`` if the state
        key is not recognized for the given platform.
    """
    entry = STATE_MAP.get((platform.lower(), native_state_key))
    if entry is None:
        return None
    field_name, transform = entry
    normalized_value = transform(native_value) if transform is not None else native_value
    return (field_name, normalized_value)


def resolve_action(platform: str, native_action: str) -> str | None:
    """Resolve a platform-specific action string to a normalized action.

    Args:
        platform: One of ``"windows"``, ``"linux"``, ``"web"``.
        native_action: The platform-specific action or pattern identifier.

    Returns:
        The corresponding normalized action string, or ``None`` if unknown.
    """
    return ACTION_MAP.get((platform.lower(), native_action))
