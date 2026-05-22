"""X11 EWMH _NET_ACTIVE_WINDOW helper for LinuxBackend (architecture v2 §3.2).

This module is a lazy import target for the xlib fallback path in
:meth:`~guidewire.backends.linux.LinuxBackend.focus_window`.  Keeping the
``python-xlib`` dependency isolated here follows architecture §10 tradeoffs:
the import only fires when AT-SPI activation fails and xlib is actually needed,
so users without ``python-xlib`` are unaffected.

Window discovery uses the proven xwindow-attribute / PID pattern from
:meth:`LinuxBackend._accessible_to_xlib_window`: first try the AT-SPI
``xwindow:`` attribute, then fall back to matching the application PID against
``_NET_WM_PID`` on top-level X windows (GW-075).
"""

from typing import Any


def _resolve_xlib_window(accessible: Any, display: Any) -> Any:
    """Map an AT-SPI accessible to an X11 window object.

    Strategy (mirrors ``LinuxBackend._accessible_to_xlib_window``):
    1. Read AT-SPI attributes looking for ``xwindow:<id>``.
    2. Fall back to PID-based discovery via ``_NET_WM_PID``.

    Raises:
        RuntimeError: If the accessible cannot be mapped to an X window.
    """
    from Xlib import X  # type: ignore[import-untyped]

    # --- Strategy 1: xwindow attribute ------------------------------------
    try:
        attrs = accessible.getAttributes()
        for attr in attrs:
            if attr.startswith("xwindow:"):
                xid = int(attr.split(":")[1])
                return display.create_resource_object("window", xid)
    except Exception:
        pass

    # --- Strategy 2: PID via _NET_WM_PID ----------------------------------
    try:
        app = accessible.getApplication()
        pid = app.get_attributes().get("pid")
        if pid is not None:
            root = display.screen().root
            pid_atom = display.get_atom("_NET_WM_PID", only_if_exists=True)
            if pid_atom:
                for win in root.query_tree().children:
                    try:
                        win_pid = win.get_full_property(pid_atom, 0)
                        if win_pid and win_pid.value[0] == int(pid):
                            return win
                    except Exception:
                        continue
    except Exception:
        pass

    raise RuntimeError(
        "Could not map AT-SPI accessible to an X11 window "
        "(no xwindow attribute and PID discovery failed)"
    )


def xlib_activate(accessible: Any) -> None:
    """Send ``_NET_ACTIVE_WINDOW`` via python-xlib EWMH helper.

    Uses the xwindow-attribute / PID discovery pattern to locate the
    X11 window corresponding to *accessible*, then sends an EWMH
    ``_NET_ACTIVE_WINDOW`` client message to the root window.

    Args:
        accessible: A live ``pyatspi.Accessible`` representing the window.

    Raises:
        ImportError: If ``python-xlib`` is not installed.
        RuntimeError: If the accessible cannot be mapped to an X window.
        Exception: If the xlib activation fails (display, D-Bus, etc.).
    """
    from Xlib import X  # type: ignore[import-untyped]
    from Xlib.display import Display  # type: ignore[import-untyped]
    from Xlib.protocol import event  # type: ignore[import-untyped]

    display = Display()
    try:
        window = _resolve_xlib_window(accessible, display)
        root = display.screen().root

        active_atom = display.get_atom("_NET_ACTIVE_WINDOW", only_if_exists=True)
        if active_atom == 0:
            raise RuntimeError("Window manager does not support _NET_ACTIVE_WINDOW")

        client_message = event.ClientMessage(
            window=window.id,
            client_type=active_atom,
            data=(32, [1, 0, 0, 0, 0]),  # source=1 (application), timestamp=0
        )
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        root.send_event(client_message, event_mask=mask)
        display.flush()
    finally:
        display.close()


__all__ = ["xlib_activate"]
