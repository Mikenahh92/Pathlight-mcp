"""CDP protocol handler — command sending, Future-based correlation, and dispatch.

Provides :class:`CDPProtocol` — the protocol layer responsible for:

- Generating unique command IDs.
- Serialising CDP commands to JSON and sending them via a pluggable transport.
- Correlating responses back to their callers using :class:`~concurrent.futures.Future`.
- Dispatching incoming CDP events to an
  :class:`~guidewire.cdp.events.EventBuffer`.

This class is separated from :class:`~guidewire.cdp.connection.CDPConnection`
so that the wire-level transport (WebSocket) can be replaced or tested
independently of the command/response protocol.
"""

import logging
import threading
from concurrent.futures import Future
from typing import Any, Protocol

from guidewire.cdp.events import EventBuffer
from guidewire.cdp.protocol import (
    CDPEvent,
    CDPMessage,
    CDPResponse,
    parse_cdp_message,
)

__all__ = ["CDPProtocol"]

logger = logging.getLogger(__name__)

_DEFAULT_CMD_TIMEOUT = 30  # seconds


class _SendFn(Protocol):
    """Type protocol for the raw-send callable."""

    def __call__(self, data: dict[str, Any]) -> None: ...


class CDPProtocol:
    """CDP command/response protocol handler.

    Manages command ID generation, serialisation, Future-based response
    correlation, and event dispatch.  The actual WebSocket I/O is delegated
    to a ``send_fn`` callback injected at construction time.

    Args:
        send_fn: Callable that accepts a JSON dict and sends it over the
            transport.  Must raise on failure.
        event_buffer: The :class:`~guidewire.cdp.events.EventBuffer` to
            dispatch events to.
        default_timeout: Default timeout in seconds for command responses.

    Attributes:
        events: The :class:`~guidewire.cdp.events.EventBuffer` for received events.
    """

    def __init__(
        self,
        send_fn: _SendFn,
        event_buffer: EventBuffer,
        *,
        default_timeout: float = _DEFAULT_CMD_TIMEOUT,
    ) -> None:
        self._send_fn = send_fn
        self.events = event_buffer
        self._default_timeout = default_timeout

        self._next_id = 1
        self._id_lock = threading.Lock()

        # Pending command futures keyed by command ID
        self._pending: dict[int, Future[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()

    # -- Public API -----------------------------------------------------------

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command and wait for the response.

        Args:
            method: CDP domain method (e.g. ``"Page.navigate"``).
            params: Method parameters (optional).
            session_id: Optional CDP session identifier.  When set,
                included as a top-level ``sessionId`` field in the
                JSON-RPC message for session-scoped routing.
            timeout: Per-command timeout in seconds.  Defaults to
                ``default_timeout``.

        Returns:
            The ``result`` dict from the CDP response.

        Raises:
            TimeoutError: If the response is not received within *timeout*.
        """
        cmd_id = self._next_command_id()
        message = CDPMessage(
            id=cmd_id, method=method, params=params or {},
            session_id=session_id,
        )
        future: Future[dict[str, Any]] = Future()

        with self._pending_lock:
            self._pending[cmd_id] = future

        self._send_fn(message.to_dict())

        effective_timeout = timeout or self._default_timeout
        try:
            return future.result(timeout=effective_timeout)
        except TimeoutError:
            with self._pending_lock:
                self._pending.pop(cmd_id, None)
            raise TimeoutError(
                f"CDP command {method} (id={cmd_id}) timed out "
                f"after {effective_timeout}s"
            ) from None

    def dispatch(self, data: dict[str, Any]) -> None:
        """Route a parsed CDP message to the appropriate handler.

        Called by the receiver loop for each incoming message.

        Args:
            data: Parsed JSON dict from the WebSocket.
        """
        msg = parse_cdp_message(data)

        if isinstance(msg, CDPResponse):
            self._handle_response(msg)
        else:
            self._handle_event(msg)

    def cancel_pending(self) -> None:
        """Cancel all pending command futures (e.g. on disconnect)."""
        with self._pending_lock:
            for _cmd_id, future in self._pending.items():
                if not future.done():
                    future.set_exception(
                        ConnectionError("CDP connection closed while waiting for response")
                    )
            self._pending.clear()

    # -- Internal -------------------------------------------------------------

    def _next_command_id(self) -> int:
        """Generate the next unique command ID."""
        with self._id_lock:
            cmd_id = self._next_id
            self._next_id += 1
            return cmd_id

    def _handle_response(self, response: CDPResponse) -> None:
        """Match a response to its pending Future and resolve it."""
        with self._pending_lock:
            future = self._pending.pop(response.id, None)

        if future is not None and not future.done():
            if response.is_error:
                from guidewire.cdp.protocol import CDPError

                future.set_exception(
                    CDPError(
                        code=response.error.get("code", -1) if response.error else -1,
                        message=response.error.get("message", "Unknown CDP error")
                        if response.error
                        else "Unknown CDP error",
                    )
                )
            else:
                future.set_result(response.result or {})
        else:
            logger.debug(
                "Received response for unknown command id=%d", response.id
            )

    def _handle_event(self, event: CDPEvent) -> None:
        """Buffer a CDP event."""
        self.events.put(event)
