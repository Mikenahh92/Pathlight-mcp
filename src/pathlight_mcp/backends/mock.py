"""MockBackend — in-memory test double for DesktopBackend (architecture v2 §5).

Provides programmable implementations of all 16 canonical synchronous methods
so that unit tests can exercise the MCP tool layer without a real platform
accessibility backend.

Usage::

    from pathlight_mcp.backends import MockBackend

    backend = (
        MockBackend()
        .add_window(title="Notepad", app="notepad.exe")
        .add_element(role="button", name="Save", parent=w)
    )

    windows = backend.list_windows()
    info = backend.get_window_info(windows[0])
    elements = backend.find_elements(windows[0], role="button")
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Self
from uuid import uuid4

from pathlight_mcp.backends.base import DesktopBackend
from pathlight_mcp.backends.types import (
    DesktopAction,
    ElementBounds,
    ElementState,
    NativeHandle,
)
from pathlight_mcp.errors import (
    ElementNotFoundError,
    WindowNotFoundError,
)

# -- Internal data classes --------------------------------------------------


@dataclass(slots=True)
class _MockWindow:
    """Internal representation of a mock window."""

    handle: NativeHandle
    title: str
    app: str
    focused: bool = False
    bounds: ElementBounds = field(
        default_factory=lambda: ElementBounds(x=0, y=0, width=800, height=600),
    )
    disposed: bool = False
    minimized: bool = False
    maximized: bool = False


@dataclass(slots=True)
class _MockElement:
    """Internal representation of a mock element."""

    handle: NativeHandle
    role: str
    name: str | None = None
    value: str | None = None
    window_handle: NativeHandle | None = None
    parent_handle: NativeHandle | None = None
    valid: bool = True
    states: ElementState = field(default_factory=ElementState)
    actions: list[DesktopAction] = field(default_factory=list)
    bounds: ElementBounds = field(
        default_factory=lambda: ElementBounds(x=0, y=0, width=100, height=30),
    )
    # Table data for get_table_info support (GW-049)
    table_headers: list[str | None] | None = None
    table_rows: list[list[str | None]] | None = None


# -- Unique handle factory --------------------------------------------------


def _new_handle() -> NativeHandle:
    """Return a unique opaque handle string."""
    return NativeHandle(uuid4().hex)


# -- Tree-building helpers --------------------------------------------------


def _element_to_dict(e: _MockElement) -> dict[str, Any]:
    """Convert an internal _MockElement to a DesktopElement-style dict (no children)."""
    return {
        "ref": e.handle,
        "role": e.role,
        "name": e.name,
        "states": asdict(e.states),
        "bounds": {
            "x": e.bounds.x,
            "y": e.bounds.y,
            "width": e.bounds.width,
            "height": e.bounds.height,
        },
        "actions": [a.value for a in e.actions],
        "children": [],
    }


def _build_tree(
    elements: list[_MockElement],
    parent_handle: NativeHandle | None,
    depth: int,
    max_depth: int,
    counter: list[int],
    max_nodes: int,
) -> list[dict[str, Any]]:
    """Recursively build a tree of element dicts respecting depth and node limits.

    Children at *depth* 1 are the direct children of the window root.
    ``max_depth=0`` means no children at all (root only).
    """
    if depth >= max_depth or counter[0] >= max_nodes:
        return []

    children: list[dict[str, Any]] = []
    for e in elements:
        if not e.valid or e.parent_handle != parent_handle:
            continue
        if counter[0] >= max_nodes:
            break
        counter[0] += 1
        node = _element_to_dict(e)
        node["children"] = _build_tree(elements, e.handle, depth + 1, max_depth, counter, max_nodes)
        children.append(node)
    return children


# -- MockBackend ------------------------------------------------------------


class MockBackend(DesktopBackend):
    """In-memory test double for :class:`DesktopBackend`.

    Stores windows and elements in plain dictionaries.  All methods are
    synchronous and complete immediately.  Supports simulating disposed
    (invalid) elements and configurable error conditions for testing.

    Builder methods (``add_window``, ``add_element``) return ``self`` for
    fluent chaining per architecture v2 §5.3.
    """

    def __init__(self) -> None:
        self._windows: dict[NativeHandle, _MockWindow] = {}
        self._elements: dict[NativeHandle, _MockElement] = {}
        self._disposed: bool = False
        self._action_log: list[dict[str, Any]] = []
        self._last_window_handle: NativeHandle | None = None
        self._clipboard_content: str = ""

    # -- Builder API (seed helpers, fluent) ----------------------------------

    def add_window(
        self,
        title: str = "",
        app: str = "",
        focused: bool = False,
        bounds: ElementBounds | None = None,
    ) -> Self:
        """Register a mock window and return ``self`` for chaining."""
        handle = _new_handle()
        self._windows[handle] = _MockWindow(
            handle=handle,
            title=title,
            app=app,
            focused=focused,
            bounds=bounds or ElementBounds(x=0, y=0, width=800, height=600),
        )
        self._last_window_handle = handle
        return self

    def add_element(
        self,
        role: str,
        name: str | None = None,
        value: str | None = None,
        parent: NativeHandle | None = None,
        bounds: ElementBounds | None = None,
        states: ElementState | None = None,
        actions: list[DesktopAction] | None = None,
    ) -> Self:
        """Register a mock element and return ``self`` for chaining.

        Args:
            role: Normalized accessibility role.
            name: Accessible name.
            value: Current value.
            parent: Window handle this element belongs to (or ``None``).
            bounds: Bounding rectangle, or a default.
            states: Element state flags, or defaults.
            actions: Supported actions list, or empty.
        """
        handle = _new_handle()
        self._elements[handle] = _MockElement(
            handle=handle,
            role=role,
            name=name,
            value=value,
            window_handle=parent,
            parent_handle=None,
            valid=True,
            states=states or ElementState(),
            actions=actions or [],
            bounds=bounds or ElementBounds(x=0, y=0, width=100, height=30),
        )
        return self

    def invalidate(self, element: NativeHandle) -> None:
        """Mark an element as invalid (simulates a stale reference)."""
        if element in self._elements:
            self._elements[element].valid = False

    def set_clipboard(self, text: str) -> Self:
        """Set the mock clipboard content and return ``self`` for chaining.

        Fluent builder companion to :meth:`clipboard_write`.  Allows test
        setup of initial clipboard state in a chained builder call.

        Args:
            text: The text to pre-populate the mock clipboard with.

        Returns:
            ``self`` for fluent chaining.
        """
        self._clipboard_content = text
        return self

    # -- Canonical methods ---------------------------------------------------
    def add_table(
        self,
        role: str = "table",
        name: str | None = None,
        headers: list[str | None] | None = None,
        rows: list[list[str | None]] | None = None,
        parent: NativeHandle | None = None,
    ) -> Self:
        """Register a mock table element with headers and row data.

        Args:
            role: Normalized accessibility role (default ``"table"``).
            name: Accessible name for the table element.
            headers: Column header strings.
            rows: Row data as list of lists of cell values.
            parent: Window handle this table belongs to.

        Returns:
            ``self`` for chaining.
        """
        handle = _new_handle()
        self._elements[handle] = _MockElement(
            handle=handle,
            role=role,
            name=name,
            value=None,
            window_handle=parent,
            parent_handle=None,
            valid=True,
            states=ElementState(),
            actions=[],
            bounds=ElementBounds(x=0, y=0, width=400, height=300),
            table_headers=headers,
            table_rows=rows,
        )
        return self

    # -- Canonical 8 methods -------------------------------------------------

    def list_windows(self) -> list[NativeHandle]:
        """Return handles for all registered mock windows."""
        return [h for h, w in self._windows.items() if not w.disposed]

    def get_window_info(self, window: NativeHandle) -> dict[str, Any]:
        """Return window metadata as a dict (architecture v2 §4.1)."""
        if window not in self._windows:
            raise WindowNotFoundError(f"Window handle {window!r} not found")
        w = self._windows[window]
        return {
            "title": w.title,
            "app_name": w.app,
            "focused": w.focused,
            "bounds": {
                "x": w.bounds.x,
                "y": w.bounds.y,
                "width": w.bounds.width,
                "height": w.bounds.height,
            },
        }

    def focus_window(self, window: NativeHandle) -> None:
        """Focus a mock window by handle."""
        if window not in self._windows:
            raise WindowNotFoundError(f"Window handle {window!r} not found")
        for w in self._windows.values():
            w.focused = False
        self._windows[window].focused = True

    def snapshot(
        self,
        window: NativeHandle,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """Return a tree dict of window elements (architecture v2 §4.1).

        Returns a dict matching the DesktopElement schema with nested children.
        Respects ``max_depth`` and ``max_nodes`` limits.
        """
        if window not in self._windows:
            raise WindowNotFoundError(f"Window handle {window!r} not found")
        w = self._windows[window]
        elements = [e for e in self._elements.values() if e.window_handle == window and e.valid]
        counter = [0]
        children = _build_tree(elements, None, 0, max_depth, counter, max_nodes)
        return {
            "ref": window,
            "role": "window",
            "name": w.title,
            "states": {"enabled": True, "focused": w.focused},
            "bounds": {
                "x": w.bounds.x,
                "y": w.bounds.y,
                "width": w.bounds.width,
                "height": w.bounds.height,
            },
            "actions": [],
            "children": children,
        }

    def find_elements(
        self,
        window: NativeHandle,
        role: str | None = None,
        name: str | None = None,
    ) -> list[NativeHandle]:
        """Find mock elements matching criteria within a window.

        Returns list of NativeHandle (not ElementState) per architecture v2 §4.1.
        """
        if window not in self._windows:
            raise WindowNotFoundError(f"Window handle {window!r} not found")
        results: list[NativeHandle] = []
        for e in self._elements.values():
            if e.window_handle != window or not e.valid:
                continue
            if role is not None and e.role != role:
                continue
            if name is not None and (e.name is None or name.lower() not in e.name.lower()):
                continue
            results.append(e.handle)
        return results

    def perform_action(
        self,
        handle: NativeHandle,
        action: DesktopAction,
        **kwargs: Any,
    ) -> Any:
        """Perform an action on a mock element or window.

        Parameter order is ``(handle, action, **kwargs)`` per architecture v2 §4.1.
        Accepts both element handles and window handles (for window-level actions
        like ``PRESS_KEY``).  Returns ``str`` when action is ``GET_TEXT``,
        ``dict`` when action is ``GET_TABLE_INFO``, otherwise ``None``.
        """
        # Window-level actions (e.g. press_key) target window handles
        if handle in self._windows:
            self._action_log.append({"action": action, "handle": handle, "kwargs": kwargs})
            return None

        if handle not in self._elements:
            raise ElementNotFoundError(f"Element handle {handle!r} not found")
        e = self._elements[handle]
        if not e.valid:
            from pathlight_mcp.errors import StaleElementReferenceError

            raise StaleElementReferenceError(f"Element handle {handle!r} is stale")
        self._action_log.append({"action": action, "handle": handle, "kwargs": kwargs})
        if action == DesktopAction.GET_TEXT:
            return e.value or ""
        if action == DesktopAction.GET_TABLE_INFO:
            return self._dispatch_table_info(e, **kwargs)
        if action == DesktopAction.SELECT:
            return None
        if action == DesktopAction.SELECT_ITEM:
            return None
        if action == DesktopAction.DESELECT_ITEM:
            return None
        if action == DesktopAction.ADD_TO_SELECTION:
            return None
        return None

    def _dispatch_table_info(self, e: _MockElement, **kwargs: Any) -> dict[str, Any]:
        """Handle GET_TABLE_INFO dispatch for a mock table element.

        Returns a plain dict response based on the ``table_action`` kwarg.
        """
        from pathlight_mcp.errors import ActionNotSupportedError

        if e.table_headers is None and e.table_rows is None:
            raise ActionNotSupportedError(
                f"Element '{e.handle!r}' does not support table/grid access"
            )

        table_action = kwargs.get("table_action", "info")
        max_rows = kwargs.get("max_rows", 100)
        max_columns = kwargs.get("max_columns", 50)
        row_idx = kwargs.get("row", 0)
        col_idx = kwargs.get("column", 0)

        headers = e.table_headers or []
        rows_raw = e.table_rows or []
        col_count = max(len(headers), max((len(r) for r in rows_raw), default=0))

        if table_action == "info":
            limited_rows = rows_raw[:max_rows]
            header_list = headers[:max_columns]
            result_rows: list[list[dict[str, Any]]] = []
            for r_idx, row in enumerate(limited_rows):
                cells: list[dict[str, Any]] = []
                for c_idx in range(min(len(row), max_columns)):
                    cells.append({"row": r_idx, "column": c_idx, "value": row[c_idx]})
                result_rows.append(cells)
            return {
                "row_count": len(rows_raw),
                "column_count": col_count,
                "headers": header_list,
                "rows": result_rows,
            }
        elif table_action == "read_cell":
            if row_idx >= len(rows_raw) or col_idx >= len(rows_raw[row_idx]):
                return {"row": row_idx, "column": col_idx, "value": None}
            return {
                "row": row_idx,
                "column": col_idx,
                "value": rows_raw[row_idx][col_idx],
            }
        elif table_action == "read_row":
            if row_idx >= len(rows_raw):
                return {"row": row_idx, "cells": []}
            row = rows_raw[row_idx]
            cells = [
                {"row": row_idx, "column": c, "value": row[c]}
                for c in range(min(len(row), max_columns))
            ]
            return {"row": row_idx, "cells": cells}
        elif table_action == "read_column":
            col_cells: list[dict[str, Any]] = []
            for r_idx in range(min(len(rows_raw), max_rows)):
                if col_idx < len(rows_raw[r_idx]):
                    col_cells.append(
                        {"row": r_idx, "column": col_idx, "value": rows_raw[r_idx][col_idx]}
                    )
            header = headers[col_idx] if col_idx < len(headers) else None
            return {"column": col_idx, "header": header, "cells": col_cells}
        else:
            raise ActionNotSupportedError(f"Unknown table_action: {table_action!r}")

    def get_element_info(self, handle: NativeHandle) -> dict[str, Any]:
        """Return element metadata as a dict."""
        if handle not in self._elements:
            raise ElementNotFoundError(f"Element handle {handle!r} not found")
        e = self._elements[handle]
        if not e.valid:
            from pathlight_mcp.errors import StaleElementReferenceError

            raise StaleElementReferenceError(f"Element handle {handle!r} is stale")
        return {
            "role": e.role,
            "name": e.name,
            "states": asdict(e.states),
            "actions": [a.value for a in e.actions],
        }

    def is_valid(self, element: NativeHandle) -> bool:
        """Check whether a mock element or window reference is still valid."""
        if element in self._windows:
            return True
        if element not in self._elements:
            return False
        return self._elements[element].valid

    def clipboard_read(self) -> str:
        """Read text content from the mock clipboard.

        Returns the current ``_clipboard_content`` for test assertions.
        """
        return self._clipboard_content

    # -- Window state management (GW-055) ------------------------------------

    def _require_window(self, window: NativeHandle) -> _MockWindow:
        """Resolve a window handle or raise WindowNotFoundError."""
        if window not in self._windows:
            raise WindowNotFoundError(f"Window handle {window!r} not found")
        return self._windows[window]

    def minimize_window(self, window: NativeHandle) -> None:
        """Minimize a mock window."""
        w = self._require_window(window)
        w.minimized = True
        w.maximized = False
        w.focused = False
        self._action_log.append({"action": "minimize_window", "handle": window})

    def maximize_window(self, window: NativeHandle) -> None:
        """Maximize a mock window."""
        w = self._require_window(window)
        w.maximized = True
        w.minimized = False
        self._action_log.append({"action": "maximize_window", "handle": window})

    def restore_window(self, window: NativeHandle) -> None:
        """Restore a mock window from minimized/maximized state."""
        w = self._require_window(window)
        w.minimized = False
        w.maximized = False
        self._action_log.append({"action": "restore_window", "handle": window})

    def move_window(self, window: NativeHandle, x: int, y: int) -> None:
        """Move a mock window to the given coordinates."""
        w = self._require_window(window)
        w.bounds = ElementBounds(x=x, y=y, width=w.bounds.width, height=w.bounds.height)
        self._action_log.append({"action": "move_window", "handle": window, "x": x, "y": y})

    def resize_window(self, window: NativeHandle, width: int, height: int) -> None:
        """Resize a mock window."""
        w = self._require_window(window)
        w.bounds = ElementBounds(x=w.bounds.x, y=w.bounds.y, width=width, height=height)
        self._action_log.append(
            {"action": "resize_window", "handle": window, "width": width, "height": height}
        )

    def clipboard_write(self, text: str) -> None:
        """Write text to the mock clipboard.

        Records the text in ``_clipboard_content`` for test assertions.
        """
        self._clipboard_content = text

    def scroll_to_item(
        self,
        container: NativeHandle,
        *,
        item_name: str | None = None,
        item_index: int | None = None,
        max_retries: int = 10,
    ) -> NativeHandle | None:
        """Scroll a mock virtualized list to find and return a target item (GW-052).

        Searches through all mock elements belonging to the same window as
        *container* for an element matching *item_name* or *item_index*.

        Returns:
            Native handle for the found item, or ``None``.

        Raises:
            ElementNotFoundError: If the container handle is invalid.
            ActionNotSupportedError: If neither item_name nor item_index is provided.
        """
        from pathlight_mcp.errors import ActionNotSupportedError, StaleElementReferenceError

        if container not in self._elements and container not in self._windows:
            raise ElementNotFoundError(f"Container handle {container!r} not found")

        if container in self._elements and not self._elements[container].valid:
            raise StaleElementReferenceError(f"Container handle {container!r} is stale")

        if item_name is None and item_index is None:
            raise ActionNotSupportedError("scroll_to_item requires either item_name or item_index")

        # Find the window for this container
        window_handle = None
        if container in self._elements:
            window_handle = self._elements[container].window_handle
        elif container in self._windows:
            window_handle = container

        # Search all elements in the same window
        candidates = [
            e
            for e in self._elements.values()
            if e.valid and e.window_handle == window_handle and e.handle != container
        ]

        for idx, elem in enumerate(candidates):
            if (
                item_name is not None
                and elem.name is not None
                and item_name.lower() in elem.name.lower()
            ):
                self._action_log.append(
                    {
                        "action": "scroll_to_item",
                        "container": container,
                        "found": elem.handle,
                        "match": "name",
                    }
                )
                return elem.handle
            if item_index is not None and idx == item_index:
                self._action_log.append(
                    {
                        "action": "scroll_to_item",
                        "container": container,
                        "found": elem.handle,
                        "match": "index",
                    }
                )
                return elem.handle

        return None

    def dispose(self) -> None:
        """Release all mock resources."""
        self._disposed = True
        self._windows.clear()
        self._elements.clear()

    def screenshot(self, window: NativeHandle) -> bytes:
        """Return a small dummy PNG image for testing.

        Produces a minimal valid 1×1 white PNG so tests can verify base64
        output without depending on ``mss`` or a display server.

        Args:
            window: Opaque native window handle.

        Returns:
            Raw PNG image bytes.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        if window not in self._windows:
            raise WindowNotFoundError(f"Window handle {window!r} not found")
        # Minimal 1×1 white PNG (67 bytes)
        return (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
            b"\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    # -- Inspection helpers for tests ----------------------------------------

    @property
    def action_log(self) -> list[dict[str, Any]]:
        """Recorded action calls."""
        return list(self._action_log)

    @property
    def is_disposed(self) -> bool:
        """Whether dispose has been called."""
        return self._disposed

    @property
    def last_window_handle(self) -> NativeHandle | None:
        """Handle of the most recently added window (for fluent chaining)."""
        return self._last_window_handle

    @property
    def clipboard_content(self) -> str:
        """Current content of the mock clipboard."""
        return self._clipboard_content
