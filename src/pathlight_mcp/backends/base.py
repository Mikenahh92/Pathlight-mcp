"""DesktopBackend — abstract contract for platform accessibility backends.

Defines the 16 canonical synchronous methods that every platform backend
must implement (architecture v2 §4.1).  Concrete backends (Windows UIA,
macOS AX, Linux AT-SPI) inherit from :class:`DesktopBackend` and translate
native accessibility APIs into the cross-platform types defined in
:mod:`pathlight_mcp.backends.types`.
"""

from abc import ABC, abstractmethod
from typing import Any

from pathlight_mcp.backends.types import (
    DesktopAction,
    NativeHandle,
)

__all__ = [
    "DesktopBackend",
]


class DesktopBackend(ABC):
    """Abstract base class for platform accessibility backends.

    Every method is synchronous.  The 16 methods form the complete contract
    that the MCP tool layer calls.  Subclasses must implement all of them.

    Method mapping (architecture v2 §4.1):
        list_windows  → desktop.list_windows
        get_window_info → desktop.get_window_info
        focus_window  → desktop.focus_window
        minimize_window → desktop.manage_window (action=minimize)
        maximize_window → desktop.manage_window (action=maximize)
        restore_window → desktop.manage_window (action=restore)
        move_window   → desktop.manage_window (action=move)
        resize_window → desktop.manage_window (action=resize)
        snapshot      → desktop.snapshot
        find_elements → desktop.find_elements
        perform_action → desktop.perform_action (incl. GET_TABLE_INFO)
        get_element_info → element metadata
        is_valid      → internal staleness check
        clipboard_read → desktop.clipboard_read
        clipboard_write → desktop.clipboard_write
        dispose       → resource cleanup
    """

    @abstractmethod
    def list_windows(self) -> list[NativeHandle]:
        """List all visible top-level windows.

        Returns:
            List of opaque native window handles.

        Maps to: ``desktop.list_windows`` (PRD §6.1).
        """

    @abstractmethod
    def get_window_info(self, window: NativeHandle) -> dict[str, Any]:
        """Return window metadata as a dict (architecture v2 §4.1).

        Args:
            window: Opaque native window handle from :meth:`list_windows`.

        Returns:
            Dict with keys ``title`` (str), ``app_name`` (str),
            ``focused`` (bool), ``bounds`` (ElementBounds | None).

        Maps to: ``desktop.get_window_info``.
        """

    @abstractmethod
    def focus_window(self, window: NativeHandle) -> None:
        """Bring a window to the foreground.

        Args:
            window: Opaque native window handle.

        Raises:
            WindowNotFoundError: If the handle is invalid.

        Maps to: ``desktop.focus_window`` (PRD §6.2).
        """

    @abstractmethod
    def snapshot(
        self,
        window: NativeHandle,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """Return an accessibility snapshot as a tree dict (architecture v2 §4.1).

        Produces a concise, depth-limited view of the accessibility tree
        suitable for LLM consumption (PRD §5.3).

        Args:
            window: Opaque native window handle.
            max_depth: Maximum tree depth to traverse (default 4).
            max_nodes: Maximum number of nodes to include (default 500).

        Returns:
            Dict matching the DesktopElement schema with keys:
            ``ref``, ``role``, ``name``, ``states``, ``bounds``, ``actions``,
            ``children`` (nested list of child dicts).

        Maps to: ``desktop.snapshot`` (PRD §6.3).
        """

    @abstractmethod
    def find_elements(
        self,
        window: NativeHandle,
        role: str | None = None,
        name: str | None = None,
    ) -> list[NativeHandle]:
        """Find elements matching criteria within a window.

        Args:
            window: Opaque native window handle.
            role: Normalized role to match (e.g. ``"button"``).
            name: Accessible name to match (case-insensitive substring).

        Returns:
            List of matching opaque native element handles.

        Maps to: ``desktop.find_elements`` (PRD §6.5).
        """

    @abstractmethod
    def perform_action(
        self,
        handle: NativeHandle,
        action: DesktopAction,
        **kwargs: Any,
    ) -> Any:
        """Perform an action on an element (architecture v2 §4.1).

        Dispatches to the appropriate native action pattern based on
        the :class:`DesktopAction` value.

        Args:
            handle: Opaque native element handle (first positional arg).
            action: The action to perform.
            **kwargs: Action-specific parameters.

        Returns:
            ``str`` when action is ``GET_TEXT``, otherwise ``None``.

        Raises:
            ActionNotSupportedError: If the action is not available.

        Maps to: ``desktop.perform_action`` (PRD 6.6-6.12).
        """

    @abstractmethod
    def get_element_info(self, handle: NativeHandle) -> dict[str, Any]:
        """Return element metadata as a dict.

        Args:
            handle: Opaque native element handle.

        Returns:
            Dict with keys ``role`` (str), ``name`` (str | None),
            ``states`` (dict of state flags).

        Raises:
            ElementNotFoundError: If the handle is not known.
        """

    @abstractmethod
    def clipboard_read(self) -> str:
        """Read text content from the system clipboard.

        Returns:
            The current text content of the OS clipboard as a string.

        Raises:
            BackendUnavailableError: If the clipboard cannot be accessed.

        Maps to: ``desktop.clipboard_read`` (PRD §6).
        """

    @abstractmethod
    def is_valid(self, element: NativeHandle) -> bool:
        """Check whether a native element reference is still valid.

        Args:
            element: Opaque native element handle.

        Returns:
            ``True`` if the element still exists in the accessibility tree.
        """

    @abstractmethod
    def scroll_to_item(
        self,
        container: NativeHandle,
        *,
        item_name: str | None = None,
        item_index: int | None = None,
        max_retries: int = 10,
    ) -> NativeHandle | None:
        """Scroll a virtualized list container to bring a target item into view.

        On Windows, uses UIA ``ItemContainerPattern.FindItemByProperty``
        followed by ``VirtualizedItemPattern.Realize()`` to materialize
        off-screen items.  On Linux, uses a best-effort scroll-and-retry
        approach.

        Args:
            container: Opaque native handle for the list/container element.
            item_name: Accessible name of the target item (case-insensitive
                substring match).
            item_index: Zero-based index of the target item within the
                container.
            max_retries: Maximum scroll iterations before giving up
                (Linux best-effort, default 10).

        Returns:
            A native handle for the now-visible item, or ``None`` if the
            item could not be found.

        Raises:
            ElementNotFoundError: If the container handle is invalid.
            ActionNotSupportedError: If the container does not support
                virtualization or scrolling.
        """

    # -- Window state management (GW-055) ------------------------------------

    @abstractmethod
    def minimize_window(self, window: NativeHandle) -> None:
        """Minimize a window to the taskbar / dock.

        Args:
            window: Opaque native window handle.

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If the platform cannot minimize.
        """

    @abstractmethod
    def maximize_window(self, window: NativeHandle) -> None:
        """Maximize a window to fill the screen.

        Args:
            window: Opaque native window handle.

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If the platform cannot maximize.
        """

    @abstractmethod
    def restore_window(self, window: NativeHandle) -> None:
        """Restore a window from minimized/maximized state.

        Args:
            window: Opaque native window handle.

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If the platform cannot restore.
        """

    @abstractmethod
    def move_window(self, window: NativeHandle, x: int, y: int) -> None:
        """Move a window to the given screen coordinates.

        Args:
            window: Opaque native window handle.
            x: Target left-edge X coordinate in screen pixels.
            y: Target top-edge Y coordinate in screen pixels.

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If the platform cannot move windows.
        """

    @abstractmethod
    def resize_window(self, window: NativeHandle, width: int, height: int) -> None:
        """Resize a window.

        Args:
            window: Opaque native window handle.
            width: Target width in pixels.
            height: Target height in pixels.

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If the platform cannot resize windows.
        """

    @abstractmethod
    def clipboard_write(self, text: str) -> None:
        """Write text to the system clipboard.

        Args:
            text: The text string to place on the OS clipboard.

        Raises:
            BackendUnavailableError: If the clipboard cannot be accessed.

        Maps to: ``desktop.clipboard_write`` (PRD §6).
        """

    @abstractmethod
    def dispose(self) -> None:
        """Release all resources held by this backend.

        Called when the MCP server shuts down.  Should clean up event
        subscriptions, COM references, and other platform handles.
        """
