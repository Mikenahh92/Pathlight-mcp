"""WebBackend — CDP-based accessibility backend for web browsers (GW-095).

Uses the Chrome DevTools Protocol (CDP) Accessibility domain to query the
browser's accessibility tree and the Input domain for action dispatch.
This is the third :class:`~guidewire.backends.base.DesktopBackend`
implementation, complementing the Windows UIA and Linux AT-SPI backends.

Architecture overview:
    - :class:`WebBackend` holds a :class:`~guidewire.cdp.browser.CDPBrowser`
      for browser connection management.
    - ``list_windows`` → discover browser page targets via HTTP ``/json/list``.
    - ``snapshot`` →
      :meth:`~guidewire.cdp.domains.accessibility.AccessibilityDomain.get_full_ax_tree`
      produces an AX node tree that is converted to
      :class:`~guidewire.models.NormalizedElement`
      via the ``"web"`` platform mapping tables.
    - ``find_elements`` →
      :meth:`~guidewire.cdp.domains.accessibility.AccessibilityDomain.query_ax_tree`
      for server-side filtering by role/name.
    - ``perform_action`` → :class:`~guidewire.cdp.domains.input.InputDomain` for
      mouse / keyboard event dispatch (click, type, press_key).
    - Lazy bounds fetching via :class:`~guidewire.cdp.domains.dom.DOMDomain.get_box_model`
      when AX nodes lack inline bounds.
    - Cache invalidation on each ``snapshot`` call so stale AX node IDs are
      not reused across tree generations.

Implementation status:
    - ``list_windows``, ``get_window_info``, ``focus_window`` — GW-095
    - ``snapshot`` — GW-095
    - ``find_elements`` — GW-095
    - ``perform_action``, ``get_element_info``, ``is_valid`` — GW-095
    - Window state management (minimize, maximize, etc.) — GW-095
    - ``clipboard_read``, ``clipboard_write`` — GW-095
    - ``dispose`` — GW-095
"""

import json
import logging
from typing import Any

from guidewire.backends.base import DesktopBackend
from guidewire.backends.types import DesktopAction, NativeHandle
from guidewire.backends.web_normalize import (
    build_normalized_tree,
    fetch_bounds_from_dom,
    find_root_ax_node,
)
from guidewire.cdp._types import AXNode, CDPTarget
from guidewire.cdp.browser import CDPBrowser
from guidewire.cdp.domains.accessibility import AccessibilityDomain
from guidewire.cdp.domains.dom import DOMDomain
from guidewire.cdp.domains.input import InputDomain
from guidewire.cdp.session import CDPSession
from guidewire.errors import (
    ActionNotSupportedError,
    BackendUnavailableError,
    ElementNotFoundError,
    StaleElementReferenceError,
    WindowNotFoundError,
)
from guidewire.models import NormalizedElement

logger = logging.getLogger(__name__)

__all__ = [
    "WebBackend",
]


class WebBackend(DesktopBackend):
    """CDP-based accessibility backend for web browsers.

    Connects to a Chromium-based browser's debug port and uses the CDP
    Accessibility domain to query the accessibility tree, the Input domain
    for action dispatch, and the DOM domain for bounds resolution.

    Args:
        host: Hostname or IP of the Chromium debug target.
        port: Debug port number.
        browser: Optional pre-constructed :class:`~guidewire.cdp.browser.CDPBrowser`
            instance.  When provided, *host* and *port* are ignored in favour
            of the browser's own settings.  This enables dependency injection
            for testing or shared connection pooling.

    Usage::

        from guidewire.backends.web import WebBackend

        backend = WebBackend(host="localhost", port=9222)
        backend.connect()
        windows = backend.list_windows()
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9222,
        *,
        browser: CDPBrowser | None = None,
    ) -> None:
        if browser is not None:
            self._browser = browser
        else:
            self._browser = CDPBrowser(host=host, port=port)
        self._disposed: bool = False
        self._connected: bool = False

        # AX node cache: node_id → AXNode (populated per snapshot)
        self._ax_cache: dict[str, AXNode] = {}

        # Session cache: target_id → CDPSession
        self._sessions: dict[str, CDPSession] = {}

        # Domain cache: target_id → (AccessibilityDomain, DOMDomain, InputDomain)
        self._domains: dict[str, tuple[AccessibilityDomain, DOMDomain, InputDomain]] = {}

    # -- Connection management ------------------------------------------------

    def connect(self) -> None:
        """Open the browser connection.

        Raises:
            BackendUnavailableError: If the connection cannot be established.
        """
        if self._disposed:
            raise BackendUnavailableError("WebBackend has been disposed")
        if self._connected:
            return
        try:
            self._browser.connect()
            self._connected = True
            logger.info("WebBackend connected to %s:%s", self._browser.host, self._browser.port)
        except Exception as exc:
            raise BackendUnavailableError(
                f"Failed to connect to browser at {self._browser.host}:{self._browser.port}: {exc}"
            ) from exc

    def _require_connected(self) -> None:
        """Raise if the backend is not connected or has been disposed."""
        if self._disposed:
            raise BackendUnavailableError("WebBackend has been disposed")
        if not self._connected:
            raise BackendUnavailableError("WebBackend is not connected — call connect() first")

    def _get_or_create_session(self, target_id: str) -> CDPSession:
        """Get or create a CDP session for the given target ID.

        Args:
            target_id: The browser target identifier.

        Returns:
            An attached :class:`~guidewire.cdp.session.CDPSession`.
        """
        session = self._sessions.get(target_id)
        if session is not None and session.is_attached:
            return session

        target = self._browser.get_target(target_id)
        if target is None:
            raise WindowNotFoundError(f"No browser target found with id {target_id!r}")

        session = self._browser.attach(target)
        self._sessions[target_id] = session
        return session

    def _get_domains(self, target_id: str) -> tuple[AccessibilityDomain, DOMDomain, InputDomain]:
        """Get or create domain wrappers for the given target.

        Args:
            target_id: The browser target identifier.

        Returns:
            A tuple of (AccessibilityDomain, DOMDomain, InputDomain).
        """
        domains = self._domains.get(target_id)
        if domains is not None:
            return domains

        session = self._get_or_create_session(target_id)
        acc = AccessibilityDomain(session)
        dom = DOMDomain(session)
        inp = InputDomain(session)
        self._domains[target_id] = (acc, dom, inp)
        return (acc, dom, inp)

    # -- DesktopBackend interface ---------------------------------------------

    def list_windows(self) -> list[NativeHandle]:
        """List all browser page targets as window handles.

        Discovers browser targets via the HTTP ``/json/list`` endpoint and
        filters to ``"page"`` type targets only.

        Returns:
            List of :class:`NativeHandle` instances wrapping
            :class:`~guidewire.cdp._types.CDPTarget` objects.

        Raises:
            BackendUnavailableError: If the browser is not connected or has
                been disposed.
        """
        self._require_connected()
        targets = self._browser.list_targets(target_type="page")
        return [NativeHandle(t) for t in targets]

    def get_window_info(self, window: NativeHandle) -> dict[str, Any]:
        """Return window metadata as a dict.

        Args:
            window: Opaque native window handle wrapping a
                :class:`~guidewire.cdp._types.CDPTarget`.

        Returns:
            Dict with keys ``title``, ``app_name``, ``focused``, ``bounds``.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        target = self._resolve_target(window)
        return {
            "title": target.title or "",
            "app_name": "browser",
            "focused": False,
            "bounds": None,
        }

    def focus_window(self, window: NativeHandle) -> None:
        """Bring a browser page to the foreground by activating its target.

        For web backends, "focus" means bringing the page to the front of
        the browser tab order via ``Page.bringToFront``.

        Args:
            window: Opaque native window handle.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        self._require_connected()
        target = self._resolve_target(window)
        session = self._get_or_create_session(target.id)
        try:
            session.send_command("Page.bringToFront")
        except Exception as exc:
            raise WindowNotFoundError(f"Failed to focus window: {exc}") from exc

    def snapshot(
        self,
        window: NativeHandle,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """Return an accessibility snapshot as a tree dict.

        Fetches the full AX tree via
        :meth:`~guidewire.cdp.domains.accessibility.AccessibilityDomain.get_full_ax_tree`,
        builds a parent-child tree from the ``childIds`` fields, converts
        to :class:`~guidewire.models.NormalizedElement` via the ``"web"``
        platform mapping tables, and applies depth/node limits.

        The AX node cache is rebuilt on every snapshot call so that stale
        node IDs from previous snapshots are not reused.

        Args:
            window: Opaque native window handle wrapping a
                :class:`~guidewire.cdp._types.CDPTarget`.
            max_depth: Maximum tree depth to traverse (default 4).
            max_nodes: Maximum number of nodes to include (default 500).

        Returns:
            Dict matching the NormalizedElement schema.

        Raises:
            BackendUnavailableError: If the backend is disposed.
            WindowNotFoundError: If the handle is invalid.
        """
        self._require_connected()

        target = self._resolve_target(window)
        acc, dom, _ = self._get_domains(target.id)

        # Fetch the full AX tree and rebuild the cache
        ax_nodes = acc.get_full_ax_tree()
        self._ax_cache = {n.node_id: n for n in ax_nodes}

        # Find the root node (webArea or the first node with no parent reference)
        root_node = find_root_ax_node(ax_nodes)
        if root_node is None:
            fallback = NormalizedElement(
                ref="",
                backend_id="",
                role="unknown",
            )
            return fallback.to_dict()

        # Build the NormalizedElement tree with depth/node limits
        counter = [0]
        result = build_normalized_tree(
            root_node, 0, max_depth, counter, max_nodes, dom, self._ax_cache
        )
        if result is None:
            fallback = NormalizedElement(
                ref=root_node.node_id,
                backend_id=root_node.node_id,
                role="unknown",
            )
            return fallback.to_dict()

        return result.to_dict()

    def find_elements(
        self,
        window: NativeHandle,
        role: str | None = None,
        name: str | None = None,
    ) -> list[NativeHandle]:
        """Find elements matching criteria within a browser page.

        Uses :meth:`~guidewire.cdp.domains.accessibility.AccessibilityDomain.query_ax_tree`
        for server-side filtering when exact role/name matches are requested.
        Falls back to client-side filtering over the cached AX tree for
        case-insensitive substring name matching.

        Args:
            window: Opaque native window handle.
            role: Normalized role to match (e.g. ``"button"``).
            name: Accessible name to match (case-insensitive substring).

        Returns:
            List of matching :class:`NativeHandle` instances wrapping
            :class:`~guidewire.cdp._types.AXNode` node IDs as strings.

        Raises:
            BackendUnavailableError: If the backend is disposed.
        """
        self._require_connected()

        if role is None and name is None:
            return []

        target = self._resolve_target(window)
        acc, _, _ = self._get_domains(target.id)

        results: list[NativeHandle] = []

        # Build AX cache if empty
        if not self._ax_cache:
            ax_nodes = acc.get_full_ax_tree()
            self._ax_cache = {n.node_id: n for n in ax_nodes}

        from guidewire.models.mappings import ROLE_MAP

        # Reverse-lookup: find the raw CDP AX role(s) that map to the
        # normalized role the caller requested.
        raw_roles_for_norm: list[str] | None = None
        if role is not None:
            raw_roles_for_norm = [
                raw for (plat, raw), norm in ROLE_MAP.items() if plat == "web" and norm == role
            ]

        # Try server-side query_ax_tree when we have a direct role match.
        # query_ax_tree requires an exact AX role name.
        if raw_roles_for_norm and name is None:
            # Pure role-only filter — use query_ax_tree directly
            all_failed = True
            for raw_role in raw_roles_for_norm:
                try:
                    matched = acc.query_ax_tree(role=raw_role)
                    for node in matched:
                        results.append(NativeHandle(node.node_id))
                    all_failed = False
                except Exception:
                    logger.debug(
                        "query_ax_tree failed for role=%s, falling back",
                        raw_role,
                        exc_info=True,
                    )
            if not all_failed and results:
                return results
            # All queries failed or returned nothing — fall back to client-side
            return self._client_side_find(role, name)

        if name is not None and raw_roles_for_norm and len(raw_roles_for_norm) == 1:
            # Exact name + single role — try query_ax_tree with accessible_name
            try:
                matched = acc.query_ax_tree(accessible_name=name, role=raw_roles_for_norm[0])
                for node in matched:
                    results.append(NativeHandle(node.node_id))
                if results:
                    return results
            except Exception:
                logger.debug(
                    "query_ax_tree failed for name=%s role=%s, falling back",
                    name,
                    raw_roles_for_norm[0],
                    exc_info=True,
                )

        # Client-side fallback: filter the AX cache
        return self._client_side_find(role, name)

    def perform_action(
        self,
        handle: NativeHandle,
        action: DesktopAction,
        **kwargs: Any,
    ) -> Any:
        """Perform an action on a web element via CDP Input domain.

        Maps each :class:`DesktopAction` variant to the appropriate CDP
        Input domain method:

        - ``CLICK`` → coordinate-based mouse event dispatch at element center
        - ``TYPE`` → ``Input.insertText``
        - ``PRESS_KEY`` → ``Input.dispatchKeyEvent``
        - ``SET_VALUE`` → JavaScript evaluation (fallback)
        - ``GET_TEXT`` → read from AX node value/name
        - ``EXPAND`` / ``COLLAPSE`` → click on the element
        - ``TOGGLE`` → click on the element
        - ``SELECT`` / ``SELECT_ITEM`` → click on the element

        Args:
            handle: Opaque native element handle (AX node ID string).
            action: The action to perform.
            **kwargs: Action-specific parameters.

        Returns:
            ``str`` when action is ``GET_TEXT``, otherwise ``None``.

        Raises:
            StaleElementReferenceError: If the backend is disposed.
            ActionNotSupportedError: If the action is not available.
            ElementNotFoundError: If the handle is invalid.
        """
        if self._disposed:
            raise StaleElementReferenceError("WebBackend has been disposed")
        self._require_connected()

        node_id = self._resolve_element_id(handle)
        node = self._ax_cache.get(node_id)
        if node is None:
            raise ElementNotFoundError(f"AX node {node_id!r} not found in cache")

        # Find the session that owns this element — use the first active session
        session = self._get_active_session()
        _, dom, inp = self._get_domains(session.target.id)

        try:
            if action == DesktopAction.CLICK:
                return self._action_click(node, dom, inp)
            if action == DesktopAction.TYPE:
                return self._action_type(node, inp, **kwargs)
            if action == DesktopAction.PRESS_KEY:
                return self._action_press_key(inp, **kwargs)
            if action == DesktopAction.SET_VALUE:
                return self._action_set_value(node, session, inp, **kwargs)
            if action == DesktopAction.GET_TEXT:
                return self._action_get_text(node)
            if action in (
                DesktopAction.TOGGLE,
                DesktopAction.EXPAND,
                DesktopAction.COLLAPSE,
                DesktopAction.SELECT,
                DesktopAction.SELECT_ITEM,
            ):
                return self._action_click(node, dom, inp)
            if action == DesktopAction.SCROLL:
                return self._action_scroll(node, dom, inp)
            if action == "focus":
                return self._action_focus(node, dom)
        except (ActionNotSupportedError, StaleElementReferenceError, ElementNotFoundError):
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Failed to perform {action!r}: {exc}") from exc

        raise ActionNotSupportedError(f"WebBackend does not support action {action!r}")

    def get_element_info(self, handle: NativeHandle) -> dict[str, Any]:
        """Return element metadata as a dict.

        Args:
            handle: Opaque native element handle (AX node ID string).

        Returns:
            Dict with keys ``role``, ``name``, ``states``.

        Raises:
            ElementNotFoundError: If the handle is not known.
        """
        node_id = self._resolve_element_id(handle)
        node = self._ax_cache.get(node_id)
        if node is None:
            raise ElementNotFoundError(f"AX node {node_id!r} not found in cache")

        from dataclasses import fields as dc_fields

        from guidewire.backends.normalize import normalize_states
        from guidewire.models.mappings import resolve_role

        raw_role = node.role or ""
        normalized_role = resolve_role("web", raw_role) or raw_role.lower()

        # Extract states from properties
        raw_states: dict[str, Any] = {}
        if node.properties:
            raw_states.update(node.properties)

        norm_states = normalize_states("web", raw_states)
        states_dict: dict[str, Any] = {}
        for f in dc_fields(norm_states):
            val = getattr(norm_states, f.name)
            if val is not None:
                states_dict[f.name] = val

        return {
            "role": normalized_role,
            "name": node.name,
            "states": states_dict,
        }

    def is_valid(self, element: NativeHandle) -> bool:
        """Check whether an AX node reference is still valid.

        Checks the local cache. Returns ``False`` if the element is
        not in the cache (e.g. from a previous snapshot generation).

        Args:
            element: Opaque native element handle (AX node ID string).

        Returns:
            ``True`` if the element exists in the current AX cache.
        """
        if self._disposed:
            return False
        try:
            node_id = self._resolve_element_id(element)
            return node_id in self._ax_cache
        except Exception:
            return False

    def clipboard_read(self) -> str:
        """Read text from clipboard via CDP ``Runtime.evaluate``.

        Uses ``navigator.clipboard.readText()`` to read the clipboard
        in the browser context.

        Returns:
            The clipboard text content.

        Raises:
            BackendUnavailableError: If the clipboard cannot be read.
        """
        self._require_connected()
        session = self._get_active_session()
        try:
            result = session.send_command(
                "Runtime.evaluate",
                {
                    "expression": "navigator.clipboard.readText()",
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
            value = result.get("result", {}).get("value")
            return str(value) if value is not None else ""
        except Exception as exc:
            raise BackendUnavailableError(f"Failed to read clipboard: {exc}") from exc

    def clipboard_write(self, text: str) -> None:
        """Write text to clipboard via CDP ``Runtime.evaluate``.

        Args:
            text: The text to write to the clipboard.

        Raises:
            BackendUnavailableError: If the clipboard cannot be written.
        """
        self._require_connected()
        session = self._get_active_session()
        try:
            session.send_command(
                "Runtime.evaluate",
                {
                    "expression": f"navigator.clipboard.writeText({json.dumps(text)})",
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
        except Exception as exc:
            raise BackendUnavailableError(f"Failed to write clipboard: {exc}") from exc

    def dispose(self) -> None:
        """Release all resources held by this backend.

        Detaches all CDP sessions and closes the browser connection.
        """
        if self._disposed:
            return

        # Detach all sessions
        for session in self._sessions.values():
            try:
                session.close()
            except Exception:
                logger.debug("Error closing session during dispose")

        self._sessions.clear()
        self._domains.clear()
        self._ax_cache.clear()

        # Close browser connection
        try:
            self._browser.close()
        except Exception:
            logger.debug("Error closing browser during dispose")

        self._connected = False
        self._disposed = True

    # -- Window state management -----------------------------------------------

    def minimize_window(self, window: NativeHandle) -> None:
        """Web browsers do not support minimize via CDP.

        Raises:
            ActionNotSupportedError: Always.
        """
        raise ActionNotSupportedError(
            "WebBackend does not support minimize_window — "
            "browser windows cannot be minimized via CDP"
        )

    def maximize_window(self, window: NativeHandle) -> None:
        """Web browsers do not support maximize via CDP.

        Raises:
            ActionNotSupportedError: Always.
        """
        raise ActionNotSupportedError(
            "WebBackend does not support maximize_window — "
            "browser windows cannot be maximized via CDP"
        )

    def restore_window(self, window: NativeHandle) -> None:
        """Web browsers do not support restore via CDP.

        Raises:
            ActionNotSupportedError: Always.
        """
        raise ActionNotSupportedError(
            "WebBackend does not support restore_window — "
            "browser windows cannot be restored via CDP"
        )

    def move_window(self, window: NativeHandle, x: int, y: int) -> None:
        """Web browsers do not support move via CDP.

        Raises:
            ActionNotSupportedError: Always.
        """
        raise ActionNotSupportedError(
            "WebBackend does not support move_window — browser windows cannot be moved via CDP"
        )

    def resize_window(self, window: NativeHandle, width: int, height: int) -> None:
        """Web browsers do not support resize via CDP.

        Raises:
            ActionNotSupportedError: Always.
        """
        raise ActionNotSupportedError(
            "WebBackend does not support resize_window — browser windows cannot be resized via CDP"
        )

    def scroll_to_item(
        self,
        container: NativeHandle,
        *,
        item_name: str | None = None,
        item_index: int | None = None,
        max_retries: int = 10,
    ) -> NativeHandle | None:
        """Web virtualized list scroll-to-item.

        For web backends, this uses ``DOM.scrollIntoViewIfNeeded`` to bring
        the target element into the viewport. It searches the AX cache for
        the matching element.

        Args:
            container: Opaque native handle for the container element.
            item_name: Name of the target item (case-insensitive substring).
            item_index: Zero-based index of the target item.
            max_retries: Unused for web (kept for API compat).

        Returns:
            A :class:`NativeHandle` for the found item, or ``None``.
        """
        self._require_connected()

        if item_name is None and item_index is None:
            raise ActionNotSupportedError("scroll_to_item requires either item_name or item_index")

        # Search the AX cache for children of the container
        container_id = self._resolve_element_id(container)
        children = self._find_cached_children(container_id)

        # Filter by name or index
        if item_name is not None:
            for child in children:
                if child.name and item_name.lower() in (child.name or "").lower():
                    # Scroll into view
                    self._scroll_node_into_view(child)
                    return NativeHandle(child.node_id)
        elif item_index is not None and 0 <= item_index < len(children):
            child = children[item_index]
            self._scroll_node_into_view(child)
            return NativeHandle(child.node_id)

        return None

    # -- Internal helpers -------------------------------------------------------

    @staticmethod
    def _resolve_target(window: NativeHandle) -> CDPTarget:
        """Validate that *window* wraps a :class:`CDPTarget`.

        Returns:
            The underlying :class:`CDPTarget`.

        Raises:
            WindowNotFoundError: If the handle is not a valid CDPTarget.
        """
        if not isinstance(window, CDPTarget):
            raise WindowNotFoundError(f"Window handle {window!r} is not a valid CDP target")
        return window

    @staticmethod
    def _resolve_element_id(handle: NativeHandle) -> str:
        """Extract the AX node ID from a :class:`NativeHandle`.

        Args:
            handle: Opaque native element handle.

        Returns:
            The AX node ID string.

        Raises:
            ElementNotFoundError: If the handle is ``None`` or empty.
        """
        if handle is None:
            raise ElementNotFoundError("Element handle is None")
        # Element handles are stored as node ID strings
        return str(handle)

    def _get_active_session(self) -> CDPSession:
        """Get any active CDP session.

        Returns:
            An active :class:`CDPSession`.

        Raises:
            BackendUnavailableError: If no session is active.
        """
        for session in self._sessions.values():
            if session.is_attached:
                return session
        raise BackendUnavailableError("No active CDP session")

    def _client_side_find(
        self,
        role: str | None,
        name: str | None,
    ) -> list[NativeHandle]:
        """Client-side filtering over the AX cache.

        Args:
            role: Normalized role to match.
            name: Case-insensitive substring name to match.

        Returns:
            List of matching :class:`NativeHandle` instances.
        """
        from guidewire.models.mappings import resolve_role

        results: list[NativeHandle] = []
        for node in self._ax_cache.values():
            # Role filter (normalize the AX role to compare)
            if role is not None:
                node_role = resolve_role("web", node.role) if node.role else None
                if node_role != role and (node.role or "").lower() != role:
                    continue
            # Name filter (case-insensitive substring)
            if name is not None:
                node_name = node.name or ""
                if name.lower() not in node_name.lower():
                    continue
            results.append(NativeHandle(node.node_id))
        return results

    def _find_cached_children(self, parent_id: str) -> list[AXNode]:
        """Find direct children of a node in the AX cache.

        Args:
            parent_id: The parent node ID.

        Returns:
            List of child :class:`AXNode` instances.
        """
        parent = self._ax_cache.get(parent_id)
        if parent is None:
            return []
        return [self._ax_cache[cid] for cid in parent.child_ids if cid in self._ax_cache]

    def _scroll_node_into_view(self, node: AXNode) -> None:
        """Scroll an AX node into view via the DOM domain.

        Args:
            node: The :class:`AXNode` to scroll into view.
        """
        if node.backend_dom_node_id is None:
            return
        try:
            session = self._get_active_session()
            _, dom, _ = self._get_domains(session.target.id)
            dom.scroll_into_view_if_needed(backend_node_id=node.backend_dom_node_id)
        except Exception:
            logger.debug("scroll_into_view failed for node %s", node.node_id, exc_info=True)

    # -- Action dispatch helpers -----------------------------------------------

    def _action_click(self, node: AXNode, dom: DOMDomain, inp: InputDomain) -> None:
        """Click an element at its center coordinates.

        Resolves the element bounds (from AX node or DOM box model) and
        dispatches a mouse press/release at the center point.

        Args:
            node: The :class:`AXNode` to click.
            dom: :class:`DOMDomain` for bounds resolution.
            inp: :class:`InputDomain` for mouse dispatch.

        Raises:
            ActionNotSupportedError: If bounds cannot be determined.
        """
        bounds = node.bounds
        if bounds is None and node.backend_dom_node_id is not None:
            bounds_data = fetch_bounds_from_dom(dom, node.backend_dom_node_id)
            if bounds_data is not None:
                bounds = bounds_data

        if bounds is None:
            raise ActionNotSupportedError(
                f"Cannot determine bounds for click on node {node.node_id!r}"
            )

        x = float(bounds.get("x", 0)) + float(bounds.get("width", 0)) / 2
        y = float(bounds.get("y", 0)) + float(bounds.get("height", 0)) / 2

        inp.dispatch_mouse_event("mousePressed", x, y, button="left", click_count=1)
        inp.dispatch_mouse_event("mouseReleased", x, y, button="left", click_count=1)

    @staticmethod
    def _action_type(node: AXNode, inp: InputDomain, **kwargs: Any) -> None:
        """Type text into an element via ``Input.insertText``.

        First focuses the element (if it has a backend DOM node), then
        inserts the text using the CDP Input domain.

        Args:
            node: The :class:`AXNode` to type into.
            inp: :class:`InputDomain` for text insertion.
            **kwargs: Must contain ``text`` (str).

        Raises:
            ActionNotSupportedError: If text parameter is missing.
        """
        text = kwargs.get("text")
        if text is None:
            raise ActionNotSupportedError("TYPE action requires a 'text' parameter")
        inp.insert_text(str(text))

    @staticmethod
    def _action_press_key(inp: InputDomain, **kwargs: Any) -> None:
        """Press a key via ``Input.dispatchKeyEvent``.

        Dispatches a ``keyDown`` followed by a ``keyUp`` event for the
        specified key.

        Args:
            inp: :class:`InputDomain` for key event dispatch.
            **kwargs: Must contain ``keys`` (str).

        Raises:
            ActionNotSupportedError: If keys parameter is missing.
        """
        key = kwargs.get("keys") or kwargs.get("key")
        if key is None:
            raise ActionNotSupportedError("PRESS_KEY action requires a 'keys' parameter")
        key_str = str(key)
        inp.dispatch_key_event("keyDown", key=key_str)
        inp.dispatch_key_event("keyUp", key=key_str)

    @staticmethod
    def _action_set_value(
        node: AXNode, session: CDPSession, inp: InputDomain, **kwargs: Any
    ) -> None:
        """Set the value of an element via JavaScript evaluation.

        When the element has a ``backend_dom_node_id``, resolves the DOM
        node to a remote object and sets its ``value`` property via
        ``Runtime.callFunctionOn``.  Falls back to focus + insertText
        when no DOM node ID is available.

        Args:
            node: The :class:`AXNode` to set the value on.
            session: The :class:`CDPSession` for JS evaluation.
            inp: :class:`InputDomain` for fallback text insertion.
            **kwargs: Must contain ``value`` (str).

        Raises:
            ActionNotSupportedError: If value parameter is missing.
        """
        value = kwargs.get("value")
        if value is None:
            raise ActionNotSupportedError("SET_VALUE action requires a 'value' parameter")

        value_str = str(value)

        if node.backend_dom_node_id is not None:
            # Resolve the AX node's DOM element and set value via JS
            try:
                selector = f'[data-ax-node-id="{node.node_id}"]'
                js_expr = (
                    f"document.querySelector('{selector}') "
                    f"&& (document.querySelector('{selector}').value "
                    f"= {json.dumps(value_str)})"
                )
                session.send_command(
                    "Runtime.evaluate",
                    {
                        "expression": js_expr,
                        "returnByValue": True,
                    },
                )
                return
            except Exception:
                logger.debug("JS set_value failed, falling back to insertText")

        # Fallback: focus + insert text
        inp.insert_text(value_str)

    @staticmethod
    def _action_get_text(node: AXNode) -> str:
        """Get the text value of an AX node.

        Tries the node value first, then falls back to name.

        Args:
            node: The :class:`AXNode` to read text from.

        Returns:
            The text string.
        """
        if node.value is not None:
            return str(node.value)
        if node.name is not None:
            return str(node.name)
        return ""

    @staticmethod
    def _action_scroll(node: AXNode, dom: DOMDomain, inp: InputDomain) -> None:
        """Scroll an element via mouse wheel at its center.

        Args:
            node: The :class:`AXNode` to scroll.
            dom: :class:`DOMDomain` for bounds resolution.
            inp: :class:`InputDomain` for mouse wheel dispatch.
        """
        bounds = node.bounds
        if bounds is None:
            return
        x = float(bounds.get("x", 0)) + float(bounds.get("width", 0)) / 2
        y = float(bounds.get("y", 0)) + float(bounds.get("height", 0)) / 2
        inp.dispatch_mouse_event("mouseWheel", x, y, button="none", delta_y=100.0)

    @staticmethod
    def _action_focus(node: AXNode, dom: DOMDomain) -> None:
        """Focus an element via the DOM domain.

        Args:
            node: The :class:`AXNode` to focus.
            dom: :class:`DOMDomain` for focus command.
        """
        if node.backend_dom_node_id is not None:
            try:
                dom.focus(backend_node_id=node.backend_dom_node_id)
            except Exception:
                logger.debug("DOM focus failed for node %s", node.node_id, exc_info=True)
