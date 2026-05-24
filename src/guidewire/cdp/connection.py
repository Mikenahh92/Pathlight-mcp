"""CDP WebSocket connection client.

Provides :class:`CDPConnection` — a synchronous WebSocket client that connects
to a Chromium-based browser's debug port, sends CDP commands, receives
responses, and dispatches events to an :class:`~guidewire.cdp.events.EventBuffer`.

Uses the ``websocket-client`` library for the underlying WebSocket transport.
The connection runs a background receiver thread that continuously reads
messages and dispatches them as responses (matched by command ID) or events
(buffered for later retrieval).
"""

import contextlib
import json
import logging
import threading
from typing import Any

from guidewire.cdp._protocol import CDPProtocol
from guidewire.cdp._types import ConnectionState
from guidewire.cdp.events import EventBuffer
from guidewire.errors import BackendUnavailableError, GuidewireError

__all__ = ["CDPConnection"]

logger = logging.getLogger(__name__)

_DEFAULT_WS_TIMEOUT = 30  # seconds


def _import_websocket() -> Any:
    """Lazy-import the ``websocket`` module.

    Defers the import so that ``websocket-client`` is only required when
    actually connecting to a CDP endpoint.  Test suites can patch this
    function to inject a fake.

    Raises:
        BackendUnavailableError: If ``websocket-client`` is not installed.
    """
    try:
        import websocket

        return websocket
    except ImportError as exc:
        raise BackendUnavailableError(
            "websocket-client is required for CDP connections. "
            "Install it with: pip install websocket-client>=1.6"
        ) from exc


class CDPConnection:
    """Synchronous CDP WebSocket client.

    Connects to a Chromium debug port via WebSocket and provides a simple
    request/response API for sending CDP commands and receiving results.
    CDP events emitted by the browser are buffered in an
    :class:`~guidewire.cdp.events.EventBuffer`.

    Usage::

        conn = CDPConnection(host="localhost", port=9222)
        conn.connect()
        result = conn.send_command("Page.navigate", {"url": "https://example.com"})
        events = conn.events.get_by_method("Page.loadEventFired")
        conn.close()

    Args:
        host: Hostname or IP of the Chromium debug target.
        port: Debug port number.
        url: Alternative: full WebSocket URL (overrides host/port).
        event_buffer: Optional pre-configured event buffer.  If ``None``,
            a default :class:`EventBuffer` with capacity 1000 is created.
        ws_timeout: Timeout in seconds for WebSocket operations (default 30).

    Attributes:
        events: The :class:`~guidewire.cdp.events.EventBuffer` for received events.
        state: Current :class:`~guidewire.cdp._types.ConnectionState`.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9222,
        *,
        url: str | None = None,
        event_buffer: EventBuffer | None = None,
        ws_timeout: float = _DEFAULT_WS_TIMEOUT,
    ) -> None:
        self._url = url or f"ws://{host}:{port}"
        self._host = host
        self._port = port
        self._ws_timeout = ws_timeout
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self.events = (
            event_buffer if event_buffer is not None
            else EventBuffer(maxsize_per_method=1024)
        )

        self._ws: Any = None  # websocket.WebSocket instance
        self._protocol: CDPProtocol | None = None
        self._receiver_thread: threading.Thread | None = None
        self._closed = False

    # -- Public API -----------------------------------------------------------

    @property
    def url(self) -> str:
        """The WebSocket URL this connection targets."""
        return self._url

    @property
    def host(self) -> str:
        """The hostname of the CDP target."""
        return self._host

    @property
    def port(self) -> int:
        """The port number of the CDP target."""
        return self._port

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Return ``True`` if the WebSocket is currently connected."""
        return self._state == ConnectionState.CONNECTED

    def connect(self) -> None:
        """Open the WebSocket connection and start the receiver thread.

        Raises:
            BackendUnavailableError: If the WebSocket connection cannot be
                established.
        """
        self._state = ConnectionState.CONNECTING
        ws_mod = _import_websocket()

        try:
            self._ws = ws_mod.create_connection(
                self._url,
                timeout=self._ws_timeout,
            )
        except Exception as exc:
            self._state = ConnectionState.DISCONNECTED
            raise BackendUnavailableError(
                f"Failed to connect to CDP endpoint at {self._url}: {exc}"
            ) from exc

        self._closed = False

        # Create the protocol handler with our send function
        self._protocol = CDPProtocol(
            send_fn=self._send_raw,
            event_buffer=self.events,
            default_timeout=self._ws_timeout,
        )

        self._state = ConnectionState.CONNECTED

        self._receiver_thread = threading.Thread(
            target=self._receiver_loop,
            name="cdp-receiver",
            daemon=True,
        )
        self._receiver_thread.start()
        logger.info("CDP connected to %s", self._url)

    def close(self) -> None:
        """Close the WebSocket connection and stop the receiver thread.

        Safe to call multiple times.  After closing, :meth:`connect` can be
        called again to re-establish the connection.
        """
        self._state = ConnectionState.CLOSING
        self._closed = True

        if self._ws is not None:
            with contextlib.suppress(Exception):
                self._ws.close()
            self._ws = None

        # Cancel any pending command futures
        if self._protocol is not None:
            self._protocol.cancel_pending()

        if (
            self._receiver_thread is not None
            and self._receiver_thread.is_alive()
        ):
            self._receiver_thread.join(timeout=5)
            self._receiver_thread = None

        self._state = ConnectionState.CLOSED
        logger.info("CDP connection closed")

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command and wait for the response.

        Args:
            method: CDP domain method (e.g. ``"Page.navigate"``).
            params: Method parameters (optional).
            timeout: Per-command timeout in seconds.  Defaults to the
                connection's ``ws_timeout``.

        Returns:
            The ``result`` dict from the CDP response.

        Raises:
            GuidewireError: If the browser returns an error response.
            TimeoutError: If the response is not received within *timeout*.
            BackendUnavailableError: If the connection is not open.
        """
        if self._state != ConnectionState.CONNECTED or self._protocol is None:
            raise BackendUnavailableError("CDP connection is not open")

        from guidewire.cdp.protocol import CDPError

        try:
            return self._protocol.send_command(method, params, timeout=timeout)
        except CDPError as exc:
            # Map CDP errors to Guidewire errors
            raise self._map_cdp_error(exc) from exc
        except ConnectionError as exc:
            raise BackendUnavailableError(str(exc)) from exc

    # -- Internal -------------------------------------------------------------

    def _send_raw(self, data: dict[str, Any]) -> None:
        """Send a raw JSON dict over the WebSocket.

        Args:
            data: JSON-serializable dict to send.

        Raises:
            BackendUnavailableError: If the send fails.
        """
        try:
            payload = json.dumps(data)
            self._ws.send(payload)
        except Exception as exc:
            raise BackendUnavailableError(f"Failed to send CDP message: {exc}") from exc

    @staticmethod
    def _map_cdp_error(exc: "Exception") -> GuidewireError:
        """Map a CDP protocol error to the appropriate GuidewireError.

        Args:
            exc: The raw CDP error.

        Returns:
            A :class:`~guidewire.errors.GuidewireError` subclass.
        """
        from guidewire.cdp.protocol import CDPError

        if isinstance(exc, CDPError):
            code = exc.code
            message = exc.message

            # CDP error code mappings
            # -32000: Generic error (often "Not found" type)
            # -32600: Invalid request
            # -32601: Method not found
            # -32602: Invalid params
            if code == -32000 and "not found" in message.lower():
                from guidewire.errors import ElementNotFoundError

                return ElementNotFoundError(message)

            return GuidewireError(f"CDP error {code}: {message}")

        return GuidewireError(str(exc))

    def _receiver_loop(self) -> None:
        """Background thread that reads WebSocket messages and dispatches them."""
        while not self._closed:
            try:
                raw = self._ws.recv()
                if not raw:
                    continue
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Received non-JSON CDP message: %s", raw[:200])
                continue
            except Exception:
                if not self._closed:
                    logger.debug("CDP receiver loop ending")
                break

            if self._protocol is not None:
                self._protocol.dispatch(data)

    def __enter__(self) -> "CDPConnection":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
