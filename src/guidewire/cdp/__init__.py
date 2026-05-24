"""Chrome DevTools Protocol (CDP) transport and domain wrappers.

Provides the foundational transport and typed domain wrappers for
communicating with Chromium-based browsers via the Chrome DevTools Protocol.

Transport layer:

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
- :class:`AXNode` — CDP Accessibility node dataclass
- :class:`DOMNode` — CDP DOM node dataclass
- :class:`BoxModel` — CDP BoxModel dataclass
- :class:`RemoteObject` — CDP Runtime.RemoteObject dataclass
- :class:`FrameTree` — CDP Page frame tree dataclass
- :class:`FrameNavigationReply` — CDP Page.navigate reply dataclass
- :data:`CDP_PROTOCOL_VERSION` — CDP protocol version constant
- Message framing helpers for CDP command/reply/event JSON protocol.

Domain wrappers (see :mod:`guidewire.cdp.domains`):

- :class:`AccessibilityDomain` — query the browser accessibility tree
- :class:`DOMDomain` — query and manipulate the DOM tree
- :class:`RuntimeDomain` — evaluate JavaScript and inspect objects
- :class:`InputDomain` — dispatch mouse and keyboard events
- :class:`PageDomain` — control page navigation and lifecycle
- :class:`TargetDomain` — discover and manage browser targets
"""

from guidewire.cdp._types import (
    CDP_PROTOCOL_VERSION,
    AXNode,
    BoxModel,
    CDPTarget,
    CommandSender,
    ConnectionState,
    DOMNode,
    FrameNavigationReply,
    FrameTree,
    RemoteObject,
    SessionState,
)
from guidewire.cdp.browser import CDPBrowser
from guidewire.cdp.connection import CDPConnection
from guidewire.cdp.domains import (
    AccessibilityDomain,
    CDPDomain,
    DOMDomain,
    InputDomain,
    PageDomain,
    RuntimeDomain,
    TargetDomain,
)
from guidewire.cdp.events import EventBuffer
from guidewire.cdp.protocol import CDPEvent, CDPMessage, CDPResponse
from guidewire.cdp.session import CDPSession

__all__ = [
    "CDP_PROTOCOL_VERSION",
    "AXNode",
    "AccessibilityDomain",
    "BoxModel",
    "CDPBrowser",
    "CDPConnection",
    "CDPDomain",
    "CDPEvent",
    "CDPMessage",
    "CDPProtocol",
    "CDPResponse",
    "CDPSession",
    "CDPTarget",
    "CommandSender",
    "ConnectionState",
    "DOMDomain",
    "DOMNode",
    "EventBuffer",
    "FrameNavigationReply",
    "FrameTree",
    "InputDomain",
    "PageDomain",
    "RemoteObject",
    "RuntimeDomain",
    "SessionState",
    "TargetDomain",
]
