"""BackendRouter — transparent routing layer across multiple backends (GW-097).

Holds a native backend (Windows or Linux) and an optional WebBackend,
multiplexing requests to the correct backend based on window handle origin.

From the tool layer's perspective, a :class:`BackendRouter` is a single
:class:`~guidewire.backends.base.DesktopBackend` — tools never know which
concrete backend serves a given request.

Routing strategy
----------------
- **Window handles** produced by :meth:`list_windows` are tagged with the
  originating backend.  Every method that receives a window handle resolves
  it back to the owning backend and delegates.
- **Element handles** produced by ``snapshot`` / ``find_elements`` are similarly
  tagged, so ``perform_action``, ``get_element_info``, ``is_valid``, etc. all
  route correctly.
- **Global operations** (``clipboard_read``, ``clipboard_write``) always
  delegate to the native backend when available, since these are OS-level
  concerns.
- **``list_windows``** merges windows from all active backends.
- **``dispose``** disposes all backends.

Tagged handles
~~~~~~~~~~~~~~
:class:`TaggedHandle` wraps a real native handle together with a backend
identifier (``"native"`` or ``"web"``).  Because :data:`NativeHandle` is a
``NewType("NativeHandle", Any)``, a :class:`TaggedHandle` instance is also a
valid ``NativeHandle`` — no type gymnastics required.
"""

from __future__ import annotations

import logging
from typing import Any

from guidewire.backends.base import DesktopBackend
from guidewire.backends.types import DesktopAction, NativeHandle
from guidewire.errors import (
    BackendUnavailableError,
    WindowNotFoundError,
)

__all__ = ["BackendRouter", "TaggedHandle"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tagged handle
# ---------------------------------------------------------------------------


class TaggedHandle:
    """Opaque handle wrapper that remembers which backend owns it.

    This is intentionally **not** a dataclass or NamedTuple — it must be
    small, fast to construct, and usable anywhere a ``NativeHandle`` is
    expected (since ``NativeHandle = NewType("NativeHandle", Any)``).

    Attributes:
        inner: The real native handle from the owning backend.
        backend_id: ``"native"`` or ``"web"``.
    """

    __slots__ = ("backend_id", "inner")

    def __init__(self, inner: Any, backend_id: str) -> None:
        self.inner = inner
        self.backend_id = backend_id

    def __repr__(self) -> str:
        return f"TaggedHandle({self.inner!r}, {self.backend_id!r})"


def _tag(handle: Any, backend_id: str) -> NativeHandle:
    """Wrap *handle* in a :class:`TaggedHandle`.

    If *handle* is already a :class:`TaggedHandle`, return it unchanged.
    """
    if isinstance(handle, TaggedHandle):
        return NativeHandle(handle)  # type: ignore[call-arg]
    return NativeHandle(TaggedHandle(handle, backend_id))  # type: ignore[call-arg]


def _untag(handle: NativeHandle) -> tuple[Any, str]:
    """Extract ``(inner_handle, backend_id)`` from a possibly-tagged handle.

    If the handle is not a :class:`TaggedHandle`, returns ``(handle, "native")``
    as a safe default (backward compatibility with untagged handles).
    """
    if isinstance(handle, TaggedHandle):
        return handle.inner, handle.backend_id
    return handle, "native"


# ---------------------------------------------------------------------------
# BackendRouter
# ---------------------------------------------------------------------------


class BackendRouter(DesktopBackend):
    """Transparent routing backend that delegates to native or web backends.

    Implements the full :class:`~guidewire.backends.base.DesktopBackend`
    interface.  Each method resolves the correct backend from the incoming
    handle and forwards the call.

    Args:
        native: The platform-native backend (Windows or Linux).
        web: Optional :class:`~guidewire.backends.web.WebBackend`.
    """

    def __init__(
        self,
        native: DesktopBackend,
        web: DesktopBackend | None = None,
    ) -> None:
        self._native = native
        self._web = web
        # Map backend_id → backend instance for fast lookup
        self._backends: dict[str, DesktopBackend] = {"native": native}
        if web is not None:
            self._backends["web"] = web

    # -- Public helpers -------------------------------------------------------

    @property
    def native(self) -> DesktopBackend:
        """The platform-native backend."""
        return self._native

    @property
    def web(self) -> DesktopBackend | None:
        """The web backend, or ``None`` if not configured."""
        return self._web

    def backend_for(self, handle: NativeHandle) -> DesktopBackend:
        """Return the backend that owns *handle*.

        Args:
            handle: A possibly-tagged native handle.

        Returns:
            The owning :class:`DesktopBackend`.

        Raises:
            WindowNotFoundError: If the backend_id is unknown.
        """
        _, backend_id = _untag(handle)
        backend = self._backends.get(backend_id)
        if backend is None:
            raise WindowNotFoundError(f"No backend registered for id {backend_id!r}")
        return backend

    # -- DesktopBackend interface ---------------------------------------------

    def list_windows(self) -> list[NativeHandle]:
        """List windows from all active backends.

        Each returned handle is tagged with its originating backend.
        """
        all_handles: list[NativeHandle] = []

        for backend_id, backend in self._backends.items():
            try:
                handles = backend.list_windows()
            except (BackendUnavailableError, Exception) as exc:
                logger.debug(
                    "list_windows failed for backend %s: %s",
                    backend_id,
                    exc,
                )
                continue
            for h in handles:
                all_handles.append(_tag(h, backend_id))

        return all_handles

    def get_window_info(self, window: NativeHandle) -> dict[str, Any]:
        """Delegate get_window_info to the owning backend."""
        inner, backend_id = _untag(window)
        backend = self._require_backend(backend_id)
        info = backend.get_window_info(inner)
        # Annotate with backend_id so callers can distinguish source
        info["_backend_id"] = backend_id
        return info

    def focus_window(self, window: NativeHandle) -> None:
        """Delegate focus_window to the owning backend."""
        inner, backend_id = _untag(window)
        self._require_backend(backend_id).focus_window(inner)

    def snapshot(
        self,
        window: NativeHandle,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """Delegate snapshot to the owning backend.

        Element handles within the returned tree dict are tagged so that
        downstream tools (click, type_text, etc.) route correctly.
        """
        inner, backend_id = _untag(window)
        backend = self._require_backend(backend_id)
        tree = backend.snapshot(inner, max_depth=max_depth, max_nodes=max_nodes)
        # Tag all element refs in the tree so perform_action etc. route correctly
        _tag_tree_refs(tree, backend_id)
        return tree

    def find_elements(
        self,
        window: NativeHandle,
        role: str | None = None,
        name: str | None = None,
    ) -> list[NativeHandle]:
        """Delegate find_elements to the owning backend, tag results."""
        inner, backend_id = _untag(window)
        backend = self._require_backend(backend_id)
        handles = backend.find_elements(inner, role=role, name=name)
        return [_tag(h, backend_id) for h in handles]

    def perform_action(
        self,
        handle: NativeHandle,
        action: DesktopAction,
        **kwargs: Any,
    ) -> Any:
        """Delegate perform_action to the owning backend."""
        inner, backend_id = _untag(handle)
        return self._require_backend(backend_id).perform_action(inner, action, **kwargs)

    def get_element_info(self, handle: NativeHandle) -> dict[str, Any]:
        """Delegate get_element_info to the owning backend."""
        inner, backend_id = _untag(handle)
        info = self._require_backend(backend_id).get_element_info(inner)
        info["_backend_id"] = backend_id
        return info

    def is_valid(self, element: NativeHandle) -> bool:
        """Delegate is_valid to the owning backend."""
        inner, backend_id = _untag(element)
        backend = self._backends.get(backend_id)
        if backend is None:
            return False
        try:
            return backend.is_valid(inner)
        except Exception:
            return False

    def clipboard_read(self) -> str:
        """Read clipboard from the native backend (OS-level)."""
        return self._native.clipboard_read()

    def clipboard_write(self, text: str) -> None:
        """Write clipboard via the native backend (OS-level)."""
        self._native.clipboard_write(text)

    def dispose(self) -> None:
        """Dispose all managed backends."""
        for backend in self._backends.values():
            try:
                backend.dispose()
            except Exception:
                logger.debug("Error disposing backend", exc_info=True)

    def scroll_to_item(
        self,
        container: NativeHandle,
        *,
        item_name: str | None = None,
        item_index: int | None = None,
        max_retries: int = 10,
    ) -> NativeHandle | None:
        """Delegate scroll_to_item to the owning backend, tag result."""
        inner, backend_id = _untag(container)
        backend = self._require_backend(backend_id)
        result = backend.scroll_to_item(
            inner,
            item_name=item_name,
            item_index=item_index,
            max_retries=max_retries,
        )
        if result is None:
            return None
        return _tag(result, backend_id)

    # -- Window state management ----------------------------------------------

    def minimize_window(self, window: NativeHandle) -> None:
        """Delegate minimize_window to the owning backend."""
        inner, backend_id = _untag(window)
        self._require_backend(backend_id).minimize_window(inner)

    def maximize_window(self, window: NativeHandle) -> None:
        """Delegate maximize_window to the owning backend."""
        inner, backend_id = _untag(window)
        self._require_backend(backend_id).maximize_window(inner)

    def restore_window(self, window: NativeHandle) -> None:
        """Delegate restore_window to the owning backend."""
        inner, backend_id = _untag(window)
        self._require_backend(backend_id).restore_window(inner)

    def move_window(self, window: NativeHandle, x: int, y: int) -> None:
        """Delegate move_window to the owning backend."""
        inner, backend_id = _untag(window)
        self._require_backend(backend_id).move_window(inner, x, y)

    def resize_window(self, window: NativeHandle, width: int, height: int) -> None:
        """Delegate resize_window to the owning backend."""
        inner, backend_id = _untag(window)
        self._require_backend(backend_id).resize_window(inner, width, height)

    # -- Internal helpers -----------------------------------------------------

    def _require_backend(self, backend_id: str) -> DesktopBackend:
        """Look up a backend by id, raising if not found."""
        backend = self._backends.get(backend_id)
        if backend is None:
            raise WindowNotFoundError(f"No backend registered for id {backend_id!r}")
        return backend


# ---------------------------------------------------------------------------
# Tree ref tagging helpers
# ---------------------------------------------------------------------------


def _tag_tree_refs(node: dict[str, Any], backend_id: str) -> None:
    """Walk a snapshot tree dict and tag all element refs.

    Mutates the tree in-place.  Adds a ``_routing_handle`` key to each node
    containing a :class:`TaggedHandle` wrapping the original ``backend_id``
    value.  The original ``backend_id`` string is preserved unchanged so
    that downstream serialization (``to_dict()`` / ``json.dumps()``) is not
    broken by a non-string ``TaggedHandle`` object.

    The snapshot tool's ``_dict_to_element`` reads ``_routing_handle`` when
    present and uses it as the value stored in :class:`ElementRefStore`,
    enabling downstream tools (click, type_text, etc.) to route the handle
    back to the correct backend.
    """
    backend_id_val = node.get("backend_id")
    if backend_id_val is not None:
        # Preserve the original backend_id string for display/serialization.
        # Store the TaggedHandle in a separate key for routing.
        node["_routing_handle"] = _tag(backend_id_val, backend_id)

    children = node.get("children")
    if children:
        for child in children:
            _tag_tree_refs(child, backend_id)
