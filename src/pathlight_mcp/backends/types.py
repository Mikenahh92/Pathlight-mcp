"""Backend type definitions (architecture v2 §3).

Defines the opaque handle alias and the cross-platform data classes used
by :class:`~pathlight_mcp.backends.base.DesktopBackend` and the MCP tool layer.

- ``NativeHandle`` — opaque platform-native handle (``NewType('NativeHandle', Any)``)
- ``ElementState`` — normalized accessibility element state flags (9 booleans, §3.2)
- ``ElementBounds`` — bounding rectangle for an element
- ``DesktopAction`` — enumeration of supported desktop actions (12 variants, §4.3)
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NewType

__all__ = [
    "DesktopAction",
    "ElementBounds",
    "ElementState",
    "NativeHandle",
]

# -- Opaque handle alias ---------------------------------------------------

#: Opaque handle to a native accessibility element or window.
#: Platform backends bind this to their native types at runtime
#: (e.g. ``comtypes`` COM pointers on Windows, ``pyatspi`` objects on Linux).
NativeHandle = NewType("NativeHandle", Any)


# -- Cross-platform data classes -------------------------------------------


@dataclass(slots=True, frozen=True)
class ElementBounds:
    """Axis-aligned bounding rectangle for a UI element.

    Attributes:
        x: Left-edge X coordinate in screen pixels.
        y: Top-edge Y coordinate in screen pixels.
        width: Width in pixels.
        height: Height in pixels.
    """

    x: int
    y: int
    width: int
    height: int


@dataclass(slots=True, frozen=True)
class ElementState:
    """Normalized accessibility element state flags (architecture v2 §3.2).

    Captures 9 boolean state properties that the MCP tool layer needs
    to represent element state to the LLM.

    Attributes:
        enabled: Whether the element is enabled/interactive.
        focused: Whether the element currently has keyboard focus.
        selected: Whether the element is selected.
        checked: Whether the element is checked (bool or tri-state str).
        expanded: Whether the element is expanded (e.g. combo box, tree node).
        visible: Whether the element is visible on screen.
        offscreen: Whether the element is off-screen (clipped or scrolled away).
        read_only: Whether the element's value is read-only.
        required: Whether the element requires user input (form validation).
    """

    enabled: bool = True
    focused: bool = False
    selected: bool = False
    checked: bool | str = False
    expanded: bool = False
    visible: bool = True
    offscreen: bool = False
    read_only: bool = False
    required: bool = False


# -- Desktop action enumeration --------------------------------------------


class DesktopAction(StrEnum):
    """Enumeration of actions that :meth:`DesktopBackend.perform_action` supports.

    16 canonical variants per architecture v2 §4.3.
    Each value maps to one or more PRD §6 tool endpoints.
    """

    CLICK = "click"
    TYPE = "type"
    PRESS_KEY = "press_key"
    SET_VALUE = "set_value"
    SELECT = "select"
    SELECT_ITEM = "select_item"
    DESELECT_ITEM = "deselect_item"
    ADD_TO_SELECTION = "add_to_selection"
    SCROLL = "scroll"
    SCROLL_TO_ITEM = "scroll_to_item"
    GET_TEXT = "get_text"
    TOGGLE = "toggle"
    EXPAND = "expand"
    COLLAPSE = "collapse"
    INCREMENT = "increment"
    DECREMENT = "decrement"
    GET_TABLE_INFO = "get_table_info"
