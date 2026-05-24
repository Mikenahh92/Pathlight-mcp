"""Internal type definitions for the CDP transport layer.

Defines shared enumerations, data classes, and type aliases used across the
CDP package.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "CDPTarget",
    "ConnectionState",
    "SessionState",
]


class ConnectionState(StrEnum):
    """Lifecycle states of a :class:`~guidewire.cdp.connection.CDPConnection`.

    Attributes:
        DISCONNECTED: Initial state — no WebSocket has been opened.
        CONNECTING: A WebSocket handshake is in progress.
        CONNECTED: The WebSocket is open and the receiver thread is running.
        CLOSING: The connection is being shut down.
        CLOSED: The connection has been fully closed.
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    CLOSED = "closed"


class SessionState(StrEnum):
    """Lifecycle states of a :class:`~guidewire.cdp.session.CDPSession`.

    Attributes:
        DETACHED: No session is active — not attached to any target.
        ATTACHING: An attach request is in progress.
        ATTACHED: Actively attached to a browser target.
        DETACHING: A detach request is in progress.
    """

    DETACHED = "detached"
    ATTACHING = "attaching"
    ATTACHED = "attached"
    DETACHING = "detaching"


@dataclass(slots=True, frozen=True)
class CDPTarget:
    """Represents a browser target discovered via the CDP HTTP endpoint.

    Chromium exposes discoverable targets through the ``/json/list`` HTTP
    endpoint on the debug port.  Each target describes a page, service worker,
    or other debuggable context.

    Attributes:
        id: Unique target identifier (e.g. ``"ABCDEF"``).
        type: Target type (e.g. ``"page"``, ``"service_worker"``).
        title: Human-readable title of the target.
        url: URL currently loaded in the target.
        web_socket_debugger_url: WebSocket URL for attaching to this target.
    """

    id: str
    type: str
    title: str = ""
    url: str = ""
    web_socket_debugger_url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CDPTarget":
        """Parse a target from the ``/json/list`` JSON response.

        Args:
            data: A single target dict from the ``/json/list`` response.

        Returns:
            A :class:`CDPTarget` instance.
        """
        return cls(
            id=data.get("id", ""),
            type=data.get("type", ""),
            title=data.get("title", ""),
            url=data.get("url", ""),
            web_socket_debugger_url=data.get("webSocketDebuggerUrl", ""),
        )
