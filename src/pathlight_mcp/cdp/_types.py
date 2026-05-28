"""Internal type definitions for the CDP transport layer.

Defines shared enumerations, data classes, type aliases, and typed CDP
domain dataclasses used across the CDP package.

Typed domain dataclasses (AC-7):

- :class:`AXNode` — CDP Accessibility node
- :class:`DOMNode` — CDP DOM node
- :class:`BoxModel` — CDP BoxModel result
- :class:`RemoteObject` — CDP Runtime.RemoteObject
- :class:`FrameTree` — CDP Page frame tree
- :class:`FrameNavigationReply` — CDP Page.navigate reply
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "CDP_PROTOCOL_VERSION",
    "AXNode",
    "BoxModel",
    "CDPTarget",
    "CommandSender",
    "ConnectionState",
    "DOMNode",
    "FrameNavigationReply",
    "FrameTree",
    "RemoteObject",
    "SessionState",
]

# ---------------------------------------------------------------------------
# CDP protocol version constant (AC-8)
# ---------------------------------------------------------------------------

CDP_PROTOCOL_VERSION: str = "1.3"
"""The Chrome DevTools Protocol version implemented by this package."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ConnectionState(StrEnum):
    """Lifecycle states of a :class:`~pathlight_mcp.cdp.connection.CDPConnection`.

    Attributes:
        DISCONNECTED: Initial state — no WebSocket has been opened.
        CONNECTING: A WebSocket handshake is in progress.
        CONNECTED: The WebSocket is open and the receiver thread is running.
        RECONNECTING: The connection is being re-established after a dead-peer
            detection or transport failure.
        CLOSING: The connection is being shut down.
        CLOSED: The connection has been fully closed.
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSING = "closing"
    CLOSED = "closed"


class SessionState(StrEnum):
    """Lifecycle states of a :class:`~pathlight_mcp.cdp.session.CDPSession`.

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


# ---------------------------------------------------------------------------
# CommandSender protocol (AC-7)
# ---------------------------------------------------------------------------


@runtime_checkable
class CommandSender(Protocol):
    """Protocol for objects that can send CDP commands.

    Any object that implements ``send_command`` satisfies this protocol,
    including :class:`~pathlight_mcp.cdp.session.CDPSession`.
    """

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Transport data classes
# ---------------------------------------------------------------------------


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
    def from_dict(cls, data: dict[str, Any]) -> CDPTarget:
        """Parse a target from the ``/json/list`` JSON response.

        Args:
            data: A single target dict from the ``/json/list`` response.

        Returns:
            A :class:`CDPTarget` instance.
        """
        return cls(
            id=data.get("id", "") or data.get("targetId", ""),
            type=data.get("type", ""),
            title=data.get("title", ""),
            url=data.get("url", ""),
            web_socket_debugger_url=data.get("webSocketDebuggerUrl", ""),
        )


# ---------------------------------------------------------------------------
# Typed CDP domain dataclasses (AC-7)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class AXNode:
    """A node in the CDP Accessibility tree.

    Represents a single accessible node returned by CDP
    ``Accessibility.getFullAXTree`` / ``getPartialAXTree`` / ``queryAXTree``.

    Attributes:
        node_id: CDP node identifier.
        role: AX role value (e.g. ``"button"``, ``"textField"``).
        name: Accessible name.
        description: Accessible description.
        value: Current value.
        backend_dom_node_id: Backend DOM node identifier (for cross-referencing).
        child_ids: CDP child node identifiers (for tree linking).
        bounds: Bounding rectangle ``{x, y, width, height}`` if present.
        properties: Raw CDP properties dict (states, etc.).
        raw: The original CDP node dict for debugging / forward-compat.
    """

    node_id: str
    role: str | None = None
    name: str | None = None
    description: str | None = None
    value: str | None = None
    backend_dom_node_id: int | None = None
    child_ids: tuple[str, ...] = ()
    bounds: dict[str, float] | None = None
    properties: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_cdp(cls, data: dict[str, Any]) -> AXNode:
        """Construct an :class:`AXNode` from a raw CDP AX node dict.

        Args:
            data: A single node dict from a CDP Accessibility response.

        Returns:
            A :class:`AXNode` instance.
        """
        role_val = data.get("role", {}).get("value")
        name_val = data.get("name", {}).get("value")
        desc_val = data.get("description", {}).get("value")
        value_val = data.get("value", {}).get("value")
        backend_id = data.get("backendDOMNodeId")
        bounds_data = data.get("bounds")

        # Properties: merge dedicated states and properties lists
        props: dict[str, Any] = {}
        states_data = data.get("states")
        if isinstance(states_data, list):
            for s in states_data:
                if isinstance(s, dict):
                    key = s.get("name", "")
                    val = s.get("value")
                    if key:
                        props[key] = val
        elif isinstance(states_data, dict):
            props.update(states_data)

        properties_data = data.get("properties")
        if isinstance(properties_data, list):
            for p in properties_data:
                if isinstance(p, dict):
                    key = p.get("name", "")
                    val = p.get("value")
                    if key:
                        props[key] = val
        elif isinstance(properties_data, dict):
            props.update(properties_data)

        bounds_out: dict[str, float] | None = None
        if bounds_data and isinstance(bounds_data, dict):
            try:
                bounds_out = {
                    "x": float(bounds_data.get("x", 0)),
                    "y": float(bounds_data.get("y", 0)),
                    "width": float(bounds_data.get("width", 0)),
                    "height": float(bounds_data.get("height", 0)),
                }
            except (TypeError, ValueError):
                bounds_out = None

        return cls(
            node_id=data.get("nodeId", ""),
            role=role_val,
            name=name_val,
            description=desc_val,
            value=str(value_val) if value_val is not None else None,
            backend_dom_node_id=int(backend_id) if backend_id is not None else None,
            child_ids=tuple(data.get("childIds", [])),
            bounds=bounds_out,
            properties=props or None,
            raw=data,
        )


@dataclass(slots=True, frozen=True)
class DOMNode:
    """A node in the CDP DOM tree.

    Represents a single DOM node returned by CDP ``DOM.getDocument``,
    ``DOM.describeNode``, etc.

    Attributes:
        node_id: CDP node identifier.
        backend_node_id: Backend DOM node identifier.
        node_name: Tag name (e.g. ``"DIV"``, ``"BUTTON"``).
        node_value: Text content (for text nodes).
        attributes: Flat list of ``[name, value, name, value, ...]``.
        child_node_count: Number of children.
        children: Child :class:`DOMNode` instances (only when depth allows).
        document_url: Document URL (for document nodes).
        base_url: Base URL (for document nodes).
        public_id: Doctype public ID.
        system_id: Doctype system ID.
        internal_subset: Doctype internal subset.
        xml_version: XML version.
        name: Element name (for doctype / processing instruction nodes).
        local_name: Local name (for elements in namespaces).
    """

    node_id: int
    backend_node_id: int = 0
    node_name: str = ""
    node_value: str = ""
    attributes: tuple[str, ...] = ()
    child_node_count: int = 0
    children: tuple[DOMNode, ...] = ()
    document_url: str | None = None
    base_url: str | None = None
    public_id: str | None = None
    system_id: str | None = None
    internal_subset: str | None = None
    xml_version: str | None = None
    name: str | None = None
    local_name: str = ""

    @classmethod
    def from_cdp(cls, data: dict[str, Any]) -> DOMNode:
        """Construct a :class:`DOMNode` from a raw CDP DOM node dict.

        Args:
            data: A single node dict from a CDP DOM response.

        Returns:
            A :class:`DOMNode` instance.
        """
        children_data = data.get("children", [])
        children = tuple(cls.from_cdp(c) for c in children_data) if children_data else ()

        return cls(
            node_id=data.get("nodeId", 0),
            backend_node_id=data.get("backendNodeId", 0),
            node_name=data.get("nodeName", ""),
            node_value=data.get("nodeValue", ""),
            attributes=tuple(data.get("attributes", [])),
            child_node_count=data.get("childNodeCount", len(children)),
            children=children,
            document_url=data.get("documentURL"),
            base_url=data.get("baseURL"),
            public_id=data.get("publicId"),
            system_id=data.get("systemId"),
            internal_subset=data.get("internalSubset"),
            xml_version=data.get("xmlVersion"),
            name=data.get("name"),
            local_name=data.get("localName", ""),
        )


@dataclass(slots=True, frozen=True)
class BoxModel:
    """CDP ``DOM.getBoxModel`` result.

    Represents the box model of a DOM element with border, content, margin,
    padding, width, and height values.

    Attributes:
        border: Border quad as ``[x1, y1, x2, y2, x3, y3, x4, y4]``.
        content: Content quad.
        margin: Margin quad.
        padding: Padding quad.
        width: Element width.
        height: Element height.
    """

    border: tuple[float, ...] = ()
    content: tuple[float, ...] = ()
    margin: tuple[float, ...] = ()
    padding: tuple[float, ...] = ()
    width: int = 0
    height: int = 0

    @classmethod
    def from_cdp(cls, data: dict[str, Any]) -> BoxModel:
        """Construct a :class:`BoxModel` from a raw CDP ``getBoxModel`` result.

        Args:
            data: The ``model`` dict from a CDP ``DOM.getBoxModel`` response.

        Returns:
            A :class:`BoxModel` instance.
        """
        return cls(
            border=tuple(data.get("border", [])),
            content=tuple(data.get("content", [])),
            margin=tuple(data.get("margin", [])),
            padding=tuple(data.get("padding", [])),
            width=data.get("width", 0),
            height=data.get("height", 0),
        )

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        """Compute a bounding ``(x, y, width, height)`` from the border quad.

        Returns:
            Bounding tuple or ``None`` if border data is incomplete.
        """
        if len(self.border) < 8:
            return None
        xs = [self.border[i] for i in range(0, 8, 2)]
        ys = [self.border[i] for i in range(1, 8, 2)]
        x = min(xs)
        y = min(ys)
        w = max(xs) - x
        h = max(ys) - y
        if w <= 0 or h <= 0:
            return None
        return (x, y, w, h)


@dataclass(slots=True, frozen=True)
class RemoteObject:
    """CDP ``Runtime.RemoteObject`` descriptor.

    Represents a JavaScript value or object returned by CDP
    ``Runtime.evaluate``, ``Runtime.callFunctionOn``, etc.

    Attributes:
        type: Object type (e.g. ``"object"``, ``"string"``, ``"number"``).
        subtype: Subtype hint (e.g. ``"null"``, ``"array"``, ``"node"``).
        class_name: Constructor class name (for objects).
        value: JSON-serializable value (when ``returnByValue`` is ``True``).
        unserializable_value: Unserializable value string (e.g. ``"NaN"``).
        description: Object description string.
        object_id: Unique object identifier for further CDP calls.
        preview: Object preview (if available).
    """

    type: str
    subtype: str | None = None
    class_name: str | None = None
    value: Any = None
    unserializable_value: str | None = None
    description: str | None = None
    object_id: str | None = None
    preview: dict[str, Any] | None = None

    @classmethod
    def from_cdp(cls, data: dict[str, Any]) -> RemoteObject:
        """Construct a :class:`RemoteObject` from a raw CDP remote object dict.

        Args:
            data: A ``Runtime.RemoteObject`` dict from a CDP response.

        Returns:
            A :class:`RemoteObject` instance.
        """
        return cls(
            type=data.get("type", ""),
            subtype=data.get("subtype"),
            class_name=data.get("className"),
            value=data.get("value"),
            unserializable_value=data.get("unserializableValue"),
            description=data.get("description"),
            object_id=data.get("objectId"),
            preview=data.get("preview"),
        )


@dataclass(slots=True, frozen=True)
class FrameTree:
    """CDP ``Page.getFrameTree`` result.

    Represents a frame and its child frames from the CDP Page domain.

    Attributes:
        frame: The frame descriptor dict with keys like ``id``, ``url``,
            ``loaderId``, ``securityOrigin``, ``mimeType``.
        child_frames: Child :class:`FrameTree` instances.
    """

    frame: dict[str, Any]
    child_frames: tuple[FrameTree, ...] = ()

    @classmethod
    def from_cdp(cls, data: dict[str, Any]) -> FrameTree:
        """Construct a :class:`FrameTree` from a raw CDP ``frameTree`` dict.

        Args:
            data: A ``frameTree`` dict from a CDP ``Page.getFrameTree`` response.

        Returns:
            A :class:`FrameTree` instance.
        """
        children_data = data.get("childFrames", [])
        child_frames = tuple(cls.from_cdp(c) for c in children_data) if children_data else ()
        return cls(
            frame=data.get("frame", {}),
            child_frames=child_frames,
        )


@dataclass(slots=True, frozen=True)
class FrameNavigationReply:
    """Reply from CDP ``Page.navigate``.

    Attributes:
        frame_id: The frame that navigated.
        loader_id: The new loader identifier.
    """

    frame_id: str
    loader_id: str = ""

    @classmethod
    def from_cdp(cls, data: dict[str, Any]) -> FrameNavigationReply:
        """Construct from a raw CDP ``Page.navigate`` response dict.

        Args:
            data: The ``result`` dict from a CDP ``Page.navigate`` response.

        Returns:
            A :class:`FrameNavigationReply` instance.
        """
        return cls(
            frame_id=data.get("frameId", ""),
            loader_id=data.get("loaderId", ""),
        )
