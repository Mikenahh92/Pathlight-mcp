"""WindowsBackend — Windows UI Automation accessibility backend (architecture v2 §4.2).

Uses the ``comtypes`` library to access the Windows UI Automation (UIA)
COM API.  The ``comtypes`` import is guarded so that this module can be
imported on non-Windows platforms without error (the constructor will raise
:class:`BackendUnavailableError` at instantiation time).

Implementation status:
- ``list_windows`` — implemented (GW-020)
- ``get_window_info`` — implemented (GW-081)
- ``focus_window`` — implemented (GW-021)
- ``snapshot``, ``find_elements`` — implemented (GW-022)
- ``perform_action``, ``get_element_info``, ``is_valid`` — implemented (GW-023)

Implements :meth:`snapshot` and :meth:`find_elements` via
``IUIAutomationTreeWalker`` for depth-limited accessibility tree traversal,
extracting raw element properties (ControlType, Name, Value, IsEnabled,
bounds, patterns) for later normalization by the tool layer (GW-022).
"""

import contextlib
import logging
import sys
from dataclasses import dataclass
from typing import Any

from guidewire.backends.base import DesktopBackend
from guidewire.backends.normalize import (
    normalize_element,
    normalize_states,
)
from guidewire.backends.types import DesktopAction, NativeHandle
from guidewire.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
    ElementNotFoundError,
    StaleElementReferenceError,
    WindowNotFoundError,
)
from guidewire.models import NormalizedElement
from guidewire.models.mappings import resolve_role, resolve_state

logger = logging.getLogger(__name__)

__all__ = [
    "WindowsBackend",
]

# -- UIA constants (architecture §5: module-level) ----------------------------

_UIA_TREE_SCOPE_CHILDREN = 2  # UIA TreeScope_Children
_UIA_CONTROL_TYPE_PROPERTY_ID = 30003  # UIA_PropertyId_UIA_ControlTypePropertyId
_UIA_WINDOW_CONTROL_TYPE_ID = 50032  # UIA_ControlType_Window (0xC370)
_UIA_IS_OFFSCREEN_PROPERTY_ID = 30022  # UIA_PropertyId_UIA_IsOffscreenPropertyId
_UIA_PROCESS_ID_PROPERTY_ID = 30076  # UIA_PropertyId_UIA_ProcessIdPropertyId
_UIA_NATIVE_WINDOW_HANDLE_PROPERTY_ID = 30020  # UIA_PropertyId_UIA_NativeWindowHandlePropertyId
_UIA_CLASS_NAME_PROPERTY_ID = 30012  # UIA_PropertyId_UIA_ClassNamePropertyId
_UIA_BOUNDING_RECTANGLE_PROPERTY_ID = 30001  # UIA_PropertyId_UIA_BoundingRectanglePropertyId


@dataclass(slots=True)
class _ComHandle:
    """Wraps a COM ``IUIAutomationElement`` reference with a liveness check.

    Architecture §3.1 requires that element references carry the backend's
    ``IUIAutomation`` pointer so that ``is_alive()`` can verify the element
    still exists via ``ElementFromHandle`` or a similar COM query.
    """

    element: Any
    _uia: Any

    def is_alive(self) -> bool:
        """Return ``True`` if the COM element is still reachable.

        Queries ``_uia.CompareElements`` to check the reference is valid
        without requiring a window handle.
        """
        try:
            self._uia.CompareElements(self.element, self.element)
            return True
        except Exception:
            return False


# UIA property IDs used by get_element_info
_UIA_NAME_PROPERTY_ID = 30005  # UIA_PropertyId_UIA_NamePropertyId
_UIA_CONTROL_TYPE_PROPERTY_ID_ROLE = 30003  # Same as above, for role lookup
_UIA_IS_ENABLED_PROPERTY_ID = 30010  # UIA_PropertyId_UIA_IsEnabledPropertyId
_UIA_HAS_KEYBOARD_FOCUS_PROPERTY_ID = 30008  # UIA_PropertyId_UIA_HasKeyboardFocusPropertyId
_UIA_IS_SELECTED_PROPERTY_ID = 30011  # UIA_PropertyId_UIA_IsSelectedPropertyId
_UIA_TOGGLE_STATE_PROPERTY_ID = 30086  # UIA_PropertyId_UIA_ToggleToggleStatePropertyId
_UIA_IS_EXPANDED_PROPERTY_ID = 30015  # UIA_PropertyId_UIA_ExpansionExpandCollapseStatePropertyId
_UIA_IS_READ_ONLY_PROPERTY_ID = 30016  # UIA_PropertyId_UIA_ValueIsReadOnlyPropertyId
_UIA_IS_REQUIRED_FOR_FORM_PROPERTY_ID = 30025  # UIA_PropertyId_UIA_IsRequiredForFormPropertyId
_UIA_IS_PASSWORD_PROPERTY_ID = 30019  # UIA_PropertyId_UIA_IsPasswordPropertyId

# UIA pattern IDs used by perform_action
_UIA_INVOKE_PATTERN_ID = 10000  # UIA_InvokePatternId
_UIA_VALUE_PATTERN_ID = 10002  # UIA_ValuePatternId
_UIA_TOGGLE_PATTERN_ID = 10015  # UIA_TogglePatternId
_UIA_SELECTION_ITEM_PATTERN_ID = 10010  # UIA_SelectionItemPatternId
_UIA_TEXT_PATTERN_ID = 10014  # UIA_TextPatternId
_UIA_SCROLL_PATTERN_ID = 10011  # UIA_ScrollPatternId
_UIA_RANGE_VALUE_PATTERN_ID = 10003  # UIA_RangeValuePatternId
_UIA_EXPAND_COLLAPSE_PATTERN_ID = 10005  # UIA_ExpandCollapsePatternId
_UIA_LEGACY_IAccessIBLE_PATTERN_ID = 10018  # UIA_LegacyIAccessiblePatternId

# UIA ControlType IDs for role mapping
_UIA_CONTROL_TYPE_NAMES: dict[int, str] = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "MenuBar",
    50010: "MenuItem",
    50011: "ProgressBar",
    50012: "RadioButton",
    50013: "ScrollBar",
    50014: "Slider",
    50015: "Spinner",
    50016: "StatusBar",
    50017: "Tab",
    50018: "TabItem",
    50019: "Text",
    50020: "ToolBar",
    50021: "ToolTip",
    50022: "Tree",
    50023: "TreeItem",
    50024: "Custom",
    50025: "Group",
    50026: "Thumb",
    50027: "DataGrid",
    50028: "DataItem",
    50029: "Document",
    50030: "SplitButton",
    50031: "Window",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
}


class WindowsBackend(DesktopBackend):
    """Windows UI Automation-based accessibility backend.

    Wraps the ``comtypes`` library to communicate with the Windows UI
    Automation COM API for enumerating windows, walking the accessibility
    tree, and performing actions on elements.

    Raises:
        BackendUnavailableError: If the platform is not Windows or the
            ``comtypes`` package is not installed.

    Usage::

        from guidewire.backends import WindowsBackend

        backend = WindowsBackend()
        windows = backend.list_windows()
    """

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise BackendUnavailableError(
                f"WindowsBackend requires the Windows platform (current: {sys.platform!r})"
            )
        try:
            import comtypes
            import comtypes.client
        except ImportError:
            raise BackendUnavailableError(
                "WindowsBackend requires the 'comtypes' package "
                "(install via: pip install 'guidewire[windows]')"
            ) from None
        comtypes.CoInitialize()  # type: ignore[attr-defined]
        self._com_initialized: bool = True
        self._uia: Any = comtypes.client.CreateObject(  # type: ignore[attr-defined]
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=comtypes.IUnknown,  # type: ignore[attr-defined]
        )
        self._disposed: bool = False

    # -- DesktopBackend interface (16 abstract methods) -------------------------

    def list_windows(self) -> list[NativeHandle]:
        """List all visible top-level application windows.

        Uses ``IUIAutomation.GetRootElement`` to obtain the desktop root,
        then ``FindAll`` with ``TreeScope_Children`` and a
        ``ControlType.Window`` property condition to enumerate top-level
        windows.  Off-screen windows are filtered out.

        Returns:
            List of opaque native window handles (COM pointers to
            ``IUIAutomationElement`` objects).

        Raises:
            BackendUnavailableError: If the backend is disposed or UIA
                COM calls fail.
        """
        if self._disposed:
            raise BackendUnavailableError("WindowsBackend has been disposed")

        import comtypes  # noqa: F401

        try:
            root = self._uia.GetRootElement()
            condition = self._uia.CreatePropertyCondition(
                _UIA_CONTROL_TYPE_PROPERTY_ID,
                _UIA_WINDOW_CONTROL_TYPE_ID,
            )
            found = self._uia.FindAll(_UIA_TREE_SCOPE_CHILDREN, root, condition)
            count = found.Length
            result: list[NativeHandle] = []
            for i in range(count):
                element = found.GetElement(i)
                is_offscreen = element.GetCurrentPropertyValue(_UIA_IS_OFFSCREEN_PROPERTY_ID)
                if not is_offscreen:
                    result.append(NativeHandle(element))
            return result
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise BackendUnavailableError(f"Failed to enumerate windows via UIA: {exc}") from exc

    def get_window_info(self, window: NativeHandle) -> dict[str, Any]:
        """Return window metadata as a dict.

        Reads the following UIA properties from the COM element:
        - ``CurrentName`` → ``title``
        - ``CurrentClassName`` → ``app_name``
        - ``CurrentHasKeyboardFocus`` → ``focused``
        - ``CurrentBoundingRectangle`` → ``bounds``

        Args:
            window: Opaque native window handle.

        Returns:
            Dict with keys ``title``, ``app_name``, ``focused``, ``bounds``.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        if self._disposed:
            raise WindowNotFoundError("WindowsBackend has been disposed")

        element = self._unwrap_element(window)

        try:
            # Liveness probe — verify the element is reachable.
            # If this raises, the element is stale / invalid and the outer
            # handler will translate it to WindowNotFoundError.
            element.GetCurrentPropertyValue(_UIA_PROCESS_ID_PROPERTY_ID)

            # Title — CurrentName property
            title = ""
            with contextlib.suppress(Exception):
                title = element.GetCurrentPropertyValue(_UIA_NAME_PROPERTY_ID) or ""

            # App name — CurrentClassName as a stable identifier
            app_name = ""
            with contextlib.suppress(Exception):
                app_name = (
                    element.GetCurrentPropertyValue(_UIA_CLASS_NAME_PROPERTY_ID) or ""
                )

            # Focused — CurrentHasKeyboardFocus
            focused = False
            with contextlib.suppress(Exception):
                focused = bool(
                    element.GetCurrentPropertyValue(_UIA_HAS_KEYBOARD_FOCUS_PROPERTY_ID)
                )

            # Bounds — CurrentBoundingRectangle (tagRECT: left, top, width, height)
            bounds: dict[str, int] | None = None
            with contextlib.suppress(Exception):
                rect = element.GetCurrentPropertyValue(
                    _UIA_BOUNDING_RECTANGLE_PROPERTY_ID
                )
                if rect is not None:
                    # comtypes returns a tagRECT with left/top/right/bottom
                    # or sometimes a tuple depending on the COM variant
                    if hasattr(rect, "left"):
                        bounds = {
                            "x": int(rect.left),
                            "y": int(rect.top),
                            "width": int(rect.right - rect.left)
                            if rect.right is not None
                            else 0,
                            "height": int(rect.bottom - rect.top)
                            if rect.bottom is not None
                            else 0,
                        }
                    elif isinstance(rect, (tuple, list)) and len(rect) >= 4:
                        bounds = {
                            "x": int(rect[0]),
                            "y": int(rect[1]),
                            "width": int(rect[2] - rect[0]),
                            "height": int(rect[3] - rect[1]),
                        }

            return {
                "title": str(title),
                "app_name": str(app_name),
                "focused": focused,
                "bounds": bounds,
            }
        except (WindowNotFoundError, BackendUnavailableError):
            raise
        except Exception as exc:
            raise WindowNotFoundError(
                f"Failed to read window info: {exc}"
            ) from exc

    # -- Private helpers (section 4.2-4.4) -----------------------------------

    @staticmethod
    def _extract_hwnd(window: NativeHandle) -> int:
        """Extract an HWND integer from a :class:`NativeHandle`.

        Handles two internal representations:

        1. **HWND integer** — produced by code paths that already resolved
           the native handle.  ``int(window)`` succeeds directly.
        2. **COM IUIAutomationElement** — returned by :meth:`list_windows`.
           The element's ``NativeWindowHandle`` UIA property (30020) is
           read to obtain the HWND integer.

        Args:
            window: Opaque native window handle.

        Returns:
            The underlying HWND as a positive integer.

        Raises:
            WindowNotFoundError: If the extracted handle is zero (null window).
            TypeError: If the handle cannot be converted to an HWND.
        """
        from guidewire.errors import WindowNotFoundError

        # Fast path — already an int.
        if isinstance(window, int):
            hwnd = window
        else:
            # Try int conversion first (covers NativeHandle wrapping an int).
            try:
                hwnd = int(window)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                # COM IUIAutomationElement — read the NativeWindowHandle property.
                try:
                    hwnd = int(
                        window.GetCurrentPropertyValue(  # type: ignore[union-attr]
                            _UIA_NATIVE_WINDOW_HANDLE_PROPERTY_ID
                        )
                    )
                except Exception as exc:
                    raise TypeError(
                        f"Cannot extract HWND from {type(window).__name__}: {exc}"
                    ) from exc

        if hwnd == 0:
            raise WindowNotFoundError("Window handle 0x0 is not a valid window")
        return hwnd

    def _element_from_handle(self, hwnd: int) -> Any:
        """Bridge an HWND to an ``IUIAutomationElement`` via COM.

        Args:
            hwnd: A valid window handle.

        Returns:
            The COM ``IUIAutomationElement`` for the window.
        """
        return self._uia.ElementFromHandle(hwnd)

    # -- DesktopBackend interface (16 abstract methods) -------------------------

    def focus_window(self, window: NativeHandle) -> None:
        """Bring a window to the foreground.

        Validates the backend is not disposed, extracts and validates the
        HWND, then calls the Win32 ``SetForegroundWindow`` API.  If the first
        attempt fails (foreground-lock restriction), a ``keybd_event`` Alt-key
        workaround is applied (architecture §2.4) and the call is retried once.
        On success, ``IUIAutomationElement.SetFocus`` is called to set keyboard
        focus (architecture §2.5).

        Args:
            window: Opaque native window handle (HWND integer).

        Raises:
            RuntimeError: If the backend has been disposed.
            WindowNotFoundError: If the handle does not reference a valid window
                or ``SetForegroundWindow`` fails to activate it.
            TypeError: If *window* is not an int-backed NativeHandle.
            OSError: If an unexpected ctypes / OS error occurs.
        """
        import ctypes

        from guidewire.errors import WindowNotFoundError

        if self._disposed:
            raise RuntimeError("Cannot focus window on a disposed backend")

        try:
            hwnd = self._extract_hwnd(window)
        except TypeError as exc:
            raise TypeError(f"window must be an int-backed NativeHandle: {exc}") from exc

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]

        if not user32.IsWindow(hwnd):  # type: ignore[attr-defined]
            raise WindowNotFoundError(f"Window handle {hwnd:#x} is not a valid window")

        # First attempt: SetForegroundWindow
        result = user32.SetForegroundWindow(hwnd)  # type: ignore[attr-defined]

        # Foreground-lock workaround (architecture §2.4): simulate an Alt
        # keypress to convince Windows that user interaction is occurring,
        # then retry SetForegroundWindow once.
        if not result:
            vk_menu = 0x12  # Alt key virtual-key code
            keyeventf_keyup = 0x0002
            user32.keybd_event(vk_menu, 0, 0, 0)  # type: ignore[attr-defined]
            user32.keybd_event(  # type: ignore[attr-defined]
                vk_menu,
                0,
                keyeventf_keyup,
                0,
            )
            result = user32.SetForegroundWindow(hwnd)  # type: ignore[attr-defined]

        if not result:
            raise WindowNotFoundError(f"SetForegroundWindow failed for window handle {hwnd:#x}")

        # Set UIA keyboard focus (architecture §2.5)
        try:
            element = self._element_from_handle(hwnd)
            element.SetFocus()
        except Exception:
            # SetFocus is best-effort; foreground was already set.
            pass

    def snapshot(
        self,
        window: NativeHandle,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """Return an accessibility snapshot as a tree dict (GW-022).

        Walks the UIA tree rooted at *window* using ``IUIAutomationTreeWalker``,
        extracting raw element properties and normalizing them via the
        cross-platform mapping tables in :mod:`guidewire.models.mappings`.

        Args:
            window: Opaque native window handle (COM ``IUIAutomationElement``).
            max_depth: Maximum tree depth to traverse (default 4).
            max_nodes: Maximum number of nodes to include (default 500).

        Returns:
            Dict matching the DesktopElement schema with keys:
            ``ref``, ``role``, ``name``, ``states``, ``bounds``,
            ``actions``, ``children``.

        Raises:
            WindowNotFoundError: If the handle is invalid or the backend
                is disposed.
        """
        self._ensure_not_disposed()
        return self._walk_tree(window, max_depth=max_depth, max_nodes=max_nodes)

    # -- Internal tree-walking helpers -----------------------------------------

    def _ensure_not_disposed(self) -> None:
        """Raise :class:`WindowNotFoundError` if the backend is disposed."""
        if self._disposed:
            raise WindowNotFoundError("Backend is disposed")

    def _get_tree_walker(self) -> Any:
        """Return the control view ``IUIAutomationTreeWalker`` from ``self._uia``.

        Uses ``ControlViewWalker`` rather than ``RawViewWalker`` so that
        offscreen and decorative elements are excluded natively by the UIA
        runtime, producing a cleaner tree for LLM consumption.
        """
        return self._uia.ControlViewWalker

    def _extract_element_node(self, element: Any) -> NormalizedElement:
        """Extract properties from a single ``IUIAutomationElement``.

        Reads ControlType, Name, Value, IsEnabled, bounding rectangle,
        and supported patterns, then normalizes via
        :func:`~guidewire.backends.normalize.normalize_element`.

        Returns:
            A :class:`~guidewire.models.NormalizedElement` with all fields
            populated from the COM element.
        """
        # --- ControlType / role ---
        try:
            control_type_id = element.CurrentControlType
        except Exception:
            control_type_id = 0

        control_type_name = _control_type_id_to_name(control_type_id)

        # --- Name ---
        try:
            name = element.CurrentName
        except Exception:
            name = None

        # --- Value ---
        value = None
        try:
            value_pattern = element.GetCurrentPattern(_UIA_VALUE_PATTERN_ID)
            if value_pattern is not None:
                value = value_pattern.CurrentValue
        except Exception:
            pass

        # --- Collect raw states ---
        raw_states: dict[str, Any] = {}
        _read_state(element, "IsEnabled", "CurrentIsEnabled", bool, raw_states)
        _read_state(element, "HasKeyboardFocus", "CurrentHasKeyboardFocus", bool, raw_states)
        _read_state(element, "IsSelected", "CurrentIsSelected", bool, raw_states)
        _read_state(element, "IsOffscreen", "CurrentIsOffscreen", bool, raw_states)
        _read_state(element, "IsReadOnly", "CurrentIsReadOnly", bool, raw_states)
        _read_state(element, "IsRequiredForForm", "CurrentIsRequiredForForm", bool, raw_states)
        _read_state(element, "IsPassword", "CurrentIsPassword", bool, raw_states)

        # ToggleState needs special handling (integer enum)
        try:
            toggle_state = element.CurrentToggleState
            resolved = resolve_state("windows", "ToggleState", toggle_state)
            if resolved:
                raw_states[resolved[0]] = resolved[1]
        except Exception:
            pass

        # IsExpanded
        try:
            expanded = element.CurrentIsExpanded
            resolved = resolve_state("windows", "IsExpanded", expanded)
            if resolved:
                raw_states[resolved[0]] = resolved[1]
        except Exception:
            pass

        # Visibility (requires element pattern)
        try:
            visibility = element.CurrentVisibility
            resolved = resolve_state("windows", "Visibility", visibility)
            if resolved:
                raw_states[resolved[0]] = resolved[1]
        except Exception:
            pass

        # --- Bounds (raw tuple for normalizer) ---
        raw_bounds: tuple[int, int, int, int] | None = None
        try:
            rect = element.CurrentBoundingRectangle
            if rect:
                raw_bounds = (int(rect.left), int(rect.top), int(rect.width), int(rect.height))
        except Exception:
            pass

        # --- Collect raw action patterns ---
        raw_actions: list[str] = []
        for pattern_id, pattern_name in _UIA_PATTERN_MAP:
            try:
                pattern = element.GetCurrentPattern(pattern_id)
                if pattern is not None:
                    raw_actions.append(pattern_name)
            except Exception:
                pass

        return normalize_element(
            platform="windows",
            ref=str(id(element)),
            backend_id=str(control_type_id),
            role=control_type_name,
            native_role=control_type_name,
            control_type=control_type_name,
            name=name,
            value=value,
            raw_states=raw_states,
            bounds=raw_bounds,
            raw_actions=raw_actions,
        )

    def _walk_tree(
        self,
        root_element: Any,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """Recursively walk the UIA tree and build a snapshot dict.

        The root element is always included regardless of offscreen state.
        Offscreen filtering only applies to descendant nodes.

        Args:
            root_element: The root ``IUIAutomationElement`` to walk from.
            max_depth: Maximum depth to traverse.
            max_nodes: Maximum total nodes (including root).

        Returns:
            Tree dict matching the DesktopElement schema.
        """
        counter = [0]
        walker = self._get_tree_walker()
        node = self._walk_recursive(root_element, walker, 0, max_depth, counter, max_nodes)
        if node is None:
            # Root should never be None; return a minimal node as fallback
            fallback = NormalizedElement(
                ref=str(id(root_element)),
                backend_id="0",
                role="unknown",
            )
            return fallback.to_dict()
        return node.to_dict()

    def _walk_recursive(
        self,
        element: Any,
        walker: Any,
        depth: int,
        max_depth: int,
        counter: list[int],
        max_nodes: int,
    ) -> NormalizedElement | None:
        """Recursively walk from *element* building a NormalizedElement tree.

        Returns ``None`` for offscreen descendant elements (depth > 0),
        which the caller filters out of the children list.  The root
        element (depth 0) is always included.  This acts as a safety net
        on top of ``ControlViewWalker`` (which already excludes most
        offscreen nodes).
        """
        if counter[0] >= max_nodes:
            return NormalizedElement(
                ref=str(id(element)),
                backend_id="0",
                role="unknown",
            )

        counter[0] += 1
        node = self._extract_element_node(element)

        # Exclude offscreen descendants (but never the root at depth 0)
        if depth > 0 and node.states.offscreen is True:
            counter[0] -= 1
            return None

        if depth < max_depth:
            children: list[NormalizedElement] = []
            try:
                child = walker.GetFirstChildElement(element)
                while child is not None:
                    if counter[0] >= max_nodes:
                        break
                    child_node = self._walk_recursive(
                        child, walker, depth + 1, max_depth, counter, max_nodes
                    )
                    if child_node is not None:
                        children.append(child_node)
                    next_child = walker.GetNextSiblingElement(child)
                    child = next_child
            except Exception:
                logger.debug("Error walking children at depth %d", depth, exc_info=True)
            node.children = children

        return node

    def find_elements(
        self,
        window: NativeHandle,
        role: str | None = None,
        name: str | None = None,
    ) -> list[_ComHandle]:
        """Find elements matching criteria within a window (GW-022).

        Walks the UIA tree rooted at *window* and collects elements whose
        normalized role and/or accessible name match the given filters.

        Args:
            window: Opaque native window handle (COM ``IUIAutomationElement``).
            role: Normalized role to match (exact).
            name: Accessible name to match (case-insensitive substring).

        Returns:
            List of matching ``_ComHandle`` references wrapping COM pointers.
        """
        self._ensure_not_disposed()

        # AC-15 / AD-5: require at least one filter criterion
        if role is None and name is None:
            return []

        results: list[_ComHandle] = []
        walker = self._get_tree_walker()

        try:
            child = walker.GetFirstChildElement(window)
            while child is not None:
                self._find_recursive(child, walker, 0, role, name, results)
                child = walker.GetNextSiblingElement(child)
        except Exception:
            logger.debug("Error in find_elements tree walk", exc_info=True)

        return results

    def _find_recursive(
        self,
        element: Any,
        walker: Any,
        depth: int,
        role: str | None,
        name: str | None,
        results: list[_ComHandle],
    ) -> None:
        """Recursively search for elements matching *role* and/or *name*.

        No depth limit — architecture §3.3 requires exhaustive traversal.
        """
        # Extract role and name from the element
        try:
            control_type_id = element.CurrentControlType
        except Exception:
            control_type_id = 0

        control_type_name = _control_type_id_to_name(control_type_id)
        normalized_role = resolve_role("windows", control_type_name) or control_type_name

        try:
            element_name = element.CurrentName
        except Exception:
            element_name = None

        # Match criteria
        role_match = role is None or normalized_role == role
        name_match = name is None or (
            element_name is not None and name.lower() in element_name.lower()
        )

        if role_match and name_match:
            results.append(_ComHandle(element, self._uia))

        # Recurse into children
        try:
            child = walker.GetFirstChildElement(element)
            while child is not None:
                self._find_recursive(child, walker, depth + 1, role, name, results)
                child = walker.GetNextSiblingElement(child)
        except Exception:
            pass

    def perform_action(
        self,
        handle: NativeHandle,
        action: DesktopAction,
        **kwargs: Any,
    ) -> Any:
        """Perform an action on an element via UIA COM patterns.

        Maps each :class:`DesktopAction` variant to the appropriate UIA
        pattern interface:

        - ``CLICK`` → ``IUIAutomationInvokePattern.Invoke()``
        - ``TYPE`` → ``IUIAutomationTextPattern`` / keyboard simulation
        - ``PRESS_KEY`` → ``IUIAutomationLegacyIAccessiblePattern.DoDefaultAction``
          or ``SendInput`` keyboard simulation
        - ``SET_VALUE`` → ``IUIAutomationValuePattern.SetValue()``
        - ``SELECT`` → ``IUIAutomationSelectionItemPattern.Select()``
        - ``SELECT_ITEM`` → ``IUIAutomationSelectionItemPattern.Select()``
        - ``DESELECT_ITEM`` → ``IUIAutomationSelectionItemPattern.RemoveFromSelection()``
        - ``ADD_TO_SELECTION`` → ``IUIAutomationSelectionItemPattern.AddToSelection()``
        - ``SCROLL`` → ``IUIAutomationScrollPattern.Scroll()``
        - ``GET_TEXT`` → ``IUIAutomationValuePattern.CurrentValue``
        - ``TOGGLE`` → ``IUIAutomationTogglePattern.Toggle()``
        - ``EXPAND`` → ``IUIAutomationExpandCollapsePattern.Expand()``
        - ``COLLAPSE`` → ``IUIAutomationExpandCollapsePattern.Collapse()``
        - ``INCREMENT`` → ``IUIAutomationRangeValuePattern.SetValue(current + SmallChange)``
        - ``DECREMENT`` → ``IUIAutomationRangeValuePattern.SetValue(current - SmallChange)``

        Args:
            handle: Opaque native element handle (COM ``IUIAutomationElement``).
            action: The action to perform.
            **kwargs: Action-specific parameters:
                - ``text`` (str): text to type (for ``TYPE``)
                - ``value`` (str): value to set (for ``SET_VALUE``)
                - ``key`` (str): key to press (for ``PRESS_KEY``)
                - ``scroll_amount`` (int): scroll amount (for ``SCROLL``)
                - ``horizontal`` (bool): scroll direction (for ``SCROLL``)

        Returns:
            ``str`` when action is ``GET_TEXT``, otherwise ``None``.

        Raises:
            StaleElementReferenceError: If the backend is disposed.
            ActionNotSupportedError: If the element does not support the
                required pattern.
            ElementNotFoundError: If the handle is invalid.
        """
        if self._disposed:
            raise StaleElementReferenceError("WindowsBackend has been disposed")

        element = self._unwrap_element(handle)

        try:
            if action == DesktopAction.CLICK:
                return self._action_click(element)
            if action == DesktopAction.TYPE:
                return self._action_type(element, **kwargs)
            if action == DesktopAction.PRESS_KEY:
                return self._action_press_key(element, **kwargs)
            if action == DesktopAction.SET_VALUE:
                return self._action_set_value(element, **kwargs)
            if action == DesktopAction.SELECT:
                return self._action_select(element)
            if action == DesktopAction.SELECT_ITEM:
                return self._action_select_item(element)
            if action == DesktopAction.DESELECT_ITEM:
                return self._action_deselect_item(element)
            if action == DesktopAction.ADD_TO_SELECTION:
                return self._action_add_to_selection(element)
            if action == DesktopAction.SCROLL:
                return self._action_scroll(element, **kwargs)
            if action == DesktopAction.GET_TEXT:
                return self._action_get_text(element)
            if action == DesktopAction.TOGGLE:
                return self._action_toggle(element)
            if action == DesktopAction.EXPAND:
                return self._action_expand(element)
            if action == DesktopAction.COLLAPSE:
                return self._action_collapse(element)
            if action == DesktopAction.INCREMENT:
                return self._action_increment(element)
            if action == DesktopAction.DECREMENT:
                return self._action_decrement(element)
            if action == DesktopAction.GET_TABLE_INFO:
                return self._dispatch_table_info(element, **kwargs)
        except ActionNotSupportedError:
            raise
        except StaleElementReferenceError:
            raise
        except BackendUnavailableError:
            raise
        except Exception as exc:
            if hasattr(exc, "hresult"):
                raise self._translate_com_error(exc) from exc
            raise ActionNotSupportedError(f"Failed to perform {action!r}: {exc}") from exc

    # -- Action dispatch helpers -----------------------------------------------

    @staticmethod
    def _translate_com_error(
        exc: Exception,
    ) -> StaleElementReferenceError | ActionNotSupportedError:
        """Translate a COM exception based on its HRESULT code.

        Per architecture §5:
        - 0x80040201 (UIA_E_ELEMENTNOTAVAILABLE) → StaleElementReferenceError
        - 0x80070005 (E_ACCESSDENIED) → StaleElementReferenceError
        - 0x80070057 (E_INVALIDARG) → ActionNotSupportedError
        - other HRESULTs → StaleElementReferenceError

        Args:
            exc: The COM exception to translate.

        Returns:
            The appropriate Guidewire exception.
        """
        hresult = getattr(exc, "hresult", None)
        if hresult is not None and hresult & 0x80070000 == 0x80070000 and hresult == 0x80070057:
            return ActionNotSupportedError(
                f"Invalid argument for COM call (HRESULT 0x{hresult:08X}): {exc}"
            )
        return StaleElementReferenceError(f"Element is no longer available (COM error): {exc}")

    @staticmethod
    def _unwrap_element(handle: NativeHandle) -> Any:
        """Extract the underlying COM element from a NativeHandle.

        Args:
            handle: Opaque native element handle.

        Returns:
            The COM ``IUIAutomationElement`` object.

        Raises:
            ElementNotFoundError: If the handle is ``None`` or empty.
        """
        element = handle
        if element is None:
            raise ElementNotFoundError("Element handle is None")
        return element

    def _get_pattern(self, element: Any, pattern_id: int) -> Any:
        """Retrieve a UIA pattern from an element.

        Args:
            element: COM ``IUIAutomationElement``.
            pattern_id: UIA pattern identifier (e.g. ``_UIA_INVOKE_PATTERN_ID``).

        Returns:
            The pattern COM interface object.

        Raises:
            ActionNotSupportedError: If the pattern is not available.
        """
        try:
            pattern = self._uia.GetPattern(element, pattern_id)
            if pattern is None:
                pattern_name = {
                    _UIA_INVOKE_PATTERN_ID: "Invoke",
                    _UIA_VALUE_PATTERN_ID: "Value",
                    _UIA_TOGGLE_PATTERN_ID: "Toggle",
                    _UIA_SELECTION_ITEM_PATTERN_ID: "SelectionItem",
                    _UIA_TEXT_PATTERN_ID: "Text",
                    _UIA_SCROLL_PATTERN_ID: "Scroll",
                    _UIA_RANGE_VALUE_PATTERN_ID: "RangeValue",
                    _UIA_EXPAND_COLLAPSE_PATTERN_ID: "ExpandCollapse",
                    _UIA_LEGACY_IAccessIBLE_PATTERN_ID: "LegacyIAccessible",
                }.get(pattern_id, str(pattern_id))
                raise ActionNotSupportedError(
                    f"Element does not support the {pattern_name} pattern"
                )
            return pattern
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            if hasattr(exc, "hresult"):
                raise self._translate_com_error(exc) from exc
            raise ActionNotSupportedError(f"Failed to get pattern {pattern_id}: {exc}") from exc

    def _action_click(self, element: Any) -> None:
        """Click an element via InvokePattern.

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If InvokePattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_INVOKE_PATTERN_ID)
        try:
            pattern.Invoke()
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Invoke failed: {exc}") from exc

    def _action_type(self, element: Any, **kwargs: Any) -> None:
        """Type text into an element.

        Uses ``IUIAutomationValuePattern`` if the element supports it.
        Per architecture §2.1 and test-design TC-PA-002, reads the current
        value, concatenates the new text, then calls ``SetValue`` with the
        result (append semantics).  Falls back to setting focus and sending
        keystrokes via ``SendInput`` when ValuePattern is unavailable.

        Args:
            element: COM ``IUIAutomationElement``.
            **kwargs: Must contain ``text`` (str).

        Raises:
            ActionNotSupportedError: If text cannot be typed.
        """
        text = kwargs.get("text")
        if text is None:
            raise ActionNotSupportedError("TYPE action requires a 'text' parameter")

        # Try ValuePattern first (append semantics: read + concat + set)
        try:
            pattern = self._get_pattern(element, _UIA_VALUE_PATTERN_ID)
            current = str(pattern.CurrentValue)
            pattern.SetValue(current + str(text))
            return
        except ActionNotSupportedError:
            pass

        # Fallback: set focus and simulate keyboard input
        try:
            element.SetFocus()
            self._send_text(str(text))
        except Exception as exc:
            raise ActionNotSupportedError(f"Failed to type text: {exc}") from exc

    def _action_press_key(self, element: Any, **kwargs: Any) -> None:
        """Press a key or key combo while an element has focus.

        Sets focus on the element, then simulates the key press via
        ``SendInput``.  Supports both single keys (``"Enter"``) and
        modifier combos (``"ctrl+s"``, ``"ctrl+shift+escape"``).

        Args:
            element: COM ``IUIAutomationElement``.
            **kwargs: Must contain ``keys`` (str) — the normalised key
                combo string from the tool layer.

        Raises:
            ActionNotSupportedError: If the key press cannot be performed.
        """
        keys = kwargs.get("keys")
        if keys is None:
            raise ActionNotSupportedError("PRESS_KEY action requires a 'keys' parameter")

        try:
            element.SetFocus()
            self._send_key_combo(str(keys))
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Failed to press key: {exc}") from exc

    def _action_set_value(self, element: Any, **kwargs: Any) -> None:
        """Set the value of an element via ValuePattern.

        Args:
            element: COM ``IUIAutomationElement``.
            **kwargs: Must contain ``value`` (str).

        Raises:
            ActionNotSupportedError: If ValuePattern is not available.
        """
        value = kwargs.get("value")
        if value is None:
            raise ActionNotSupportedError("SET_VALUE action requires a 'value' parameter")

        pattern = self._get_pattern(element, _UIA_VALUE_PATTERN_ID)
        try:
            pattern.SetValue(str(value))
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"SetValue failed: {exc}") from exc

    def _action_select(self, element: Any) -> None:
        """Select an element via SelectionItemPattern.

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If SelectionItemPattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_SELECTION_ITEM_PATTERN_ID)
        try:
            pattern.Select()
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Select failed: {exc}") from exc

    def _action_select_item(self, element: Any) -> None:
        """Select an item via SelectionItemPattern.Select().

        Same underlying pattern as SELECT but exposed as SELECT_ITEM for
        multi-select containers that distinguish single-item selection.

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If SelectionItemPattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_SELECTION_ITEM_PATTERN_ID)
        try:
            pattern.Select()
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"SelectItem failed: {exc}") from exc

    def _action_deselect_item(self, element: Any) -> None:
        """Deselect an item via SelectionItemPattern.RemoveFromSelection().

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If SelectionItemPattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_SELECTION_ITEM_PATTERN_ID)
        try:
            pattern.RemoveFromSelection()
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"DeselectItem failed: {exc}") from exc

    def _action_add_to_selection(self, element: Any) -> None:
        """Add an item to the current selection via SelectionItemPattern.AddToSelection().

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If SelectionItemPattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_SELECTION_ITEM_PATTERN_ID)
        try:
            pattern.AddToSelection()
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"AddToSelection failed: {exc}") from exc

    def _action_scroll(self, element: Any, **kwargs: Any) -> None:
        """Scroll an element via ScrollPattern.

        Args:
            element: COM ``IUIAutomationElement``.
            **kwargs: Optional ``scroll_amount`` (int) and ``horizontal`` (bool).

        Raises:
            ActionNotSupportedError: If ScrollPattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_SCROLL_PATTERN_ID)
        try:
            horizontal = bool(kwargs.get("horizontal", False))
            amount = int(kwargs.get("scroll_amount", 1))
            # UIA ScrollAmount: 0=LargeDecrement, 1=SmallDecrement,
            # 2=NoAmount, 3=SmallIncrement, 4=LargeIncrement
            scroll_amount = 3 if amount >= 0 else 1
            if horizontal:
                pattern.ScrollHorizontal(scroll_amount)
            else:
                pattern.ScrollVertical(scroll_amount)
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Scroll failed: {exc}") from exc

    def _action_get_text(self, element: Any) -> str:
        """Get the text value of an element via ValuePattern.

        Args:
            element: COM ``IUIAutomationElement``.

        Returns:
            The current value string.

        Raises:
            ActionNotSupportedError: If ValuePattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_VALUE_PATTERN_ID)
        try:
            return str(pattern.CurrentValue)
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"GetText failed: {exc}") from exc

    def _action_toggle(self, element: Any) -> None:
        """Toggle an element via TogglePattern.

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If TogglePattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_TOGGLE_PATTERN_ID)
        try:
            pattern.Toggle()
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Toggle failed: {exc}") from exc

    def _action_expand(self, element: Any) -> None:
        """Expand an element via ExpandCollapsePattern.

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If ExpandCollapsePattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_EXPAND_COLLAPSE_PATTERN_ID)
        try:
            pattern.Expand()
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Expand failed: {exc}") from exc

    def _action_collapse(self, element: Any) -> None:
        """Collapse an element via ExpandCollapsePattern.

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If ExpandCollapsePattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_EXPAND_COLLAPSE_PATTERN_ID)
        try:
            pattern.Collapse()
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Collapse failed: {exc}") from exc

    def _action_increment(self, element: Any) -> None:
        """Increment a range value element via RangeValuePattern.

        Reads the current value and adds the small-change delta.

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If RangeValuePattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_RANGE_VALUE_PATTERN_ID)
        try:
            current = pattern.CurrentValue
            small_change = pattern.SmallChange
            if small_change <= 0:
                small_change = 1.0
            new_value = current + small_change
            maximum = pattern.Maximum
            if new_value > maximum:
                new_value = maximum
            pattern.SetValue(new_value)
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Increment failed: {exc}") from exc

    def _action_decrement(self, element: Any) -> None:
        """Decrement a range value element via RangeValuePattern.

        Reads the current value and subtracts the small-change delta.

        Args:
            element: COM ``IUIAutomationElement``.

        Raises:
            ActionNotSupportedError: If RangeValuePattern is not available.
        """
        pattern = self._get_pattern(element, _UIA_RANGE_VALUE_PATTERN_ID)
        try:
            current = pattern.CurrentValue
            small_change = pattern.SmallChange
            if small_change <= 0:
                small_change = 1.0
            new_value = current - small_change
            minimum = pattern.Minimum
            if new_value < minimum:
                new_value = minimum
            pattern.SetValue(new_value)
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Decrement failed: {exc}") from exc

    @staticmethod
    def _send_text(text: str) -> None:
        """Simulate typing text via ``SendInput`` Win32 API.

        Args:
            text: The text string to type.
        """
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]

        for char in text:
            vk = ctypes.windll.user32.VkKeyScanW(ord(char)) & 0xFF  # type: ignore[attr-defined]
            if vk == 0xFF:
                continue
            user32.SendInput(  # type: ignore[attr-defined]
                1,
                ctypes.byref(
                    ctypes.windll.user32.INPUT(  # type: ignore[attr-defined]
                        type=1,  # INPUT_KEYBOARD
                        ki=ctypes.windll.user32.KEYBDINPUT(  # type: ignore[attr-defined]
                            wVk=vk,
                        ),
                    )
                ),
                ctypes.sizeof(ctypes.windll.user32.INPUT),  # type: ignore[attr-defined]
            )

    # -- Virtual-key code maps ------------------------------------------------

    _MODIFIER_VK: dict[str, int] = {
        "ctrl": 0x11,       # VK_CONTROL
        "control": 0x11,
        "alt": 0x12,        # VK_MENU
        "shift": 0x10,      # VK_SHIFT
        "super": 0x5B,      # VK_LWIN
        "cmd": 0x5B,
        "command": 0x5B,
        "win": 0x5B,
        "meta": 0x5B,
    }

    _KEY_VK: dict[str, int] = {
        "enter": 0x0D,
        "return": 0x0D,
        "tab": 0x09,
        "escape": 0x1B,
        "esc": 0x1B,
        "backspace": 0x08,
        "delete": 0x2E,
        "del": 0x2E,
        "insert": 0x2D,
        "arrowup": 0x26,
        "arrowdown": 0x28,
        "arrowleft": 0x25,
        "arrowright": 0x27,
        "up": 0x26,
        "down": 0x28,
        "left": 0x25,
        "right": 0x27,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21,
        "page_up": 0x21,
        "pagedown": 0x22,
        "page_down": 0x22,
        "f1": 0x70,
        "f2": 0x71,
        "f3": 0x72,
        "f4": 0x73,
        "f5": 0x74,
        "f6": 0x75,
        "f7": 0x76,
        "f8": 0x77,
        "f9": 0x78,
        "f10": 0x79,
        "f11": 0x7A,
        "f12": 0x7B,
        "space": 0x20,
    }

    @staticmethod
    def _send_key(key: str) -> None:
        """Simulate a single key press via ``SendInput`` Win32 API.

        Maps common key names to virtual-key codes.

        Args:
            key: The key name (e.g. ``"Enter"``, ``"Tab"``, ``"Escape"``).
        """
        import ctypes

        _key_map: dict[str, int] = {
            "enter": 0x0D,
            "tab": 0x09,
            "escape": 0x1B,
            "backspace": 0x08,
            "delete": 0x2E,
            "arrowup": 0x26,
            "arrowdown": 0x28,
            "arrowleft": 0x25,
            "arrowright": 0x27,
            "home": 0x24,
            "end": 0x23,
            "pageup": 0x21,
            "pagedown": 0x22,
            "f1": 0x70,
            "f2": 0x71,
            "f3": 0x72,
            "f4": 0x73,
            "f5": 0x74,
            "f6": 0x75,
            "f7": 0x76,
            "f8": 0x77,
            "f9": 0x78,
            "f10": 0x79,
            "f11": 0x7A,
            "f12": 0x7B,
            "space": 0x20,
        }

        vk = _key_map.get(key.lower())
        if vk is None:
            vk = (
                ctypes.windll.user32.VkKeyScanW(ord(key)) & 0xFF  # type: ignore[attr-defined]
                if len(key) == 1
                else 0
            )

        if vk == 0:
            return

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.SendInput(  # type: ignore[attr-defined]
            1,
            ctypes.byref(
                user32.INPUT(  # type: ignore[attr-defined]
                    type=1,
                    ki=user32.KEYBDINPUT(wVk=vk),
                )
            ),
            ctypes.sizeof(user32.INPUT),  # type: ignore[attr-defined]
        )

    @staticmethod
    def _send_key_combo(combo: str) -> None:
        """Simulate a key combo via ``SendInput`` Win32 API.

        Parses a normalised key-combo string (e.g. ``"ctrl+s"``,
        ``"ctrl+shift+escape"``, ``"enter"``) and sends the key events
        using ``SendInput``:

        1. Press all modifier keys in left-to-right order.
        2. Press and release the final (non-modifier) key.
        3. Release all modifier keys in reverse order.

        For single keys (no ``+`` separator), delegates directly to
        ``_send_key``.

        Args:
            combo: Normalised key combo string from the tool layer.
        """
        import ctypes

        parts = [p.strip() for p in combo.split("+")]
        if not parts:
            return

        # Fast path: single key, no modifier
        if len(parts) == 1:
            WindowsBackend._send_key(parts[0])
            return

        modifier_vks: list[int] = []
        main_key: str = parts[-1]  # last part is always the main key

        for part in parts[:-1]:
            vk = WindowsBackend._MODIFIER_VK.get(part)
            if vk is None:
                # Unknown modifier — treat the entire combo as a single key
                WindowsBackend._send_key(combo)
                return
            modifier_vks.append(vk)

        # Resolve the main key VK
        main_vk = WindowsBackend._KEY_VK.get(main_key)
        if main_vk is None:
            if len(main_key) == 1:
                main_vk = ctypes.windll.user32.VkKeyScanW(ord(main_key)) & 0xFF  # type: ignore[attr-defined]
            if not main_vk:
                return

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        keyeventf_keyup = 0x0002

        def _send_keyevent(vk: int, flags: int = 0) -> None:
            user32.SendInput(
                1,
                ctypes.byref(
                    user32.INPUT(
                        type=1,  # INPUT_KEYBOARD
                        ki=user32.KEYBDINPUT(wVk=vk, dwFlags=flags),
                    )
                ),
                ctypes.sizeof(user32.INPUT),
            )

        # 1. Press modifiers
        for mod_vk in modifier_vks:
            _send_keyevent(mod_vk)

        # 2. Press + release main key
        _send_keyevent(main_vk)
        _send_keyevent(main_vk, keyeventf_keyup)

        # 3. Release modifiers in reverse order
        for mod_vk in reversed(modifier_vks):
            _send_keyevent(mod_vk, keyeventf_keyup)

    def get_element_info(self, handle: NativeHandle) -> dict[str, Any]:
        """Return element metadata as a dict.

        Reads the following UIA properties from the COM element:
        - ``CurrentControlType`` → normalized role via ``resolve_role``
        - ``CurrentName`` → ``name``
        - ``IsEnabled``, ``HasKeyboardFocus``, ``IsSelected``,
          ``ToggleState``, ``IsExpanded``, ``IsReadOnly``,
          ``IsRequiredForForm``, ``IsPassword``, ``IsOffscreen``,
          ``Visibility`` → ``states`` dict

        Args:
            handle: Opaque native element handle (COM ``IUIAutomationElement``).

        Returns:
            Dict with keys ``role``, ``name``, ``states``.

        Raises:
            ElementNotFoundError: If the handle is ``None`` or invalid.
            StaleElementReferenceError: If the backend is disposed.
        """
        from guidewire.models.mappings import resolve_role

        if self._disposed:
            raise StaleElementReferenceError("WindowsBackend has been disposed")

        element = self._unwrap_element(handle)

        try:
            # Read role from ControlType
            control_type_id = element.GetCurrentPropertyValue(_UIA_CONTROL_TYPE_PROPERTY_ID)
            control_type_name = _UIA_CONTROL_TYPE_NAMES.get(int(control_type_id), "Custom")
            role = resolve_role("windows", control_type_name) or "custom"

            # Read name
            name = element.GetCurrentPropertyValue(_UIA_NAME_PROPERTY_ID)

            # Read states via normalize_states for consistency
            raw_states: dict[str, Any] = {}
            raw_states["IsEnabled"] = element.GetCurrentPropertyValue(_UIA_IS_ENABLED_PROPERTY_ID)
            raw_states["HasKeyboardFocus"] = element.GetCurrentPropertyValue(
                _UIA_HAS_KEYBOARD_FOCUS_PROPERTY_ID
            )
            raw_states["IsSelected"] = element.GetCurrentPropertyValue(_UIA_IS_SELECTED_PROPERTY_ID)
            raw_states["ToggleState"] = element.GetCurrentPropertyValue(
                _UIA_TOGGLE_STATE_PROPERTY_ID
            )
            raw_states["IsExpanded"] = element.GetCurrentPropertyValue(_UIA_IS_EXPANDED_PROPERTY_ID)
            raw_states["IsReadOnly"] = element.GetCurrentPropertyValue(
                _UIA_IS_READ_ONLY_PROPERTY_ID
            )
            raw_states["IsRequiredForForm"] = element.GetCurrentPropertyValue(
                _UIA_IS_REQUIRED_FOR_FORM_PROPERTY_ID
            )
            raw_states["IsPassword"] = element.GetCurrentPropertyValue(_UIA_IS_PASSWORD_PROPERTY_ID)
            raw_states["IsOffscreen"] = element.GetCurrentPropertyValue(
                _UIA_IS_OFFSCREEN_PROPERTY_ID
            )

            norm_states = normalize_states("windows", raw_states)
            from dataclasses import fields as _dc_fields

            states_dict = {
                f.name: getattr(norm_states, f.name)
                for f in _dc_fields(norm_states)
                if getattr(norm_states, f.name) is not None
            }

            return {
                "role": role,
                "name": str(name) if name else None,
                "states": states_dict,
            }
        except ElementNotFoundError:
            raise
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise ElementNotFoundError(f"Failed to read element info: {exc}") from exc

    def _dispatch_table_info(self, element: Any, **kwargs: Any) -> dict[str, Any]:
        """Handle GET_TABLE_INFO dispatch via UIA GridPattern (GW-049).

        Reads table data using the UIA GridPattern and returns a plain dict
        based on the ``table_action`` kwarg.
        """
        max_rows = kwargs.get("max_rows", 100)
        max_columns = kwargs.get("max_columns", 50)
        table_action = kwargs.get("table_action", "info")
        row_idx = kwargs.get("row", 0)
        col_idx = kwargs.get("column", 0)

        try:
            grid = element.GetCurrentPattern(10026)  # UIA_GridPatternId
        except Exception as exc:
            raise ActionNotSupportedError(
                "Element does not support UIA GridPattern for table access"
            ) from exc

        try:
            row_count = int(grid.CurrentRowCount)
            column_count = int(grid.CurrentColumnCount)

            # Read column headers
            headers: list[str | None] = []
            try:
                for c in range(min(column_count, max_columns)):
                    try:
                        header_item = grid.GetItem(0, c)
                        if header_item:
                            header_name = header_item.GetCurrentPropertyValue(_UIA_NAME_PROPERTY_ID)
                            headers.append(str(header_name) if header_name else None)
                        else:
                            headers.append(None)
                    except Exception:
                        headers.append(None)
            except Exception:
                headers = [None] * min(column_count, max_columns)

            data_start_row = 1 if any(h is not None for h in headers) else 0

            def _read_cell(r: int, c: int) -> dict[str, Any]:
                try:
                    actual_r = r + data_start_row
                    cell = grid.GetItem(actual_r, c)
                    if cell:
                        value = cell.GetCurrentPropertyValue(_UIA_NAME_PROPERTY_ID)
                        return {"row": r, "column": c, "value": str(value) if value else None}
                except Exception:
                    pass
                return {"row": r, "column": c, "value": None}

            if table_action == "info":
                effective_rows = min(row_count - data_start_row, max_rows)
                result_rows: list[list[dict[str, Any]]] = []
                for r in range(effective_rows):
                    row_cells = [_read_cell(r, c) for c in range(min(column_count, max_columns))]
                    result_rows.append(row_cells)
                return {
                    "row_count": row_count - data_start_row,
                    "column_count": column_count,
                    "headers": headers[:max_columns],
                    "rows": result_rows,
                }
            elif table_action == "read_cell":
                return _read_cell(row_idx, col_idx)
            elif table_action == "read_row":
                cells = [_read_cell(row_idx, c) for c in range(min(column_count, max_columns))]
                return {"row": row_idx, "cells": cells}
            elif table_action == "read_column":
                effective_rows = min(row_count - data_start_row, max_rows)
                col_cells = [_read_cell(r, col_idx) for r in range(effective_rows)]
                header = headers[col_idx] if col_idx < len(headers) else None
                return {"column": col_idx, "header": header, "cells": col_cells}
            else:
                raise ActionNotSupportedError(f"Unknown table_action: {table_action!r}")
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(
                f"Failed to read table data via GridPattern: {exc}"
            ) from exc

    def is_valid(self, element: NativeHandle) -> bool:
        """Check whether a native element reference is still valid.

        Uses a lightweight property-access probe on the underlying COM
        ``IUIAutomationElement`` to detect stale handles.  For HWND-integer
        handles (produced by ``focus_window``), delegates to the Win32
        ``IsWindow`` API via ``ctypes``.

        This method must **never** raise — the tool layer calls it outside
        any ``try / except`` block, so any exception would propagate as an
        unhandled MCP error.

        Args:
            element: Opaque native element handle.  May be a COM
                ``IUIAutomationElement`` pointer (from ``list_windows`` /
                ``find_elements``) or a bare HWND ``int`` (from
                ``focus_window``).

        Returns:
            ``True`` if the element still exists in the accessibility tree,
            ``False`` otherwise (including when the backend is disposed).
        """
        if self._disposed:
            return False

        # HWND integer handles — use Win32 IsWindow API.
        if isinstance(element, int):
            try:
                import ctypes

                user32 = ctypes.windll.user32  # type: ignore[attr-defined]
                return bool(user32.IsWindow(element))  # type: ignore[attr-defined]
            except Exception:
                return False

        # COM IUIAutomationElement — probe with a cheap property read.
        try:
            element.GetCurrentPropertyValue(_UIA_PROCESS_ID_PROPERTY_ID)
            return True
        except Exception:
            return False

    def clipboard_read(self) -> str:
        """Read text content from the system clipboard via ctypes Win32 API.

        Uses the Win32 clipboard API (``OpenClipboard``, ``GetClipboardData``,
        ``CloseClipboard``) via ``ctypes`` to read ``CF_UNICODETEXT`` content.

        Raises:
            BackendUnavailableError: If the clipboard cannot be opened or
                does not contain text.
        """
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        # Declare restype for 64-bit handle-returning Win32 API functions.
        # Without these, ctypes defaults to c_int (32-bit) which truncates
        # HANDLE values on 64-bit Windows, causing silent data loss or
        # spurious "backend_unavailable" errors.
        kernel32.GlobalLock.restype = ctypes.c_void_p
        user32.GetClipboardData.restype = ctypes.c_void_p

        cf_unicode_text = 13  # Win32 CF_UNICODETEXT format

        if not user32.OpenClipboard(0):  # type: ignore[attr-defined]
            raise BackendUnavailableError("Failed to open the Windows clipboard")

        try:
            handle = user32.GetClipboardData(cf_unicode_text)  # type: ignore[attr-defined]
            if not handle:
                # No text data on the clipboard — return empty string
                return ""

            # Lock the handle to get a pointer to the Unicode text
            ptr = kernel32.GlobalLock(handle)  # type: ignore[attr-defined]
            if not ptr:
                return ""

            try:
                # Read the Unicode string (null-terminated)
                return ctypes.c_wchar_p(ptr).value or ""
            finally:
                kernel32.GlobalUnlock(handle)  # type: ignore[attr-defined]
        finally:
            user32.CloseClipboard()  # type: ignore[attr-defined]

    def clipboard_write(self, text: str) -> None:
        """Write text to the system clipboard using Win32 ctypes.

        Opens the clipboard, clears it, sets Unicode text data via
        ``SetClipboardData``, and closes the clipboard.

        Args:
            text: The text string to place on the OS clipboard.

        Raises:
            BackendUnavailableError: If the clipboard cannot be opened or
                the operation fails.
        """
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]

        # Declare restype for 64-bit handle-returning Win32 API functions.
        # Without these, ctypes defaults to c_int (32-bit) which truncates
        # HANDLE values on 64-bit Windows, causing spurious
        # "backend_unavailable" errors.
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalFree.restype = ctypes.c_void_p
        user32.SetClipboardData.restype = ctypes.c_void_p

        # Allocate and copy text to global memory
        text_bytes = text.encode("utf-16-le") + b"\x00\x00"
        gmem_moveable = 0x0002  # GMEM_MOVEABLE
        h_mem = kernel32.GlobalAlloc(gmem_moveable, len(text_bytes))
        if not h_mem:
            raise BackendUnavailableError("Failed to allocate clipboard memory")

        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            kernel32.GlobalFree(h_mem)
            raise BackendUnavailableError("Failed to lock clipboard memory")

        try:
            ctypes.memmove(ptr, text_bytes, len(text_bytes))
        finally:
            kernel32.GlobalUnlock(h_mem)

        # Open, set, and close clipboard
        if not user32.OpenClipboard(0):
            kernel32.GlobalFree(h_mem)
            raise BackendUnavailableError("Failed to open clipboard")

        try:
            user32.EmptyClipboard()
            cf_unicode_text = 13  # Win32 CF_UNICODETEXT format
            if not user32.SetClipboardData(cf_unicode_text, h_mem):
                raise BackendUnavailableError("Failed to set clipboard data")
        finally:
            user32.CloseClipboard()

    def dispose(self) -> None:
        """Release all resources held by this backend.

        Releases the IUIAutomation COM reference, uninitializes the COM
        library, and marks the backend as disposed.  Safe to call multiple
        times (idempotent).

        Called when the MCP server shuts down.
        """
        if self._disposed:
            return
        self._uia = None
        if self._com_initialized:
            try:
                import comtypes

                comtypes.CoUninitialize()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._com_initialized = False
        self._disposed = True

    # -- Window state management (GW-055) ------------------------------------

    # Win32 ShowWindow constants
    _SW_MINIMIZE = 6
    _SW_MAXIMIZE = 3
    _SW_RESTORE = 9

    # UIA pattern IDs for virtual list support (GW-052)
    _UIA_ITEM_CONTAINER_PATTERN_ID = 10019  # UIA_ItemContainerPatternId
    _UIA_VIRTUALIZED_ITEM_PATTERN_ID = 10020  # UIA_VirtualizedItemPatternId

    def scroll_to_item(
        self,
        container: NativeHandle,
        *,
        item_name: str | None = None,
        item_index: int | None = None,
        max_retries: int = 10,
    ) -> NativeHandle | None:
        """Scroll a virtualized list to bring an item into view (GW-052).

        Uses UIA ``ItemContainerPattern.FindItemByProperty`` to locate the
        item by name, then ``VirtualizedItemPattern.Realize()`` to materialize
        it.  Falls back to a scroll-and-probe approach when the container
        does not support ``ItemContainerPattern``.

        Args:
            container: Native handle for the list/container element.
            item_name: Accessible name of the target item (case-insensitive
                substring match).
            item_index: Zero-based index of the target item.
            max_retries: Max scroll iterations for fallback.

        Returns:
            Native handle for the found item, or ``None``.

        Raises:
            ElementNotFoundError: If the container is invalid.
            ActionNotSupportedError: If the container does not support
                virtualization patterns or scrolling.
        """
        if self._disposed:
            raise StaleElementReferenceError("WindowsBackend has been disposed")

        element = self._unwrap_element(container)

        if item_name is None and item_index is None:
            raise ActionNotSupportedError("scroll_to_item requires either item_name or item_index")

        # --- Primary path: ItemContainerPattern.FindItemByProperty ---
        try:
            ic_pattern = element.GetCurrentPattern(self._UIA_ITEM_CONTAINER_PATTERN_ID)
            if ic_pattern is not None:
                # UIA_PropertyId_UIA_NamePropertyId = 30005
                found = ic_pattern.FindItemByProperty(0, 30005, item_name)
                if found is not None:
                    # Realize the virtualized item
                    try:
                        vi_pattern = found.GetCurrentPattern(self._UIA_VIRTUALIZED_ITEM_PATTERN_ID)
                        if vi_pattern is not None:
                            vi_pattern.Realize()
                    except Exception:
                        pass
                    return NativeHandle(found)
        except ActionNotSupportedError:
            raise
        except Exception:
            logger.debug("ItemContainerPattern lookup failed, trying scroll fallback")

        # --- Fallback: scroll-and-probe approach ---
        # Try using ScrollPattern to scroll down and find the item
        for attempt in range(max_retries):
            # Walk visible children looking for the target
            walker = self._get_tree_walker()
            try:
                child = walker.GetFirstChildElement(element)
                visible_index = 0
                while child is not None:
                    try:
                        child_name = child.CurrentName
                        if (
                            item_name is not None
                            and child_name is not None
                            and item_name.lower() in child_name.lower()
                        ):
                            # Found by name
                            try:
                                vi_pattern = child.GetCurrentPattern(
                                    self._UIA_VIRTUALIZED_ITEM_PATTERN_ID
                                )
                                if vi_pattern is not None:
                                    vi_pattern.Realize()
                            except Exception:
                                pass
                            return NativeHandle(child)
                        if item_index is not None and visible_index == item_index:
                            try:
                                vi_pattern = child.GetCurrentPattern(
                                    self._UIA_VIRTUALIZED_ITEM_PATTERN_ID
                                )
                                if vi_pattern is not None:
                                    vi_pattern.Realize()
                            except Exception:
                                pass
                            return NativeHandle(child)
                        visible_index += 1
                    except Exception:
                        pass
                    child = walker.GetNextSiblingElement(child)
            except Exception:
                logger.debug("Error scanning visible children, attempt %d", attempt)

            # Scroll down to reveal more items
            try:
                scroll_pattern = element.GetCurrentPattern(_UIA_SCROLL_PATTERN_ID)
                if scroll_pattern is not None:
                    # ScrollAmount_LargeIncrement = 4
                    scroll_pattern.ScrollVertical(4)
                else:
                    break  # No scroll capability, can't continue
            except Exception:
                logger.debug("Scroll failed on attempt %d", attempt)
                break

        return None

    def _require_hwnd(self, window: NativeHandle) -> int:
        """Extract HWND from a window handle, raising on invalid handles.

        Args:
            window: Opaque native window handle (HWND integer or COM element).

        Returns:
            The integer HWND.

        Raises:
            WindowNotFoundError: If the handle does not reference a valid window.
        """
        import ctypes

        try:
            hwnd = self._extract_hwnd(window)
        except TypeError as exc:
            raise WindowNotFoundError(f"Window handle is not a valid HWND: {exc}") from exc

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if not user32.IsWindow(hwnd):  # type: ignore[attr-defined]
            raise WindowNotFoundError(f"Window handle {hwnd:#x} is not a valid window")
        return hwnd

    def minimize_window(self, window: NativeHandle) -> None:
        """Minimize a window using the Win32 ShowWindow API.

        Args:
            window: Opaque native window handle.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        import ctypes

        hwnd = self._require_hwnd(window)
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.ShowWindow(hwnd, self._SW_MINIMIZE)  # type: ignore[attr-defined]

    def maximize_window(self, window: NativeHandle) -> None:
        """Maximize a window using the Win32 ShowWindow API.

        Args:
            window: Opaque native window handle.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        import ctypes

        hwnd = self._require_hwnd(window)
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.ShowWindow(hwnd, self._SW_MAXIMIZE)  # type: ignore[attr-defined]

    def restore_window(self, window: NativeHandle) -> None:
        """Restore a window from minimized/maximized state.

        Args:
            window: Opaque native window handle.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        import ctypes

        hwnd = self._require_hwnd(window)
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.ShowWindow(hwnd, self._SW_RESTORE)  # type: ignore[attr-defined]

    def move_window(self, window: NativeHandle, x: int, y: int) -> None:
        """Move a window to the given screen coordinates.

        Uses the Win32 ``MoveWindow`` API.  Preserves current width/height.

        Args:
            window: Opaque native window handle.
            x: Target left-edge X coordinate in screen pixels.
            y: Target top-edge Y coordinate in screen pixels.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        import ctypes

        hwnd = self._require_hwnd(window)
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        rect = ctypes.wintypes.RECT()  # type: ignore[attr-defined]
        user32.GetWindowRect(hwnd, ctypes.byref(rect))  # type: ignore[attr-defined]
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        user32.MoveWindow(  # type: ignore[attr-defined]
            hwnd, x, y, width, height, True
        )

    def resize_window(self, window: NativeHandle, width: int, height: int) -> None:
        """Resize a window using the Win32 MoveWindow API.

        Preserves the current position.

        Args:
            window: Opaque native window handle.
            width: Target width in pixels.
            height: Target height in pixels.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        import ctypes

        hwnd = self._require_hwnd(window)
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        rect = ctypes.wintypes.RECT()  # type: ignore[attr-defined]
        user32.GetWindowRect(hwnd, ctypes.byref(rect))  # type: ignore[attr-defined]
        user32.MoveWindow(  # type: ignore[attr-defined]
            hwnd, rect.left, rect.top, width, height, True
        )


# ---------------------------------------------------------------------------
# Module-level helpers for UIA property extraction
# ---------------------------------------------------------------------------

# UIA pattern / property IDs — see: https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpattern-ids
_UIA_VALUE_PATTERN_ID: int = 10002

# Map of UIA ControlType integer IDs to their string names.
# See: https://docs.microsoft.com/en-us/windows/win32/winauto/uiauto-controltype-ids
_UIA_CONTROL_TYPE_MAP: dict[int, str] = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
}


def _control_type_id_to_name(control_type_id: int) -> str:
    """Convert a UIA ControlType integer ID to its string name.

    Args:
        control_type_id: The UIA ControlType integer value.

    Returns:
        The string name (e.g. ``"Button"``, ``"Edit"``), or ``"Custom"``
        for unknown IDs.
    """
    return _UIA_CONTROL_TYPE_MAP.get(control_type_id, "Custom")


# Map of UIA pattern IDs to pattern names used for action resolution.
# Pattern IDs are the integer identifiers for UIA automation patterns.
_UIA_PATTERN_MAP: list[tuple[int, str]] = [
    (10000, "InvokePattern"),
    (10015, "TogglePattern"),
    (10005, "ExpandCollapsePattern"),
    (10004, "ScrollPattern"),
    (10006, "SelectionPattern"),
    (10010, "SelectionItemPattern"),
    (_UIA_VALUE_PATTERN_ID, "ValuePattern"),
    (10014, "TextPattern"),
    (10003, "RangeValuePattern"),
]


def _read_state(
    element: Any,
    state_key: str,
    attr_name: str,
    transform: type[bool],
    states: dict[str, Any],
) -> None:
    """Read a boolean state property from a UIA element and resolve it.

    Args:
        element: The ``IUIAutomationElement`` to read from.
        state_key: The key for :data:`STATE_MAP` (e.g. ``"IsEnabled"``).
        attr_name: The COM attribute name (e.g. ``"CurrentIsEnabled"``).
        transform: Type constructor to apply (typically ``bool``).
        states: Dict to accumulate resolved state key-value pairs into.
    """
    try:
        raw_value = getattr(element, attr_name)
        resolved = resolve_state("windows", state_key, raw_value)
        if resolved:
            states[resolved[0]] = resolved[1]
    except Exception:
        pass
