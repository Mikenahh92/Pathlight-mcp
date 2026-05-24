"""CDP message framing — command, reply, and event JSON protocol.

Defines the data structures and helpers for constructing CDP commands,
parsing CDP responses and events, and validating the message protocol.

CDP uses JSON-RPC-like messages:
  - Commands: ``{"id": <int>, "method": "<domain.method>", "params": {...}}``
  - Replies:  ``{"id": <int>, "result": {...}}`` or ``{"id": <int>, "error": {...}}``
  - Events:   ``{"method": "<domain.event>", "params": {...}}``
"""

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CDPError",
    "CDPEvent",
    "CDPMessage",
    "CDPResponse",
]


class CDPError(Exception):
    """Error returned by the CDP endpoint."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"CDP error {code}: {message}")


@dataclass(slots=True, frozen=True)
class CDPMessage:
    """A CDP command message to be sent to the browser.

    Attributes:
        id: Unique command identifier for correlating responses.
        method: CDP domain method (e.g. ``"Page.navigate"``).
        params: Method parameters (may be empty dict).
    """

    id: int
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary for sending over WebSocket."""
        msg: dict[str, Any] = {"id": self.id, "method": self.method}
        if self.params:
            msg["params"] = self.params
        return msg


@dataclass(slots=True, frozen=True)
class CDPResponse:
    """A CDP response (reply) from the browser.

    Attributes:
        id: Command identifier this response corresponds to.
        result: Result payload on success (``None`` if error).
        error: Error payload on failure (``None`` if success).
    """

    id: int
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def is_error(self) -> bool:
        """Return ``True`` if this response carries an error."""
        return self.error is not None

    def raise_for_error(self) -> None:
        """Raise :class:`CDPError` if this response is an error.

        Raises:
            CDPError: With the error code and message from the response.
        """
        if self.error is not None:
            raise CDPError(
                code=self.error.get("code", -1),
                message=self.error.get("message", "Unknown CDP error"),
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CDPResponse":
        """Parse a CDP response from a raw JSON dict.

        Args:
            data: Raw decoded JSON from the WebSocket.

        Returns:
            A :class:`CDPResponse` instance.

        Raises:
            ValueError: If the dict does not represent a valid CDP response.
        """
        msg_id = data.get("id")
        if msg_id is None:
            raise ValueError("CDP response missing 'id' field")
        return cls(
            id=int(msg_id),
            result=data.get("result"),
            error=data.get("error"),
        )


@dataclass(slots=True, frozen=True)
class CDPEvent:
    """A CDP event emitted by the browser.

    Attributes:
        method: Event name (e.g. ``"Page.loadEventFired"``).
        params: Event parameters (may be empty dict).
    """

    method: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CDPEvent":
        """Parse a CDP event from a raw JSON dict.

        Args:
            data: Raw decoded JSON from the WebSocket.

        Returns:
            A :class:`CDPEvent` instance.
        """
        return cls(
            method=data.get("method", ""),
            params=data.get("params", {}),
        )


def parse_cdp_message(data: dict[str, Any]) -> CDPResponse | CDPEvent:
    """Parse an incoming CDP JSON message as either a response or event.

    Args:
        data: Raw decoded JSON from the WebSocket.

    Returns:
        :class:`CDPResponse` if the message has an ``id`` field,
        :class:`CDPEvent` otherwise.
    """
    if "id" in data:
        return CDPResponse.from_dict(data)
    return CDPEvent.from_dict(data)
