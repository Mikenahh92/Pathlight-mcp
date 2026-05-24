"""Chrome DevTools Protocol (CDP) transport layer.

Provides the foundational transport for communicating with Chromium-based
browsers via the Chrome DevTools Protocol.  This package implements:

- :class:`CDPConnection` — WebSocket client that connects to Chromium's
  debug port and sends/receives CDP messages.
- :class:`CDPProtocol` — Protocol handler for command sending, Future-based
  correlation, and event dispatch.
- :class:`EventBuffer` — Thread-safe per-method circular buffer for CDP events.
- :class:`ConnectionState` — Lifecycle state enum for connections.
- Message framing helpers for CDP command/reply/event JSON protocol.

This is the lowest-level component that all web backend stories build upon.
"""

from guidewire.cdp._types import ConnectionState
from guidewire.cdp.connection import CDPConnection
from guidewire.cdp.events import EventBuffer
from guidewire.cdp.protocol import CDPEvent, CDPMessage, CDPResponse

__all__ = [
    "CDPConnection",
    "CDPEvent",
    "CDPMessage",
    "CDPProtocol",
    "CDPResponse",
    "ConnectionState",
    "EventBuffer",
]
