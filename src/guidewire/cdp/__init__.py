"""Chrome DevTools Protocol (CDP) transport layer.

Provides the foundational transport for communicating with Chromium-based
browsers via the Chrome DevTools Protocol.  This package implements:

- :class:`CDPBrowser` — High-level browser connection manager with target
  discovery, session management, and reconnection.
- :class:`CDPConnection` — WebSocket client that connects to Chromium's
  debug port and sends/receives CDP messages.
- :class:`CDPProtocol` — Protocol handler for command sending, Future-based
  correlation, and event dispatch.
- :class:`CDPSession` — Session manager that scopes CDP commands to a
  specific browser target via ``Target.attachToTarget``.
- :class:`CDPTarget` — Data class representing a browser target.
- :class:`EventBuffer` — Thread-safe per-method circular buffer for CDP events.
- :class:`ConnectionState` — Lifecycle state enum for connections.
- :class:`SessionState` — Lifecycle state enum for sessions.
- Message framing helpers for CDP command/reply/event JSON protocol.
"""

from guidewire.cdp._types import CDPTarget, ConnectionState, SessionState
from guidewire.cdp.browser import CDPBrowser
from guidewire.cdp.connection import CDPConnection
from guidewire.cdp.events import EventBuffer
from guidewire.cdp.protocol import CDPEvent, CDPMessage, CDPResponse
from guidewire.cdp.session import CDPSession

__all__ = [
    "CDPBrowser",
    "CDPConnection",
    "CDPEvent",
    "CDPMessage",
    "CDPProtocol",
    "CDPResponse",
    "CDPSession",
    "CDPTarget",
    "ConnectionState",
    "EventBuffer",
    "SessionState",
]
