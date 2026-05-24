"""WebBackend — CDP-based accessibility backend for web browsers (GW-095, GW-096, GW-101).

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
      Multi-frame support: discovers iframes via ``Page.getFrameTree``,
      attaches CDP sessions to each child frame target, and merges their
      AX trees into the main snapshot (GW-101).
    - ``find_elements`` →
      :meth:`~guidewire.cdp.domains.accessibility.AccessibilityDomain.query_ax_tree`
      for server-side filtering by role/name.
    - ``perform_action`` → :class:`~guidewire.cdp.domains.input.InputDomain` for
      mouse / keyboard event dispatch (click, type, press_key, scroll).
    - Lazy bounds fetching via :class:`~guidewire.cdp.domains.dom.DOMDomain.get_box_model`
      when AX nodes lack inline bounds, with per-node bounds caching (GW-096).
    - Stale element detection before action dispatch (GW-096).
    - Cache invalidation on each ``snapshot`` call so stale AX node IDs are
      not reused across tree generations.
    - Virtualized list detection via ``aria-rowcount`` / ``aria-setsize``
      heuristics with scroll-to-item via CDP Input domain (GW-101).

Implementation status:
    - ``list_windows``, ``get_window_info``, ``focus_window`` — GW-095
    - ``snapshot`` (incl. multi-frame / iframe) — GW-095, GW-101
    - ``find_elements`` — GW-095
    - ``perform_action`` (click, type, scroll, toggle, press_key, get_text) — GW-095, GW-096
    - Bounds caching and stale element detection — GW-096
    - ``get_element_info``, ``is_valid`` — GW-095
    - Window state management (minimize, maximize, etc.) — GW-095
    - ``clipboard_read``, ``clipboard_write`` — GW-095
    - ``scroll_to_item`` (virtualized list scroll-retry) — GW-101
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
    is_virtualized_container,
)
from guidewire.cdp._types import AXNode, CDPTarget
from guidewire.cdp.browser import CDPBrowser
from guidewire.cdp.domains.accessibility import AccessibilityDomain
from guidewire.cdp.domains.dom import DOMDomain
from guidewire.cdp.domains.input import InputDomain
from guidewire.cdp.domains.page import PageDomain
from guidewire.cdp.domains.target import TargetDomain
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

        # Bounds cache: node_id → {x, y, width, height} (GW-096)
        # Populated lazily on first bounds access, invalidated with _ax_cache.
        self._bounds_cache: dict[str, dict[str, float]] = {}

        # Session cache: target_id → CDPSession
        self._sessions: dict[str, CDPSession] = {}

        # Domain cache: target_id → domain tuple
        self._domains: dict[
            str,
            tuple[AccessibilityDomain, DOMDomain, InputDomain, PageDomain, TargetDomain],
        ] = {}

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

    def _get_domains(
        self,
        target_id: str,
    ) -> tuple[AccessibilityDomain, DOMDomain, InputDomain, PageDomain, TargetDomain]:
        """Get or create domain wrappers for the given target.

        Args:
            target_id: The browser target identifier.

        Returns:
            A tuple of (AccessibilityDomain, DOMDomain, InputDomain,
            PageDomain, TargetDomain).
        """
        domains = self._domains.get(target_id)
        if domains is not None:
            return domains

        session = self._get_or_create_session(target_id)
        acc = AccessibilityDomain(session)
        dom = DOMDomain(session)
        inp = InputDomain(session)
        page = PageDomain(session)
        target = TargetDomain(session)
        self._domains[target_id] = (acc, dom, inp, page, target)
        return (acc, dom, inp, page, target)

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

        Multi-frame support (GW-101): discovers child frames (iframes) via
        ``Page.getFrameTree``, attaches CDP sessions to each iframe target,
        and merges their AX trees into the main snapshot.  Iframe content
        appears as children of inline frame AX nodes.

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
        acc, dom, _, page, _ = self._get_domains(target.id)

        # Fetch the full AX tree and rebuild the cache
        ax_nodes = acc.get_full_ax_tree()
        self._ax_cache = {n.node_id: n for n in ax_nodes}
        self._bounds_cache = {}  # Invalidate bounds cache on new snapshot (GW-096)

        # Discover child frames (iframes) and merge their AX trees (GW-101)
        iframe_ax_nodes = self._collect_iframe_ax_trees(target.id, page)
        if iframe_ax_nodes:
            ax_nodes = ax_nodes + iframe_ax_nodes
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
        acc, _, _, _, _ = self._get_domains(target.id)

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
        - ``TYPE`` → focus element + ``Input.insertText`` (with optional clear)
        - ``PRESS_KEY`` → ``Input.dispatchKeyEvent``
        - ``SET_VALUE`` → JavaScript evaluation (fallback)
        - ``GET_TEXT`` → read from AX node value/name
        - ``EXPAND`` / ``COLLAPSE`` → click on the element
        - ``TOGGLE`` → click on the element
        - ``SELECT`` / ``SELECT_ITEM`` → click on the element
        - ``SCROLL`` → mouse wheel dispatch at element center (with direction)

        Before dispatching any action, validates that the element is still
        present in the AX cache (stale element detection, GW-096).

        Args:
            handle: Opaque native element handle (AX node ID string).
            action: The action to perform.
            **kwargs: Action-specific parameters.

        Returns:
            ``str`` when action is ``GET_TEXT``, otherwise ``None``.

        Raises:
            StaleElementReferenceError: If the backend is disposed or the
                element is stale.
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

        # Stale element check — verify the node is still reachable (GW-096)
        self._stale_check(node_id)

        # Find the session that owns this element — use the first active session
        session = self._get_active_session()
        _, dom, inp, _, _ = self._get_domains(session.target.id)

        try:
            if action == DesktopAction.CLICK:
                return self._action_click(node, dom, inp, **kwargs)
            if action == DesktopAction.TYPE:
                return self._action_type(node, dom, inp, **kwargs)
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
                return self._action_scroll(node, dom, inp, **kwargs)
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
        self._bounds_cache.clear()

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

        For virtualized containers (detected via ``aria-rowcount`` /
        ``aria-setsize``), performs a scroll-retry loop: scrolls the
        container down via mouse wheel dispatch, re-reads the AX tree, and
        checks if the target item has been materialized (GW-101).

        Args:
            container: Opaque native handle for the container element.
            item_name: Name of the target item (case-insensitive substring).
            item_index: Zero-based index of the target item.
            max_retries: Maximum scroll iterations before giving up
                (default 10 for virtualized containers).

        Returns:
            A :class:`NativeHandle` for the found item, or ``None``.
        """
        self._require_connected()

        if item_name is None and item_index is None:
            raise ActionNotSupportedError("scroll_to_item requires either item_name or item_index")

        # Search the AX cache for children of the container
        container_id = self._resolve_element_id(container)
        container_node = self._ax_cache.get(container_id)

        # First pass: try to find the item in already-rendered children
        children = self._find_cached_children(container_id)

        if item_name is not None:
            for child in children:
                if child.name and item_name.lower() in (child.name or "").lower():
                    self._scroll_node_into_view(child)
                    return NativeHandle(child.node_id)
        elif item_index is not None and 0 <= item_index < len(children):
            child = children[item_index]
            self._scroll_node_into_view(child)
            return NativeHandle(child.node_id)

        # If not found and the container is virtualized, try scroll-retry (GW-101)
        if container_node is not None and is_virtualized_container(container_node):
            return self._virtualized_scroll_retry(
                container_node,
                item_name=item_name,
                item_index=item_index,
                max_retries=max_retries,
            )

        return None

    def _virtualized_scroll_retry(
        self,
        container: AXNode,
        *,
        item_name: str | None,
        item_index: int | None,
        max_retries: int,
    ) -> NativeHandle | None:
        """Scroll-retry loop for virtualized containers (GW-101).

        Repeatedly scrolls the container down via mouse wheel dispatch and
        checks if the target item has been materialized in the AX cache.

        Args:
            container: The virtualized container :class:`AXNode`.
            item_name: Name of the target item.
            item_index: Index of the target item.
            max_retries: Maximum scroll iterations.

        Returns:
            A :class:`NativeHandle` for the found item, or ``None``.
        """
        session = self._get_active_session()
        _, dom, inp, _, _ = self._get_domains(session.target.id)

        for _attempt in range(max_retries):
            # Scroll the container down via mouse wheel
            self._action_scroll(container, dom, inp, direction="down", delta=300.0)

            # Re-fetch children from the AX cache (may have changed after scroll)
            children = self._find_cached_children(container.node_id)

            if item_name is not None:
                for child in children:
                    if child.name and item_name.lower() in (child.name or "").lower():
                        self._scroll_node_into_view(child)
                        return NativeHandle(child.node_id)
            elif item_index is not None and 0 <= item_index < len(children):
                child = children[item_index]
                self._scroll_node_into_view(child)
                return NativeHandle(child.node_id)

        logger.debug(
            "scroll_to_item: item not found after %d retries in container %s",
            max_retries,
            container.node_id,
        )
        return None

    # -- Internal helpers -------------------------------------------------------

    def _collect_iframe_ax_trees(self, target_id: str, page: PageDomain) -> list[AXNode]:
        """Collect AX trees from child frames (iframes) for multi-frame snapshots.

        Uses ``Page.getFrameTree`` to discover child frames, then for each
        child frame creates a CDP session via ``Target.attachToTarget`` and
        fetches the AX tree.  Iframe AX node IDs are prefixed with the frame
        ID to avoid collisions with the main frame's node IDs.

        Args:
            target_id: The main page target identifier.
            page: The :class:`PageDomain` for the main page.

        Returns:
            List of AX nodes from all child frames, with prefixed node IDs.
        """
        all_iframe_nodes: list[AXNode] = []

        try:
            frames = page.get_frame_tree()
        except Exception:
            logger.debug("get_frame_tree failed for target %s", target_id, exc_info=True)
            return all_iframe_nodes

        # Filter to child frames (skip the main frame which is always first)
        child_frames = [f for f in frames if f.get("parentId")]

        for frame_info in child_frames:
            frame_id = frame_info.get("id", "")
            if not frame_id:
                continue

            try:
                # Attach to the iframe target via the browser connection
                iframe_target = CDPTarget(
                    id=frame_id,
                    type="iframe",
                    title=frame_info.get("name", ""),
                    url=frame_info.get("url", ""),
                )
                iframe_session = self._browser.attach(iframe_target)
                self._sessions[f"iframe:{frame_id}"] = iframe_session

                iframe_acc = AccessibilityDomain(iframe_session)
                iframe_nodes = iframe_acc.get_full_ax_tree()

                # Prefix node IDs with frame ID to avoid collisions
                prefixed_nodes: list[AXNode] = []
                for node in iframe_nodes:
                    prefixed = AXNode(
                        node_id=f"{frame_id}:{node.node_id}",
                        role=node.role,
                        name=node.name,
                        description=node.description,
                        value=node.value,
                        backend_dom_node_id=node.backend_dom_node_id,
                        child_ids=tuple(f"{frame_id}:{cid}" for cid in node.child_ids),
                        bounds=node.bounds,
                        properties=node.properties,
                        raw=node.raw,
                    )
                    prefixed_nodes.append(prefixed)

                all_iframe_nodes.extend(prefixed_nodes)

            except Exception:
                logger.debug(
                    "Failed to collect AX tree for iframe %s",
                    frame_id,
                    exc_info=True,
                )

        return all_iframe_nodes

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
            _, dom, _, _, _ = self._get_domains(session.target.id)
            dom.scroll_into_view_if_needed(backend_node_id=node.backend_dom_node_id)
        except Exception:
            logger.debug("scroll_into_view failed for node %s", node.node_id, exc_info=True)

    # -- Action dispatch helpers (GW-096) --------------------------------------

    def _stale_check(self, node_id: str) -> None:
        """Verify an element is still valid before dispatching an action.

        Performs a lightweight cache membership check.  If the node has
        been removed from the cache (e.g. by a DOM mutation or page
        navigation that triggered a new snapshot), raises
        :class:`StaleElementReferenceError`.

        Args:
            node_id: The AX node ID to verify.

        Raises:
            StaleElementReferenceError: If the node is no longer in the cache.
        """
        if node_id not in self._ax_cache:
            raise StaleElementReferenceError(
                f"AX node {node_id!r} is stale — no longer present in the cache. "
                "Take a new snapshot and re-resolve the element reference."
            )

    def _resolve_bounds(self, node: AXNode, dom: DOMDomain) -> dict[str, float] | None:
        """Resolve element bounds with lazy caching (GW-096).

        Checks the AX node inline bounds first, then the lazy bounds cache,
        and finally fetches from the DOM domain (caching the result for
        subsequent calls).

        Args:
            node: The :class:`AXNode` to resolve bounds for.
            dom: :class:`DOMDomain` for DOM-based bounds resolution.

        Returns:
            Bounds dict ``{x, y, width, height}`` or ``None``.
        """
        # 1. Check AX node inline bounds
        if node.bounds is not None:
            return node.bounds

        # 2. Check the lazy bounds cache
        cached = self._bounds_cache.get(node.node_id)
        if cached is not None:
            return cached

        # 3. Fetch from DOM domain and cache the result
        if node.backend_dom_node_id is not None:
            bounds_data = fetch_bounds_from_dom(dom, node.backend_dom_node_id)
            if bounds_data is not None:
                self._bounds_cache[node.node_id] = bounds_data
                return bounds_data

        return None

    def _action_click(self, node: AXNode, dom: DOMDomain, inp: InputDomain, **kwargs: Any) -> None:
        """Click an element at its center coordinates.

        Resolves the element bounds (from AX node, cache, or DOM box model)
        and dispatches a mouse press/release at the center point.

        Supports double-click via ``click_count=2`` kwarg.

        Args:
            node: The :class:`AXNode` to click.
            dom: :class:`DOMDomain` for bounds resolution.
            inp: :class:`InputDomain` for mouse dispatch.
            **kwargs: Optional ``click_count`` (int, default 1).

        Raises:
            ActionNotSupportedError: If bounds cannot be determined.
        """
        bounds = self._resolve_bounds(node, dom)

        if bounds is None:
            raise ActionNotSupportedError(
                f"Cannot determine bounds for click on node {node.node_id!r}"
            )

        x = float(bounds.get("x", 0)) + float(bounds.get("width", 0)) / 2
        y = float(bounds.get("y", 0)) + float(bounds.get("height", 0)) / 2
        click_count = int(kwargs.get("click_count", 1))

        inp.dispatch_mouse_event("mousePressed", x, y, button="left", click_count=click_count)
        inp.dispatch_mouse_event("mouseReleased", x, y, button="left", click_count=click_count)

    def _action_type(self, node: AXNode, dom: DOMDomain, inp: InputDomain, **kwargs: Any) -> None:
        """Type text into an element via focus + ``Input.insertText``.

        Focuses the element via the DOM domain (if it has a backend DOM node)
        before inserting text.  When ``clear=True`` is passed, selects all
        existing text and deletes it before typing.

        Args:
            node: The :class:`AXNode` to type into.
            dom: :class:`DOMDomain` for focusing the element.
            inp: :class:`InputDomain` for text insertion.
            **kwargs: Must contain ``text`` (str).  Optional ``clear`` (bool).

        Raises:
            ActionNotSupportedError: If text parameter is missing.
        """
        text = kwargs.get("text")
        if text is None:
            raise ActionNotSupportedError("TYPE action requires a 'text' parameter")

        # Focus the element before typing (common web pattern)
        if node.backend_dom_node_id is not None:
            try:
                dom.focus(backend_node_id=node.backend_dom_node_id)
            except Exception:
                logger.debug(
                    "DOM focus before type failed for node %s",
                    node.node_id,
                    exc_info=True,
                )

        # Optionally clear existing text (GW-096)
        if kwargs.get("clear"):
            # Select all (Ctrl+A) then delete
            inp.dispatch_key_event("keyDown", key="a", modifiers=2)  # Ctrl+A
            inp.dispatch_key_event("keyUp", key="a", modifiers=2)
            inp.dispatch_key_event("keyDown", key="Backspace")
            inp.dispatch_key_event("keyUp", key="Backspace")

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

    def _action_scroll(self, node: AXNode, dom: DOMDomain, inp: InputDomain, **kwargs: Any) -> None:
        """Scroll an element via mouse wheel at its center.

        Supports directional scrolling via the ``direction`` kwarg:
        ``"down"`` (default), ``"up"``, ``"left"``, ``"right"``.
        The scroll delta magnitude can be set via ``delta`` (default 100).

        Uses the lazy bounds cache to resolve coordinates (GW-096).

        Args:
            node: The :class:`AXNode` to scroll.
            dom: :class:`DOMDomain` for bounds resolution.
            inp: :class:`InputDomain` for mouse wheel dispatch.
            **kwargs: Optional ``direction`` (str), ``delta`` (float).
        """
        bounds = self._resolve_bounds(node, dom)
        if bounds is None:
            bounds = node.bounds
        if bounds is None:
            return

        x = float(bounds.get("x", 0)) + float(bounds.get("width", 0)) / 2
        y = float(bounds.get("y", 0)) + float(bounds.get("height", 0)) / 2

        direction = kwargs.get("direction", "down")
        delta = float(kwargs.get("delta", 100.0))

        delta_x = 0.0
        delta_y = 0.0
        if direction == "down":
            delta_y = delta
        elif direction == "up":
            delta_y = -delta
        elif direction == "right":
            delta_x = delta
        elif direction == "left":
            delta_x = -delta

        inp.dispatch_mouse_event(
            "mouseWheel", x, y, button="none", delta_x=delta_x, delta_y=delta_y
        )

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
