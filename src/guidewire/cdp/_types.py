"""Internal type definitions for the CDP transport layer.

Defines shared enumerations and type aliases used across the CDP package.
"""

from enum import StrEnum

__all__ = ["ConnectionState"]


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
