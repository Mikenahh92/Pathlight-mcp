"""LinuxBackend — AT-SPI2 accessibility backend for Linux (architecture v2 §4.2).

Uses the ``pyatspi`` bindings to communicate with the AT-SPI2 accessibility
framework via the D-Bus accessibility registry.  The ``pyatspi`` import is
guarded so that this module can be imported on non-Linux platforms without
error (the constructor will raise :class:`BackendUnavailableError` at
instantiation time).

Window activation uses AT-SPI ``activate`` action as the primary path with a
``python-xlib`` ``_NET_ACTIVE_WINDOW`` EWMH fallback for window managers that
do not respond to AT-SPI activation (GW-030).  The xlib fallback is extracted
to :mod:`guidewire.backends._xlib_focus` per architecture §3.2 and §10.

Post-activation verification (AC-5, architecture §4.1 step 4) confirms that
the target accessible reports ``STATE_ACTIVE`` or ``STATE_FOCUSED`` after each
activation attempt.

Implementation status:
- ``list_windows`` — implemented (GW-029)
- ``snapshot`` — implemented (GW-031)
- ``focus_window`` — implemented (GW-030)
- ``get_window_info`` — pending
- ``find_elements`` — implemented (GW-032)
- ``perform_action`` — implemented (GW-032)
- ``get_element_info`` — implemented (GW-032)
- ``is_valid`` — implemented (GW-033)
"""

import contextlib
import logging
import sys
from typing import Any

from guidewire.backends.base import DesktopBackend
from guidewire.backends.normalize import normalize_element
from guidewire.backends.types import DesktopAction, NativeHandle
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
    "LinuxBackend",
]


class LinuxBackend(DesktopBackend):
    """AT-SPI2-based accessibility backend for the Linux desktop.

    Wraps the ``pyatspi`` library to enumerate windows, walk the
    accessibility tree, and perform actions on elements via the AT-SPI2
    D-Bus registry.

    Raises:
        BackendUnavailableError: If the platform is not Linux or the
            ``pyatspi`` package is not installed.

    Usage::

        from guidewire.backends import LinuxBackend

        backend = LinuxBackend()
        windows = backend.list_windows()
    """

    def __init__(self) -> None:
        if sys.platform != "linux":
            raise BackendUnavailableError(
                f"LinuxBackend requires the Linux platform (current: {sys.platform!r})"
            )
        try:
            import pyatspi  # availability check
        except ImportError:
            raise BackendUnavailableError(
                "LinuxBackend requires the 'pyatspi' package "
                "(install via system package manager: apt install python3-pyatspi)"
            ) from None
        self._disposed: bool = False

        import pyatspi

        self._desktop = pyatspi.Registry.getDesktop(0)

    # -- DesktopBackend interface (16 abstract methods) -------------------------

    def list_windows(self) -> list[NativeHandle]:
        """List all visible top-level application windows.

        Uses ``self._desktop`` (stored at construction) to iterate the
        AT-SPI2 desktop children, collecting only those that:

        1. Have AT-SPI2 role ``ROLE_FRAME``, ``ROLE_DIALOG``, or
           ``ROLE_WINDOW`` (Architecture §5.3).
        2. Include the ``STATE_SHOWING`` state (off-screen / hidden
           windows are filtered out).
        3. Are not ``ROLE_DESKTOP_FRAME`` entries with an empty name
           (Architecture §5.3).

        Defunct or inaccessible children are silently skipped per
        Architecture §5.4.

        Returns:
            List of opaque native window handles (``pyatspi.Accessible``
            objects wrapped in :class:`NativeHandle`).

        Raises:
            BackendUnavailableError: If the backend is disposed.
        """
        if self._disposed:
            raise BackendUnavailableError("LinuxBackend has been disposed")

        import pyatspi

        _valid_roles = {
            pyatspi.ROLE_FRAME,
            pyatspi.ROLE_DIALOG,
            pyatspi.ROLE_WINDOW,
        }

        result: list[NativeHandle] = []

        def _collect_windows(accessible: Any) -> None:
            """Recursively collect windows from accessible children."""
            try:
                child_count = accessible.childCount
            except Exception:
                return
            for i in range(child_count):
                try:
                    child = accessible.getChildAtIndex(i)
                except Exception:
                    logger.debug("Skipping inaccessible child %d", i, exc_info=True)
                    continue
                if child is None:
                    continue
                try:
                    role = child.get_role()
                    # Desktop → Application → Window nesting: applications
                    # have ROLE_APPLICATION.  Recurse into them to find their
                    # windows.  Desktop frames (e.g. the desktop background)
                    # are also traversed so that top-level panels are found.
                    if role in (
                        pyatspi.ROLE_APPLICATION,
                        pyatspi.ROLE_DESKTOP_FRAME,
                    ):
                        _collect_windows(child)
                        continue
                    state_set = child.getState()
                    if not state_set.contains(pyatspi.STATE_SHOWING):
                        continue
                    if role not in _valid_roles:
                        continue
                    result.append(NativeHandle(child))
                except Exception:
                    logger.debug("Skipping inaccessible child %d", i, exc_info=True)
                    continue

        _collect_windows(self._desktop)
        return result

    def get_window_info(self, window: NativeHandle) -> dict[str, Any]:
        """Return window metadata as a dict.

        Reads the accessible name, application name, focus state, and
        bounding box from the underlying ``pyatspi.Accessible``.

        Args:
            window: Opaque native window handle.

        Returns:
            Dict with keys ``title``, ``app_name``, ``focused``, ``bounds``.

        Raises:
            WindowNotFoundError: If the handle is invalid.
        """
        import pyatspi

        accessible = self._resolve_accessible(window)

        # Title — accessible name
        title = ""
        with contextlib.suppress(Exception):
            title = accessible.get_name() or ""

        # Application name — from the Application accessible
        app_name = ""
        with contextlib.suppress(Exception):
            app = accessible.getApplication()
            if app is not None:
                app_name = app.get_name() or ""

        # Focused — check STATE_ACTIVE or STATE_FOCUSED
        focused = False
        with contextlib.suppress(Exception):
            state_set = accessible.getState()
            focused = state_set.contains(pyatspi.STATE_ACTIVE) or state_set.contains(
                pyatspi.STATE_FOCUSED
            )

        # Bounds — from getExtent
        bounds: dict[str, int] | None = None
        with contextlib.suppress(Exception):
            ext = accessible.getExtent(0)
            if ext and hasattr(ext, "x"):
                bounds = {
                    "x": int(ext.x),
                    "y": int(ext.y),
                    "width": int(ext.width),
                    "height": int(ext.height),
                }

        return {
            "title": title,
            "app_name": app_name,
            "focused": focused,
            "bounds": bounds,
        }

    def focus_window(self, window: NativeHandle) -> None:
        """Bring a window to the foreground.

        Activates the window using the AT-SPI ``activate`` action.  If the
        AT-SPI action is not supported or fails, falls back to sending a
        ``_NET_ACTIVE_WINDOW`` EWMH client message via ``python-xlib``.

        After each activation attempt, post-activation verification checks
        whether the target accessible reports ``STATE_ACTIVE`` or
        ``STATE_FOCUSED`` (AC-5, architecture §4.1 step 4).  If the check
        fails the method falls through to the next mechanism.

        Args:
            window: Opaque native window handle (a ``pyatspi.Accessible``).

        Raises:
            WindowNotFoundError: If the handle is invalid or the accessible
                has been destroyed.
            ActionNotSupportedError: If neither AT-SPI activate nor the xlib
                fallback is available, or activation succeeds but focus is
                not acquired.
        """
        accessible = self._resolve_accessible(window)

        # Collect diagnostic context for the error message (GW-078)
        acc_name = ""
        acc_role = ""
        with contextlib.suppress(Exception):
            acc_name = accessible.get_name() or ""
        with contextlib.suppress(Exception):
            role = accessible.get_role()
            acc_role = role if isinstance(role, str) else str(role)

        atspi_tried = False
        atspi_reason = ""
        xlib_tried = False
        xlib_reason = ""
        xlib_unavailable = False

        # -- Primary path: AT-SPI activate action ----------------------------
        try:
            action = self._get_action(accessible, "activate")
            if action is not None:
                atspi_tried = True
                action.doAction(0)
                logger.debug("Activated window via AT-SPI activate: %s", window)
                if self._verify_focus(accessible):
                    return
                atspi_reason = (
                    "post-activation verification failed "
                    "(STATE_ACTIVE/STATE_FOCUSED not acquired)"
                )
                logger.debug("Post-activation verification failed after AT-SPI activate")
            else:
                atspi_reason = "activate action not found in AT-SPI action list"
        except Exception as exc:
            atspi_tried = True
            atspi_reason = str(exc)
            logger.debug("AT-SPI activate failed: %s", exc)

        # -- Fallback: _NET_ACTIVE_WINDOW via python-xlib --------------------
        try:
            self._xlib_activate(accessible)
            xlib_tried = True
            logger.debug("Activated window via xlib fallback: %s", window)
            if self._verify_focus(accessible):
                return
            xlib_reason = (
                "post-activation verification failed "
                "(STATE_ACTIVE/STATE_FOCUSED not acquired)"
            )
            logger.debug("Post-activation verification failed after xlib fallback")
        except ImportError:
            xlib_unavailable = True
            xlib_reason = "python-xlib not installed (install via: pip install python-xlib)"
            logger.debug("python-xlib not available; cannot use EWMH fallback")
        except Exception as exc:
            xlib_tried = True
            xlib_reason = str(exc)
            logger.debug("xlib fallback failed: %s", exc)

        # GW-078: Build diagnostic error message with activation path details
        parts = ["focus_window failed"]
        if acc_name:
            parts.append(f"window={acc_name!r}")
        if acc_role:
            parts.append(f"role={acc_role!r}")

        atspi_detail = (
            f"attempted, {atspi_reason}"
            if atspi_tried and atspi_reason
            else "attempted" if atspi_tried
            else f"skipped ({atspi_reason})" if atspi_reason
            else "not available"
        )
        parts.append(f"AT-SPI activate: {atspi_detail}")

        xlib_detail = (
            f"attempted, {xlib_reason}"
            if xlib_tried and xlib_reason
            else "attempted" if xlib_tried
            else "unavailable" if xlib_unavailable
            else f"failed ({xlib_reason})" if xlib_reason
            else "not attempted"
        )
        parts.append(f"xlib fallback: {xlib_detail}")
        parts.append("install python-xlib for EWMH fallback: pip install python-xlib")

        raise ActionNotSupportedError("; ".join(parts))

    # -- Private helpers for focus_window ------------------------------------

    @staticmethod
    def _resolve_accessible(handle: NativeHandle) -> Any:
        """Validate that *handle* wraps a live ``pyatspi.Accessible``.

        Returns the underlying accessible object.

        Raises:
            WindowNotFoundError: If the handle is not a pyatspi Accessible
                or has been destroyed.
        """
        import pyatspi

        if not isinstance(handle, pyatspi.Accessible):
            raise WindowNotFoundError(f"Window handle {handle!r} is not a valid pyatspi Accessible")
        try:
            # Accessing ``name`` triggers a D-Bus round-trip; if the
            # object has been destroyed this will raise.
            _ = handle.name
        except Exception:
            raise WindowNotFoundError(
                f"Window handle {handle!r} refers to a destroyed accessible"
            ) from None
        return handle

    @staticmethod
    def _get_action(accessible: Any, action_name: str) -> Any:
        """Return the AT-SPI action object for *action_name*, or ``None``.

        Real AT-SPI registries expose D-Bus prefixed names (e.g.
        ``'default.activate'`` instead of bare ``'activate'``).  Matching
        uses :func:`str.endswith` on the suffix ``.<action_name>`` so that
        both ``'activate'`` and ``'default.activate'`` (or any other
        D-Bus prefix) are accepted.
        """
        suffix = f".{action_name}"
        try:
            action_interface = accessible.get_action()
            if action_interface is None:
                return None
            for i in range(action_interface.get_n_actions()):
                name = action_interface.get_action_name(i)
                if name == action_name or name.endswith(suffix):
                    return action_interface
        except Exception:
            pass
        return None

    @staticmethod
    def _verify_focus(accessible: Any) -> bool:
        """Check whether *accessible* reports STATE_ACTIVE or STATE_FOCUSED.

        This is the AC-5 post-activation verification (architecture §4.1 step 4).
        Returns ``True`` if focus is confirmed, ``False`` otherwise.

        Args:
            accessible: A ``pyatspi.Accessible`` to query.

        Returns:
            ``True`` if the accessible has STATE_ACTIVE or STATE_FOCUSED.
        """
        import pyatspi

        try:
            state_set = accessible.get_state_set()
            if state_set.contains(pyatspi.STATE_ACTIVE) or state_set.contains(
                pyatspi.STATE_FOCUSED
            ):
                logger.debug("Post-activation verification passed: %s", accessible)
                return True
        except Exception as exc:
            logger.debug("State query failed during verification: %s", exc)
        return False

    @staticmethod
    def _xlib_activate(accessible: Any) -> None:
        """Send ``_NET_ACTIVE_WINDOW`` via the extracted _xlib_focus module.

        Delegates to :func:`guidewire.backends._xlib_focus.xlib_activate`.

        Raises:
            ImportError: If ``python-xlib`` is not installed.
            Exception: If the xlib activation fails.
        """
        from guidewire.backends._xlib_focus import xlib_activate

        xlib_activate(accessible)

    def snapshot(
        self,
        window: NativeHandle,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """Return an accessibility snapshot as a tree dict (GW-031).

        Walks the AT-SPI accessibility tree rooted at *window* using
        ``pyatspi.Accessible`` child iteration, extracting role, name,
        states, value, bounds, and actions from each accessible node.
        Properties are normalized via the cross-platform mapping tables
        in :mod:`guidewire.models.mappings`.

        Args:
            window: Opaque native window handle (``pyatspi.Accessible``).
            max_depth: Maximum tree depth to traverse (default 4).
            max_nodes: Maximum number of nodes to include (default 500).

        Returns:
            Dict matching the DesktopElement schema with keys:
            ``ref``, ``role``, ``name``, ``states``, ``bounds``,
            ``actions``, ``children``.

        Raises:
            BackendUnavailableError: If the backend is disposed.
        """
        if self._disposed:
            raise BackendUnavailableError("LinuxBackend has been disposed")

        accessible = window
        node = self._walk_tree(accessible, max_depth=max_depth, max_nodes=max_nodes)
        if node is None:
            fallback = NormalizedElement(
                ref=str(id(accessible)),
                backend_id="",
                role="unknown",
            )
            return fallback.to_dict()
        return node.to_dict()

    # -- Internal snapshot helpers --------------------------------------------

    def _extract_element_node(self, accessible: Any) -> NormalizedElement:
        """Extract properties from a single ``pyatspi.Accessible``.

        Reads role, name, description, value (via Text interface),
        states, bounds, and actions, then normalizes via
        :func:`~guidewire.backends.normalize.normalize_element`.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Returns:
            A :class:`~guidewire.models.NormalizedElement` with all fields
            populated from the accessible node.
        """
        # --- Role ---
        role_name = ""
        try:
            role = accessible.get_role()
            role_name = role if isinstance(role, str) else str(role)
        except Exception:
            pass

        # --- Name ---
        name = None
        with contextlib.suppress(Exception):
            name = accessible.get_name() or None

        # --- Description ---
        description = None
        with contextlib.suppress(Exception):
            description = accessible.get_description() or None

        # --- Value (via Text interface) ---
        value = None
        text_content = None
        with contextlib.suppress(Exception):
            text_iface = accessible.get_text()
            if text_iface is not None:
                text_content = text_iface.getText(0, text_iface.characterCount) or None

        # Also try Value interface for single-value widgets (sliders, spinboxes)
        with contextlib.suppress(Exception):
            value_iface = accessible.get_value()
            if value_iface is not None:
                value = value_iface.currentValue or None

        # --- States ---
        # Use contains() to capture both True and False values for each
        # known state, matching the pattern in get_element_info.  This
        # ensures disabled elements report enabled=False instead of
        # enabled=None (unknown), producing platform-identical output
        # to the Windows backend.
        raw_states: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            state_set = accessible.getState()

            import pyatspi

            _state_key_to_constant = {
                "enabled": pyatspi.STATE_ENABLED,
                "focused": pyatspi.STATE_FOCUSED,
                "selected": pyatspi.STATE_SELECTED,
                "checked": pyatspi.STATE_CHECKED,
                "expanded": pyatspi.STATE_EXPANDED,
                "showing": pyatspi.STATE_SHOWING,
                "visible": pyatspi.STATE_VISIBLE,
                "offscreen": pyatspi.STATE_OFFSCREEN,
                "read-only": pyatspi.STATE_READ_ONLY,
                "required": pyatspi.STATE_REQUIRED,
                "editable": pyatspi.STATE_EDITABLE,
                "indeterminate": pyatspi.STATE_INDETERMINATE,
            }

            for state_key, state_const in _state_key_to_constant.items():
                try:
                    if state_set.contains(state_const):
                        raw_states[state_key] = True
                    else:
                        raw_states[state_key] = False
                except Exception:
                    pass

        # AT-SPI has no dedicated is_password state.  Detect password
        # fields from the role name so that consumers get the same
        # is_password=True signal the Windows backend provides.
        if role_name == "password text":
            raw_states["is_password"] = True

        # --- Bounds ---
        raw_bounds: tuple[int, int, int, int] | None = None
        with contextlib.suppress(Exception):
            ext = accessible.getExtent(0)
            if ext and hasattr(ext, "x"):
                raw_bounds = (int(ext.x), int(ext.y), int(ext.width), int(ext.height))

        # --- Actions ---
        raw_actions: list[str] = []
        with contextlib.suppress(Exception):
            action_iface = accessible.get_action()
            if action_iface is not None:
                n_actions = action_iface.get_n_actions()
                for i in range(n_actions):
                    action_name = action_iface.get_action_name(i)
                    if action_name:
                        raw_actions.append(action_name)

        return normalize_element(
            platform="linux",
            ref=str(id(accessible)),
            backend_id=str(id(accessible)),
            role=role_name,
            native_role=role_name if role_name else None,
            name=name,
            description=description,
            value=value,
            text=text_content,
            raw_states=raw_states,
            bounds=raw_bounds,
            raw_actions=raw_actions,
        )

    def _walk_tree(
        self,
        root_accessible: Any,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> NormalizedElement | None:
        """Recursively walk the AT-SPI tree and build a snapshot dict.

        The root element is always included regardless of offscreen state.
        Offscreen filtering only applies to descendant nodes.

        Args:
            root_accessible: The root ``pyatspi.Accessible`` to walk from.
            max_depth: Maximum depth to traverse.
            max_nodes: Maximum total nodes (including root).

        Returns:
            Root :class:`NormalizedElement`, or ``None`` if extraction fails.
        """
        counter = [0]
        node = self._walk_recursive(root_accessible, 0, max_depth, counter, max_nodes)
        return node

    def _walk_recursive(
        self,
        accessible: Any,
        depth: int,
        max_depth: int,
        counter: list[int],
        max_nodes: int,
    ) -> NormalizedElement | None:
        """Recursively walk from *accessible* building a NormalizedElement tree.

        Returns ``None`` for offscreen descendant elements (depth > 0),
        which the caller filters out of the children list.  The root
        element (depth 0) is always included.

        Defunct or inaccessible nodes are silently skipped per
        Architecture §5.4.
        """
        if counter[0] >= max_nodes:
            return NormalizedElement(
                ref=str(id(accessible)),
                backend_id="",
                role="unknown",
            )

        # GW-078: On pyatspi 2.46 (Ubuntu 22.04), some offscreen elements
        # crash during D-Bus round-trips in getState() or property queries.
        # Pre-check offscreen state via a safe D-Bus probe so that we can
        # skip the node before calling _extract_element_node, which performs
        # many more D-Bus calls and is more likely to trigger the crash.
        if depth > 0 and self._is_likely_offscreen(accessible):
            return None

        try:
            counter[0] += 1
            node = self._extract_element_node(accessible)
        except Exception:
            logger.debug("Skipping inaccessible node at depth %d", depth, exc_info=True)
            counter[0] -= 1
            return None

        # Exclude offscreen descendants (but never the root at depth 0)
        if depth > 0 and node.states.offscreen is True:
            counter[0] -= 1
            return None

        if depth < max_depth:
            children: list[NormalizedElement] = []
            try:
                child_count = accessible.childCount
                for i in range(child_count):
                    if counter[0] >= max_nodes:
                        break
                    try:
                        child = accessible.getChildAtIndex(i)
                    except Exception:
                        logger.debug(
                            "Skipping inaccessible child %d at depth %d",
                            i,
                            depth,
                            exc_info=True,
                        )
                        continue
                    if child is None:
                        continue
                    child_node = self._walk_recursive(
                        child, depth + 1, max_depth, counter, max_nodes
                    )
                    if child_node is not None:
                        children.append(child_node)
            except Exception:
                logger.debug("Error walking children at depth %d", depth, exc_info=True)
            node.children = children

        return node

    @staticmethod
    def _is_likely_offscreen(accessible: Any) -> bool:
        """Check whether *accessible* is offscreen without triggering a crash.

        On pyatspi 2.46 (Ubuntu 22.04), ``state_set.contains(STATE_OFFSCREEN)``
        can raise a D-Bus exception for elements that are genuinely offscreen.
        This helper wraps the check safely and returns ``True`` only when the
        element explicitly reports ``STATE_OFFSCREEN``.

        Returns:
            ``True`` if the element reports STATE_OFFSCREEN, ``False`` otherwise
            (including when the state query fails — those are handled by the
            caller's own exception handling).
        """
        try:
            import pyatspi

            state_set = accessible.getState()
            return bool(state_set.contains(pyatspi.STATE_OFFSCREEN))
        except Exception:
            # Don't assume offscreen on error — let the caller's normal
            # exception handling deal with defunct/inaccessible nodes.
            return False

    def find_elements(
        self,
        window: NativeHandle,
        role: str | None = None,
        name: str | None = None,
    ) -> list[NativeHandle]:
        """Find elements matching criteria within a window (GW-032).

        Walks the AT-SPI tree rooted at *window* and collects elements whose
        normalized role and/or accessible name match the given filters.

        Args:
            window: Opaque native window handle (``pyatspi.Accessible``).
            role: Normalized role to match (exact).
            name: Accessible name to match (case-insensitive substring).

        Returns:
            List of matching opaque native element handles.
        """
        if self._disposed:
            raise BackendUnavailableError("LinuxBackend has been disposed")

        # Require at least one filter criterion
        if role is None and name is None:
            return []

        from guidewire.models.mappings import resolve_role

        results: list[NativeHandle] = []
        try:
            self._find_recursive(window, role, name, resolve_role, results)
        except Exception:
            logger.debug("Error in find_elements tree walk", exc_info=True)

        return results

    def _find_recursive(
        self,
        accessible: Any,
        role: str | None,
        name: str | None,
        resolve_fn: Any,
        results: list[NativeHandle],
    ) -> None:
        """Recursively search for elements matching *role* and/or *name*.

        No depth limit — architecture §3.3 requires exhaustive traversal.
        Defunct or inaccessible children are silently skipped.
        """
        try:
            accessible.get_child_at_index(0)
        except Exception:
            return

        idx = 0
        while True:
            try:
                child = accessible.get_child_at_index(idx)
            except Exception:
                break
            if child is None:
                break

            try:
                # Extract and normalize role
                atspi_role_name = child.getRoleName()
                normalized_role = resolve_fn("linux", atspi_role_name) or atspi_role_name

                # Extract name
                child_name = child.get_name()

                # Match criteria
                role_match = role is None or normalized_role == role
                name_match = name is None or (
                    child_name is not None and name.lower() in child_name.lower()
                )

                if role_match and name_match:
                    results.append(NativeHandle(child))

                # Recurse into children
                self._find_recursive(child, role, name, resolve_fn, results)
            except Exception:
                logger.debug("Skipping inaccessible element in find_elements", exc_info=True)

            idx += 1

    def perform_action(
        self,
        handle: NativeHandle,
        action: DesktopAction,
        **kwargs: Any,
    ) -> Any:
        """Perform an action on an element via AT-SPI2 actions (GW-032).

        Maps each :class:`DesktopAction` variant to the appropriate AT-SPI2
        action or accessibility interface:

        - ``CLICK`` → AT-SPI ``Action.doAction('click')`` or ``doAction(0)``
        - ``TYPE`` → ``grabFocus()`` then pynput keyboard simulation
        - ``PRESS_KEY`` → ``grabFocus()`` then pynput keyboard simulation
        - ``SET_VALUE`` → ``Action.doAction('edit')`` with value parameter
        - ``SELECT`` → AT-SPI ``Action.doAction('select')``
        - ``SELECT_ITEM`` → AT-SPI ``Selection.selectChild(getIndexInParent())``
        - ``DESELECT_ITEM`` → AT-SPI ``Selection.deselectChild(getIndexInParent())``
        - ``ADD_TO_SELECTION`` → AT-SPI ``Selection.selectChild(getIndexInParent())``
        - ``SCROLL`` → AT-SPI ``Action.doAction('scroll')``
        - ``GET_TEXT`` → ``Accessible.get_text()`` via Text interface
        - ``TOGGLE`` → AT-SPI ``Action.doAction('toggle')``
        - ``EXPAND`` → AT-SPI ``Action.doAction('expand')``
        - ``COLLAPSE`` → AT-SPI ``Action.doAction('collapse')``
        - ``INCREMENT`` → AT-SPI ``Action.doAction('increment')``
        - ``DECREMENT`` → AT-SPI ``Action.doAction('decrement')``

        Args:
            handle: Opaque native element handle (``pyatspi.Accessible``).
            action: The action to perform.
            **kwargs: Action-specific parameters:
                - ``text`` (str): text to type (for ``TYPE``)
                - ``value`` (str): value to set (for ``SET_VALUE``)
                - ``key`` (str): key to press (for ``PRESS_KEY``)

        Returns:
            ``str`` when action is ``GET_TEXT``, otherwise ``None``.

        Raises:
            StaleElementReferenceError: If the backend is disposed or the
                element is no longer accessible.
            ActionNotSupportedError: If the element does not support the
                required action.
            ElementNotFoundError: If the handle is ``None`` or invalid.
        """
        if self._disposed:
            raise StaleElementReferenceError("LinuxBackend has been disposed")

        accessible = self._unwrap_element(handle)

        try:
            if action == DesktopAction.CLICK:
                return self._action_click(accessible)
            if action == DesktopAction.TYPE:
                return self._action_type(accessible, **kwargs)
            if action == DesktopAction.PRESS_KEY:
                return self._action_press_key(accessible, **kwargs)
            if action == DesktopAction.SET_VALUE:
                return self._action_set_value(accessible, **kwargs)
            if action == DesktopAction.SELECT:
                return self._action_select(accessible)
            if action == DesktopAction.SELECT_ITEM:
                return self._action_select_item(accessible)
            if action == DesktopAction.DESELECT_ITEM:
                return self._action_deselect_item(accessible)
            if action == DesktopAction.ADD_TO_SELECTION:
                return self._action_add_to_selection(accessible)
            if action == DesktopAction.SCROLL:
                return self._action_scroll(accessible)
            if action == DesktopAction.GET_TEXT:
                return self._action_get_text(accessible)
            if action == DesktopAction.TOGGLE:
                return self._action_toggle(accessible)
            if action == DesktopAction.EXPAND:
                return self._action_expand(accessible)
            if action == DesktopAction.COLLAPSE:
                return self._action_collapse(accessible)
            if action == DesktopAction.INCREMENT:
                return self._action_increment(accessible)
            if action == DesktopAction.DECREMENT:
                return self._action_decrement(accessible)
            if action == DesktopAction.GET_TABLE_INFO:
                return self._dispatch_table_info(accessible, **kwargs)
        except (ActionNotSupportedError, StaleElementReferenceError):
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Failed to perform {action!r}: {exc}") from exc

    # -- Action dispatch helpers -----------------------------------------------

    @staticmethod
    def _unwrap_element(handle: NativeHandle) -> Any:
        """Extract the underlying ``pyatspi.Accessible`` from a NativeHandle.

        Args:
            handle: Opaque native element handle.

        Returns:
            The ``pyatspi.Accessible`` object.

        Raises:
            ElementNotFoundError: If the handle is ``None`` or empty.
        """
        if handle is None:
            raise ElementNotFoundError("Element handle is None")
        return handle

    def _get_action_interface(self, accessible: Any, action_name: str) -> Any:
        """Retrieve the AT-SPI Action interface from an accessible.

        Args:
            accessible: A ``pyatspi.Accessible`` object.
            action_name: The AT-SPI action name to look for.

        Returns:
            The ``pyatspi.Action`` interface object.

        Raises:
            ActionNotSupportedError: If the action interface is not available
                or the named action is not supported.
        """
        try:
            action_interface = accessible.queryAction()
        except Exception:
            raise ActionNotSupportedError("Element does not support the Action interface") from None

        if action_interface is None:
            raise ActionNotSupportedError("Element does not support the Action interface")

        # Check if the named action exists
        n_actions = action_interface.get_n_actions()
        for i in range(n_actions):
            try:
                if action_interface.get_action_name(i) == action_name:
                    return action_interface
            except Exception:
                continue

        raise ActionNotSupportedError(f"Element does not support the '{action_name}' action")

    def _do_action_by_name(self, accessible: Any, action_name: str) -> None:
        """Execute an AT-SPI action by name.

        Args:
            accessible: A ``pyatspi.Accessible`` object.
            action_name: The AT-SPI action name to execute.

        Raises:
            ActionNotSupportedError: If the action is not available.
        """
        action_interface = self._get_action_interface(accessible, action_name)
        try:
            action_interface.do_action(action_name)
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(f"Action '{action_name}' failed: {exc}") from exc

    def _action_click(self, accessible: Any) -> None:
        """Click an element via AT-SPI action.

        Tries 'click' action first, then falls back to 'press' and 'activate'.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If no click action is available.
        """
        # First check if the Action interface exists at all
        try:
            accessible.queryAction()
        except Exception:
            raise ActionNotSupportedError("Element does not support the Action interface") from None

        for action_name in ("click", "press", "activate"):
            try:
                self._do_action_by_name(accessible, action_name)
                return
            except ActionNotSupportedError:
                continue
        raise ActionNotSupportedError(
            "Element does not support any click action (click, press, activate)"
        )

    def _action_type(self, accessible: Any, **kwargs: Any) -> None:
        """Type text into an element.

        Sets focus on the element, then simulates keyboard input via
        ``pynput``.  Falls back to setting focus only if pynput is unavailable.

        Args:
            accessible: A ``pyatspi.Accessible`` object.
            **kwargs: Must contain ``text`` (str).

        Raises:
            ActionNotSupportedError: If text parameter is missing.
        """
        text = kwargs.get("text")
        if text is None:
            raise ActionNotSupportedError("TYPE action requires a 'text' parameter")

        # Set focus on the element
        self._grab_focus(accessible)

        # Simulate keyboard input via pynput
        self._send_text(str(text))

    def _action_press_key(self, accessible: Any, **kwargs: Any) -> None:
        """Press a key while an element has focus.

        Sets focus on the element, then simulates the key press via
        ``pynput``.

        Args:
            accessible: A ``pyatspi.Accessible`` object.
            **kwargs: Must contain ``keys`` (str) — normalised key combo
                from the press_key tool layer (e.g. ``"ctrl+s"``,
                ``"enter"``).

        Raises:
            ActionNotSupportedError: If keys parameter is missing.
        """
        # Accept both "keys" (tool-layer contract) and "key" (legacy)
        key = kwargs.get("keys") or kwargs.get("key")
        if key is None:
            raise ActionNotSupportedError("PRESS_KEY action requires a 'keys' parameter")

        # Set focus on the element
        self._grab_focus(accessible)

        # Simulate key press via pynput
        self._send_key(str(key))

    def _action_set_value(self, accessible: Any, **kwargs: Any) -> None:
        """Set the value of an element via AT-SPI action.

        Args:
            accessible: A ``pyatspi.Accessible`` object.
            **kwargs: Must contain ``value`` (str).

        Raises:
            ActionNotSupportedError: If value parameter is missing or the
                action is not supported.
        """
        value = kwargs.get("value")
        if value is None:
            raise ActionNotSupportedError("SET_VALUE action requires a 'value' parameter")

        # Try AT-SPI 'edit' action first
        for action_name in ("edit",):
            try:
                action_interface = self._get_action_interface(accessible, action_name)
                try:
                    action_interface.do_action(action_name)
                    return
                except Exception:
                    pass
            except ActionNotSupportedError:
                continue

        # Try setting via Text interface
        try:
            text_interface = accessible.queryText()
            if text_interface is not None:
                text_interface.set_text_content(str(value))
                return
        except Exception:
            pass

        raise ActionNotSupportedError(
            "Element does not support setting value (no edit action or Text interface)"
        )

    def _action_select(self, accessible: Any) -> None:
        """Select an element via AT-SPI action.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If select action is not available.
        """
        self._do_action_by_name(accessible, "select")

    def _action_select_item(self, accessible: Any) -> None:
        """Select an item via the AT-SPI Selection interface.

        Resolves the child index via ``getIndexInParent()`` and calls
        ``querySelection().selectChild(index)`` per Architecture Decision 1.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If the Selection interface is not available.
        """
        try:
            selection = accessible.querySelection()
        except Exception:
            raise ActionNotSupportedError(
                "Element does not support the AT-SPI Selection interface"
            ) from None
        try:
            index = accessible.getIndexInParent()
            selection.selectChild(index)
        except Exception as exc:
            raise ActionNotSupportedError(f"selectChild failed: {exc}") from exc

    def _action_deselect_item(self, accessible: Any) -> None:
        """Deselect an item via the AT-SPI Selection interface.

        Resolves the child index via ``getIndexInParent()`` and calls
        ``querySelection().deselectChild(index)`` per Architecture Decision 1.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If the Selection interface is not available.
        """
        try:
            selection = accessible.querySelection()
        except Exception:
            raise ActionNotSupportedError(
                "Element does not support the AT-SPI Selection interface"
            ) from None
        try:
            index = accessible.getIndexInParent()
            selection.deselectChild(index)
        except Exception as exc:
            raise ActionNotSupportedError(f"deselectChild failed: {exc}") from exc

    def _action_add_to_selection(self, accessible: Any) -> None:
        """Add an item to the current selection via the AT-SPI Selection interface.

        Resolves the child index via ``getIndexInParent()`` and calls
        ``querySelection().selectChild(index)`` per Architecture Decision 1.
        Unlike ``_action_select_item``, this does not clear the existing selection.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If the Selection interface is not available.
        """
        try:
            selection = accessible.querySelection()
        except Exception:
            raise ActionNotSupportedError(
                "Element does not support the AT-SPI Selection interface"
            ) from None
        try:
            index = accessible.getIndexInParent()
            selection.selectChild(index)
        except Exception as exc:
            raise ActionNotSupportedError(f"selectChild (add-to-selection) failed: {exc}") from exc

    def _action_scroll(self, accessible: Any) -> None:
        """Scroll an element via AT-SPI action.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If scroll action is not available.
        """
        for action_name in ("scroll", "scrollUp", "scrollDown", "scrollLeft", "scrollRight"):
            try:
                self._do_action_by_name(accessible, action_name)
                return
            except ActionNotSupportedError:
                continue
        raise ActionNotSupportedError("Element does not support any scroll action")

    def _action_get_text(self, accessible: Any) -> str:
        """Get the text value of an element.

        Tries the AT-SPI Text interface first, then falls back to the
        accessible name.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Returns:
            The current text string.

        Raises:
            ActionNotSupportedError: If text cannot be retrieved.
        """
        # Try the Text interface first
        try:
            text_interface = accessible.queryText()
            if text_interface is not None:
                char_count = text_interface.character_count
                text = text_interface.get_text(0, char_count)
                return str(text) if text else ""
        except Exception:
            pass

        # Fall back to accessible name
        try:
            name = accessible.get_name()
            return str(name) if name else ""
        except Exception as exc:
            raise ActionNotSupportedError(f"Failed to get text: {exc}") from exc

    def _action_toggle(self, accessible: Any) -> None:
        """Toggle an element via AT-SPI action.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If toggle action is not available.
        """
        self._do_action_by_name(accessible, "toggle")

    def _action_expand(self, accessible: Any) -> None:
        """Expand an element via AT-SPI action.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If expand action is not available.
        """
        self._do_action_by_name(accessible, "expand")

    def _action_collapse(self, accessible: Any) -> None:
        """Collapse an element via AT-SPI action.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If collapse action is not available.
        """
        self._do_action_by_name(accessible, "collapse")

    def _action_increment(self, accessible: Any) -> None:
        """Increment a value element via AT-SPI action.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If increment action is not available.
        """
        self._do_action_by_name(accessible, "increment")

    def _action_decrement(self, accessible: Any) -> None:
        """Decrement a value element via AT-SPI action.

        Args:
            accessible: A ``pyatspi.Accessible`` object.

        Raises:
            ActionNotSupportedError: If decrement action is not available.
        """
        self._do_action_by_name(accessible, "decrement")

    # -- Keyboard simulation helpers -------------------------------------------

    @staticmethod
    def _grab_focus(accessible: Any) -> None:
        """Set accessibility focus on an element.

        Uses the AT-SPI ``grabFocus`` action if available, otherwise
        uses the ``setState`` method.

        Args:
            accessible: A ``pyatspi.Accessible`` object.
        """
        try:
            action = accessible.queryAction()
            if action is not None:
                n_actions = action.get_n_actions()
                for i in range(n_actions):
                    if action.get_action_name(i) == "grabFocus":
                        action.do_action(i)
                        return
        except Exception:
            pass

    @staticmethod
    def _send_text(text: str) -> None:
        """Simulate typing text via ``pynput`` keyboard simulation.

        Falls back silently if pynput is not available (Linux server
        environments may lack a display).

        Args:
            text: The text string to type.
        """
        try:
            from pynput.keyboard import Controller

            keyboard = Controller()
            keyboard.type(text)
        except Exception:
            logger.debug("pynput keyboard simulation unavailable", exc_info=True)

    @staticmethod
    def _send_key(key: str) -> None:
        """Simulate a key press via ``pynput`` keyboard simulation.

        Supports single keys (``"enter"``, ``"tab"``, ``"a"``) and
        ``+``-delimited modifier combos (``"ctrl+s"``, ``"ctrl+shift+p"``,
        ``"alt+f4"``).

        Falls back silently if pynput is not available.

        Args:
            key: The key name or combo (e.g. ``"enter"``, ``"ctrl+s"``,
                ``"ctrl+shift+p"``).
        """
        try:
            from pynput.keyboard import Controller, Key

            keyboard = Controller()
            key_map: dict[str, Any] = {
                "enter": Key.enter,
                "tab": Key.tab,
                "escape": Key.esc,
                "backspace": Key.backspace,
                "delete": Key.delete,
                "arrowup": Key.up,
                "arrowdown": Key.down,
                "arrowleft": Key.left,
                "arrowright": Key.right,
                "home": Key.home,
                "end": Key.end,
                "pageup": Key.page_up,
                "pagedown": Key.page_down,
                "space": Key.space,
                "f1": Key.f1,
                "f2": Key.f2,
                "f3": Key.f3,
                "f4": Key.f4,
                "f5": Key.f5,
                "f6": Key.f6,
                "f7": Key.f7,
                "f8": Key.f8,
                "f9": Key.f9,
                "f10": Key.f10,
                "f11": Key.f11,
                "f12": Key.f12,
            }

            modifier_map: dict[str, Any] = {
                "ctrl": Key.ctrl,
                "shift": Key.shift,
                "alt": Key.alt,
                "super": Key.cmd,
            }

            # GW-078: Parse ``+``-delimited modifier combos.  The tool layer
            # normalises combos like "Ctrl+Shift+P" → "ctrl+shift+p", so we
            # split on ``+`` and hold all modifier keys while pressing the
            # final (non-modifier) key.
            parts = key.lower().split("+")
            if len(parts) > 1:
                # Separate modifier keys from the final key
                held_keys: list[Any] = []
                final_key = parts[-1]
                for part in parts[:-1]:
                    mod = modifier_map.get(part)
                    if mod is not None:
                        held_keys.append(mod)

                # Resolve the final key
                resolved_final = key_map.get(final_key)
                if resolved_final is None and len(final_key) == 1:
                    resolved_final = final_key

                if resolved_final is not None and held_keys:
                    # Hold all modifiers, press the key, release modifiers
                    for mod_key in held_keys:
                        keyboard.press(mod_key)
                    keyboard.press(resolved_final)
                    keyboard.release(resolved_final)
                    for mod_key in reversed(held_keys):
                        keyboard.release(mod_key)
                    return
                # If we can't resolve the combo, fall through to single-key
                # handling below (backward compatibility)

            mapped = key_map.get(key.lower())
            if mapped is not None:
                keyboard.press(mapped)
                keyboard.release(mapped)
            elif len(key) == 1:
                keyboard.press(key)
                keyboard.release(key)
        except Exception:
            logger.debug("pynput keyboard simulation unavailable", exc_info=True)

    def get_element_info(self, handle: NativeHandle) -> dict[str, Any]:
        """Return element metadata as a dict (GW-032).

        Reads the following AT-SPI2 properties from the accessible:
        - ``getRoleName()`` → normalized role via ``resolve_role``
        - ``get_name()`` → ``name``
        - State set → normalized states via ``normalize_states``

        Args:
            handle: Opaque native element handle (``pyatspi.Accessible``).

        Returns:
            Dict with keys ``role``, ``name``, ``states``.

        Raises:
            ElementNotFoundError: If the handle is ``None`` or invalid.
            StaleElementReferenceError: If the backend is disposed.
        """
        if self._disposed:
            raise StaleElementReferenceError("LinuxBackend has been disposed")

        accessible = self._unwrap_element(handle)

        try:
            from dataclasses import fields as _dc_fields

            from guidewire.backends.normalize import normalize_states
            from guidewire.models.mappings import resolve_role

            # Read role
            atspi_role_name = accessible.getRoleName()
            role = resolve_role("linux", atspi_role_name) or atspi_role_name

            # Read name
            name = accessible.get_name()
            name_str = str(name) if name else None

            # Read states from the AT-SPI state set
            # Only include states that map to ElementStates fields
            state_set = accessible.getState()
            raw_states: dict[str, Any] = {}

            import pyatspi

            state_key_to_constant = {
                "enabled": pyatspi.STATE_ENABLED,
                "focused": pyatspi.STATE_FOCUSED,
                "selected": pyatspi.STATE_SELECTED,
                "checked": pyatspi.STATE_CHECKED,
                "expanded": pyatspi.STATE_EXPANDED,
                "visible": pyatspi.STATE_VISIBLE,
                "showing": pyatspi.STATE_SHOWING,
                "offscreen": pyatspi.STATE_OFFSCREEN,
                "read-only": pyatspi.STATE_READ_ONLY,
                "required": pyatspi.STATE_REQUIRED,
            }

            for state_key, state_const in state_key_to_constant.items():
                try:
                    if state_set.contains(state_const):
                        raw_states[state_key] = True
                    else:
                        raw_states[state_key] = False
                except Exception:
                    pass

            norm_states = normalize_states("linux", raw_states)
            states_dict = {
                f.name: getattr(norm_states, f.name)
                for f in _dc_fields(norm_states)
                if getattr(norm_states, f.name) is not None
            }

            return {
                "role": role,
                "name": name_str,
                "states": states_dict,
            }
        except ElementNotFoundError:
            raise
        except StaleElementReferenceError:
            raise
        except Exception as exc:
            raise ElementNotFoundError(f"Failed to read element info: {exc}") from exc

    def _dispatch_table_info(self, accessible: Any, **kwargs: Any) -> dict[str, Any]:
        """Handle GET_TABLE_INFO dispatch via AT-SPI Table interface (GW-049).

        Reads table data using the AT-SPI Table interface and returns a plain
        dict based on the ``table_action`` kwarg.
        """
        max_rows = kwargs.get("max_rows", 100)
        max_columns = kwargs.get("max_columns", 50)
        table_action = kwargs.get("table_action", "info")
        row_idx = kwargs.get("row", 0)
        col_idx = kwargs.get("column", 0)

        try:
            table = accessible.queryTable()
        except Exception as exc:
            raise ActionNotSupportedError(
                "Element does not support AT-SPI Table interface for table access"
            ) from exc

        try:
            n_rows = int(table.nRows)
            n_cols = int(table.nColumns)

            # Read column headers
            headers: list[str | None] = []
            for c in range(min(n_cols, max_columns)):
                try:
                    header = table.getColumnHeader(c)
                    header_name = header.get_name() if header else None
                    headers.append(str(header_name) if header_name else None)
                except Exception:
                    headers.append(None)

            data_start_row = 1 if any(h is not None for h in headers) else 0

            def _read_cell(r: int, c: int) -> dict[str, Any]:
                try:
                    actual_r = r + data_start_row
                    cell_acc = table.getAccessibleAt(actual_r, c)
                    if cell_acc:
                        try:
                            text_iface = cell_acc.queryText()
                            value = text_iface.getText(0, -1)
                        except Exception:
                            value = cell_acc.get_name()
                        return {"row": r, "column": c, "value": str(value) if value else None}
                except Exception:
                    pass
                return {"row": r, "column": c, "value": None}

            if table_action == "info":
                effective_rows = min(n_rows - data_start_row, max_rows)
                result_rows: list[list[dict[str, Any]]] = []
                for r in range(effective_rows):
                    row_cells = [_read_cell(r, c) for c in range(min(n_cols, max_columns))]
                    result_rows.append(row_cells)
                return {
                    "row_count": n_rows - data_start_row,
                    "column_count": n_cols,
                    "headers": headers[:max_columns],
                    "rows": result_rows,
                }
            elif table_action == "read_cell":
                return _read_cell(row_idx, col_idx)
            elif table_action == "read_row":
                cells = [_read_cell(row_idx, c) for c in range(min(n_cols, max_columns))]
                return {"row": row_idx, "cells": cells}
            elif table_action == "read_column":
                effective_rows = min(n_rows - data_start_row, max_rows)
                col_cells = [_read_cell(r, col_idx) for r in range(effective_rows)]
                header = headers[col_idx] if col_idx < len(headers) else None
                return {"column": col_idx, "header": header, "cells": col_cells}
            else:
                raise ActionNotSupportedError(f"Unknown table_action: {table_action!r}")
        except ActionNotSupportedError:
            raise
        except Exception as exc:
            raise ActionNotSupportedError(
                f"Failed to read table data via AT-SPI Table: {exc}"
            ) from exc

    def is_valid(self, element: NativeHandle) -> bool:
        """Check whether a native element reference is still valid (GW-033).

        Uses a lightweight ``getState`` probe on the underlying
        ``pyatspi.Accessible`` to detect stale handles.  When the
        underlying AT-SPI object has been destroyed, any D-Bus property
        access raises, which this method catches and translates to
        ``False``.

        This method must **never** raise — the tool layer calls it outside
        any ``try / except`` block, so any exception would propagate as an
        unhandled MCP error.

        Args:
            element: Opaque native element handle (``pyatspi.Accessible``).

        Returns:
            ``True`` if the element still exists in the accessibility tree,
            ``False`` otherwise (including when the backend is disposed).
        """
        if self._disposed:
            return False

        if element is None:
            return False

        try:
            element.getState()
            return True
        except Exception:
            return False

    def clipboard_read(self) -> str:
        """Read text content from the system clipboard via xclip.

        Uses the xclip command-line utility to read the primary clipboard
        selection (-selection clipboard) as UTF-8 text.

        Raises:
            BackendUnavailableError: If xclip is not installed or the
                clipboard cannot be read.
        """
        import shutil
        import subprocess

        if shutil.which("xclip") is None:
            raise BackendUnavailableError(
                "LinuxBackend requires 'xclip' for clipboard access "
                "(install via: apt install xclip)"
            )

        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if "target" in stderr.lower() or "convert" in stderr.lower():
                    # No text content on clipboard
                    return ""
                raise BackendUnavailableError(
                    f"xclip failed with return code {result.returncode}: {stderr}"
                )
            return result.stdout
        except subprocess.TimeoutExpired as exc:
            raise BackendUnavailableError("xclip timed out while reading the clipboard") from exc
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise BackendUnavailableError(f"Failed to read clipboard via xclip: {exc}") from exc

    def clipboard_write(self, text: str) -> None:
        """Write text to the system clipboard using xclip.

        Pipes the text to xclip -selection clipboard via subprocess.
        Requires xclip to be installed on the system.

        Args:
            text: The text string to place on the OS clipboard.

        Raises:
            BackendUnavailableError: If xclip is not available or the
                operation fails.
        """
        import subprocess

        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                text=True,
                check=True,
                capture_output=True,
                timeout=5,
            )
        except FileNotFoundError:
            raise BackendUnavailableError("xclip is not installed or not found on PATH") from None
        except subprocess.TimeoutExpired:
            raise BackendUnavailableError("xclip timed out while writing to clipboard") from None
        except subprocess.CalledProcessError as exc:
            raise BackendUnavailableError(
                f"xclip failed with exit code {exc.returncode}: {exc.stderr}"
            ) from exc

    def dispose(self) -> None:
        """Release all resources held by this backend.

        Clears the desktop reference and marks the backend as disposed.

        Called when the MCP server shuts down.
        """
        if self._disposed:
            return
        self._desktop = None
        self._disposed = True

    def scroll_to_item(
        self,
        container: NativeHandle,
        *,
        item_name: str | None = None,
        item_index: int | None = None,
        max_retries: int = 10,
    ) -> NativeHandle | None:
        """Scroll a virtualized list to bring an item into view (GW-052).

        Uses a best-effort scroll-and-retry approach since AT-SPI does not
        have a standardized virtualization API.  Iterates visible children
        looking for the target, scrolling down when not found, up to
        *max_retries* times.

        Args:
            container: Native handle for the list/container element.
            item_name: Accessible name of the target item (case-insensitive
                substring match).
            item_index: Zero-based index of the target item.
            max_retries: Max scroll iterations (default 10).

        Returns:
            Native handle for the found item, or ``None``.

        Raises:
            ElementNotFoundError: If the container is invalid.
            ActionNotSupportedError: If neither item_name nor item_index
                is provided.
        """
        if self._disposed:
            raise StaleElementReferenceError("LinuxBackend has been disposed")

        accessible = self._unwrap_element(container)

        if item_name is None and item_index is None:
            raise ActionNotSupportedError("scroll_to_item requires either item_name or item_index")

        # Track seen names to detect when scrolling stops producing new items
        seen_names: set[str] = set()

        for attempt in range(max_retries):
            try:
                child_count = accessible.childCount
                visible_index = 0
                new_names: set[str] = set()

                for i in range(child_count):
                    try:
                        child = accessible.getChildAtIndex(i)
                        if child is None:
                            continue

                        child_name = child.get_name() or ""
                        new_names.add(child_name)

                        # Match by name
                        if (
                            item_name is not None
                            and child_name
                            and item_name.lower() in child_name.lower()
                        ):
                            return NativeHandle(child)

                        # Match by index
                        if item_index is not None and visible_index == item_index:
                            return NativeHandle(child)

                        visible_index += 1
                    except Exception:
                        logger.debug("Skipping inaccessible child %d in scroll_to_item", i)
                        continue

                # If no new items appeared, stop scrolling
                if new_names and new_names.issubset(seen_names):
                    logger.debug("No new items after scroll at attempt %d, stopping", attempt)
                    break
                seen_names.update(new_names)

            except Exception:
                logger.debug("Error scanning children, attempt %d", attempt)

            # Scroll down to reveal more items
            try:
                action_iface = accessible.get_action()
                if action_iface is not None:
                    # Try scrollDown action first
                    scrolled = False
                    for i in range(action_iface.get_n_actions()):
                        action_name = action_iface.get_action_name(i)
                        if action_name in ("scrollDown", "scroll"):
                            action_iface.doAction(i)
                            scrolled = True
                            break
                    if not scrolled:
                        break  # No scroll action available
                else:
                    break  # No action interface, can't scroll
            except Exception:
                logger.debug("Scroll action failed on attempt %d", attempt)
                break

        return None

    # -- Window state management (GW-055) ------------------------------------

    def _require_accessible(self, window: NativeHandle) -> Any:
        """Validate window handle and return the underlying accessible.

        Raises:
            WindowNotFoundError: If the handle is invalid or the accessible
                has been destroyed.
        """
        accessible = self._resolve_accessible(window)
        return accessible

    def minimize_window(self, window: NativeHandle) -> None:
        """Minimize a window using EWMH _NET_WM_STATE via python-xlib.

        Falls back to iconifying via AT-SPI if xlib is unavailable.

        Args:
            window: Opaque native window handle (pyatspi.Accessible).

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If minimization fails.
        """
        accessible = self._require_accessible(window)
        try:
            self._xlib_minimize(accessible)
            logger.debug("Minimized window via xlib: %s", window)
            return
        except ImportError:
            logger.debug("python-xlib not available for minimize; trying AT-SPI iconify")
        except Exception as exc:
            logger.debug("xlib minimize failed: %s", exc)

        raise ActionNotSupportedError(
            "minimize_window requires python-xlib (install via: pip install python-xlib)"
        )

    def maximize_window(self, window: NativeHandle) -> None:
        """Maximize a window using EWMH _NET_WM_STATE via python-xlib.

        Args:
            window: Opaque native window handle (pyatspi.Accessible).

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If maximization fails.
        """
        accessible = self._require_accessible(window)
        try:
            self._xlib_maximize(accessible)
            logger.debug("Maximized window via xlib: %s", window)
            return
        except ImportError:
            logger.debug("python-xlib not available for maximize")
        except Exception as exc:
            logger.debug("xlib maximize failed: %s", exc)

        raise ActionNotSupportedError(
            "maximize_window requires python-xlib (install via: pip install python-xlib)"
        )

    def restore_window(self, window: NativeHandle) -> None:
        """Restore a window from minimized/maximized state via EWMH.

        Args:
            window: Opaque native window handle (pyatspi.Accessible).

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If restoration fails.
        """
        accessible = self._require_accessible(window)
        try:
            self._xlib_restore(accessible)
            logger.debug("Restored window via xlib: %s", window)
            return
        except ImportError:
            logger.debug("python-xlib not available for restore")
        except Exception as exc:
            logger.debug("xlib restore failed: %s", exc)

        raise ActionNotSupportedError(
            "restore_window requires python-xlib (install via: pip install python-xlib)"
        )

    def move_window(self, window: NativeHandle, x: int, y: int) -> None:
        """Move a window to the given screen coordinates via EWMH.

        Args:
            window: Opaque native window handle (pyatspi.Accessible).
            x: Target left-edge X coordinate in screen pixels.
            y: Target top-edge Y coordinate in screen pixels.

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If moving fails.
        """
        accessible = self._require_accessible(window)
        try:
            self._xlib_move_resize(accessible, x, y, None, None)
            logger.debug("Moved window via xlib: %s to (%d, %d)", window, x, y)
            return
        except ImportError:
            logger.debug("python-xlib not available for move")
        except Exception as exc:
            logger.debug("xlib move failed: %s", exc)

        raise ActionNotSupportedError(
            "move_window requires python-xlib (install via: pip install python-xlib)"
        )

    def resize_window(self, window: NativeHandle, width: int, height: int) -> None:
        """Resize a window via EWMH.

        Args:
            window: Opaque native window handle (pyatspi.Accessible).
            width: Target width in pixels.
            height: Target height in pixels.

        Raises:
            WindowNotFoundError: If the handle is invalid.
            ActionNotSupportedError: If resizing fails.
        """
        accessible = self._require_accessible(window)
        try:
            self._xlib_move_resize(accessible, None, None, width, height)
            logger.debug("Resized window via xlib: %s to %dx%d", window, width, height)
            return
        except ImportError:
            logger.debug("python-xlib not available for resize")
        except Exception as exc:
            logger.debug("xlib resize failed: %s", exc)

        raise ActionNotSupportedError(
            "resize_window requires python-xlib (install via: pip install python-xlib)"
        )

    # -- xlib helpers for window state management (GW-055) -------------------

    @staticmethod
    def _get_xlib_display() -> Any:
        """Import and return an xlib Display connection."""
        from Xlib.display import Display  # type: ignore[import-untyped]

        return Display()

    @staticmethod
    def _accessible_to_xlib_window(accessible: Any, display: Any) -> Any:
        """Map an AT-SPI accessible to an xlib window via its application.

        Uses the AT-SPI application's D-Bus path to find the X window ID,
        then queries the X display for the corresponding window object.
        """

        # Try to get the X window ID from the accessible's attributes
        try:
            attrs = accessible.getAttributes()
            for attr in attrs:
                if attr.startswith("xwindow:"):
                    xid = int(attr.split(":")[1])

                    return display.create_resource_object("window", xid)
        except Exception:
            pass

        # Fallback: try to find window by PID using _NET_WM_PID
        try:
            app = accessible.getApplication()
            pid = app.get_attributes().get("pid")
            if pid is not None:
                root = display.screen().root
                pid_atom = display.get_atom("_NET_WM_PID", only_if_exists=True)
                if pid_atom:
                    for win_id in root.query_tree().children:
                        try:
                            win_pid = win_id.get_full_property(pid_atom, 0)
                            if win_pid and win_pid.value[0] == int(pid):
                                return win_id
                        except Exception:
                            continue
        except Exception:
            pass

        raise ActionNotSupportedError("Could not map AT-SPI accessible to an X11 window")

    @classmethod
    def _xlib_minimize(cls, accessible: Any) -> None:
        """Minimize (iconify) a window via xlib."""
        display = cls._get_xlib_display()
        try:
            window = cls._accessible_to_xlib_window(accessible, display)
            window.iconify()
            display.flush()
        finally:
            display.close()

    @classmethod
    def _xlib_maximize(cls, accessible: Any) -> None:
        """Maximize a window via EWMH _NET_WM_STATE."""
        from Xlib import X  # type: ignore[import-untyped]
        from Xlib.protocol import event  # type: ignore[import-untyped]

        display = cls._get_xlib_display()
        try:
            window = cls._accessible_to_xlib_window(accessible, display)
            root = display.screen().root

            state_atom = display.get_atom("_NET_WM_STATE")
            max_vert = display.get_atom("_NET_WM_STATE_MAXIMIZED_VERT")
            max_horz = display.get_atom("_NET_WM_STATE_MAXIMIZED_HORZ")

            # Send _NET_WM_STATE client message
            client_event = event.ClientMessage(
                window=window.id,
                client_type=state_atom,
                data=(32, [1, max_vert, max_horz, 0, 0]),  # _NET_WM_STATE_ADD=1
            )
            mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
            root.send_event(client_event, event_mask=mask)
            display.flush()
        finally:
            display.close()

    @classmethod
    def _xlib_restore(cls, accessible: Any) -> None:
        """Restore a window from minimized/maximized state via EWMH."""
        from Xlib import X  # type: ignore[import-untyped]
        from Xlib.protocol import event  # type: ignore[import-untyped]

        display = cls._get_xlib_display()
        try:
            window = cls._accessible_to_xlib_window(accessible, display)
            root = display.screen().root

            state_atom = display.get_atom("_NET_WM_STATE")
            max_vert = display.get_atom("_NET_WM_STATE_MAXIMIZED_VERT")
            max_horz = display.get_atom("_NET_WM_STATE_MAXIMIZED_HORZ")

            # Remove maximized state
            client_event = event.ClientMessage(
                window=window.id,
                client_type=state_atom,
                data=(32, [0, max_vert, max_horz, 0, 0]),  # _NET_WM_STATE_REMOVE=0
            )
            mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
            root.send_event(client_event, event_mask=mask)

            # De-iconify if minimized
            window.map()
            display.flush()
        finally:
            display.close()

    @classmethod
    def _xlib_move_resize(
        cls,
        accessible: Any,
        x: int | None,
        y: int | None,
        width: int | None,
        height: int | None,
    ) -> None:
        """Move and/or resize a window via EWMH _NET_MOVERESIZE_WINDOW."""

        display = cls._get_xlib_display()
        try:
            window = cls._accessible_to_xlib_window(accessible, display)

            # Get current geometry for unspecified values
            geometry = window.get_geometry()
            current_x = geometry.x
            current_y = geometry.y
            current_w = geometry.width
            current_h = geometry.height

            target_x = x if x is not None else current_x
            target_y = y if y is not None else current_y
            target_w = width if width is not None else current_w
            target_h = height if height is not None else current_h

            # Use configure to move/resize directly
            window.configure(
                x=target_x,
                y=target_y,
                width=target_w,
                height=target_h,
            )
            display.flush()
        finally:
            display.close()
