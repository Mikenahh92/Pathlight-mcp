"""CDP WebSocket connection client.

Provides :class:`CDPConnection` — a synchronous WebSocket client that connects
to a Chromium-based browser's debug port, sends CDP commands, receives
responses, and dispatches events to an :class:`~guidewire.cdp.events.EventBuffer`.

Uses the ``websocket-client`` library for the underlying WebSocket transport.
The connection runs a background receiver thread that continuously reads
messages and dispatches them as responses (matched by command ID) or events
(buffered for later retrieval).

Keepalive and dead-peer detection (GW-127):
    A background pinger thread sends WebSocket ping frames at a configurable
    interval.  If the corresponding pong is not received within a pong timeout,
    the connection is considered dead.  The pinger then triggers an automatic
    reconnect that re-establishes the WebSocket and replays any command that was
    in-flight when the dead peer was detected.
"""

import contextlib
import json
import logging
import threading
import time
from typing import Any

from guidewire.cdp._protocol import CDPProtocol
from guidewire.cdp._types import ConnectionState
from guidewire.cdp.events import EventBuffer
from guidewire.errors import BackendUnavailableError, GuidewireError

__all__ = ["CDPConnection"]

logger = logging.getLogger(__name__)

_DEFAULT_WS_TIMEOUT = 30  # seconds
_DEFAULT_PING_INTERVAL = 30.0  # seconds between keepalive pings
_DEFAULT_PONG_TIMEOUT = 10.0  # seconds to wait for a pong response
_DEFAULT_MAX_RECONNECT_ATTEMPTS = 3  # max reconnect attempts on dead peer
_DEFAULT_RECONNECT_BACKOFF = 1.0  # seconds base backoff between reconnect attempts


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

    Keepalive pinger (GW-127):
        A background thread sends WebSocket ping frames at *ping_interval*
        seconds (default 30).  If the corresponding pong is not received
        within *pong_timeout* seconds (default 10), the peer is considered
        dead and an automatic reconnect is triggered.

    Automatic reconnect (GW-127):
        When a dead peer is detected or the receiver loop exits unexpectedly,
        the connection attempts to reconnect up to *max_reconnect_attempts*
        times with exponential backoff.  In-flight commands are retried after
        a successful reconnect.

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
        ping_interval: Seconds between keepalive ping frames (default 30).
            Set to ``0`` to disable keepalive pings.
        pong_timeout: Seconds to wait for a pong response before declaring
            the peer dead (default 10).
        max_reconnect_attempts: Maximum number of automatic reconnect attempts
            on dead-peer detection (default 3).  Set to ``0`` to disable
            auto-reconnect.
        reconnect_backoff: Base delay in seconds between reconnect attempts
            (default 1.0).  Actual delay is ``backoff * 2^attempt``.

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
        ping_interval: float = _DEFAULT_PING_INTERVAL,
        pong_timeout: float = _DEFAULT_PONG_TIMEOUT,
        max_reconnect_attempts: int = _DEFAULT_MAX_RECONNECT_ATTEMPTS,
        reconnect_backoff: float = _DEFAULT_RECONNECT_BACKOFF,
    ) -> None:
        self._url = url or f"ws://{host}:{port}"
        self._host = host
        self._port = port
        self._ws_timeout = ws_timeout
        self._ping_interval = ping_interval
        self._pong_timeout = pong_timeout
        self._max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_backoff = reconnect_backoff
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self.events = (
            event_buffer if event_buffer is not None
            else EventBuffer(maxsize_per_method=1024)
        )

        self._ws: Any = None  # websocket.WebSocket instance
        self._protocol: CDPProtocol | None = None
        self._receiver_thread: threading.Thread | None = None
        self._pinger_thread: threading.Thread | None = None
        self._closed = False

        # Pinger state: tracks last pong time for dead-peer detection
        self._last_pong_time: float = 0.0
        self._pinger_lock = threading.Lock()
        # Event signaling the pinger that a pong was received
        self._pong_event = threading.Event()

        # Reconnect lock: serializes reconnect attempts
        self._reconnect_lock = threading.Lock()

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

        Also starts the keepalive pinger thread if ``ping_interval > 0``.

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

        # Start keepalive pinger if configured
        if self._ping_interval > 0:
            self._start_pinger()

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
            self._pinger_thread is not None
            and self._pinger_thread.is_alive()
        ):
            self._pinger_thread.join(timeout=5)
            self._pinger_thread = None

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
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command and wait for the response.

        If the connection drops during the command and auto-reconnect is
        enabled, the connection is re-established and the command is retried
        once after a successful reconnect (GW-127).

        Args:
            method: CDP domain method (e.g. ``"Page.navigate"``).
            params: Method parameters (optional).
            session_id: Optional CDP session identifier.  When set,
                included as a top-level ``sessionId`` field in the
                JSON-RPC message for session-scoped routing.
            timeout: Per-command timeout in seconds.  Defaults to the
                connection's ``ws_timeout``.

        Returns:
            The ``result`` dict from the CDP response.

        Raises:
            GuidewireError: If the browser returns an error response.
            TimeoutError: If the response is not received within *timeout*.
            BackendUnavailableError: If the connection is not open.
        """
        return self._send_command_with_reconnect(
            method, params, session_id=session_id, timeout=timeout,
        )

    # -- Reconnect support (GW-127) -------------------------------------------

    def _send_command_with_reconnect(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        session_id: str | None = None,
        timeout: float | None = None,
        _attempt: int = 0,
    ) -> dict[str, Any]:
        """Send a command with automatic reconnect-and-retry on transport failure."""
        if self._state != ConnectionState.CONNECTED or self._protocol is None:
            raise BackendUnavailableError("CDP connection is not open")

        from guidewire.cdp.protocol import CDPError

        try:
            return self._protocol.send_command(
                method, params, session_id=session_id, timeout=timeout,
            )
        except CDPError as exc:
            # Map CDP errors to Guidewire errors
            raise self._map_cdp_error(exc) from exc
        except (ConnectionError, BackendUnavailableError) as exc:
            # Transport-level error — attempt reconnect if enabled
            if self._should_retry_reconnect(_attempt, exc):
                logger.info(
                    "Transport error on %s (attempt %d), triggering reconnect: %s",
                    method, _attempt + 1, exc,
                )
                self._auto_reconnect()
                return self._send_command_with_reconnect(
                    method, params, session_id=session_id, timeout=timeout,
                    _attempt=_attempt + 1,
                )
            raise BackendUnavailableError(str(exc)) from exc

    def _should_retry_reconnect(self, attempt: int, exc: Exception) -> bool:
        """Check whether a failed command should trigger a reconnect retry."""
        if self._closed:
            return False
        if self._max_reconnect_attempts <= 0:
            return False
        if attempt >= self._max_reconnect_attempts:
            return False
        return True

    def _auto_reconnect(self) -> None:
        """Attempt to reconnect the WebSocket after a transport failure.

        Uses exponential backoff between attempts.  Raises if all attempts fail.

        Safe to call from any thread (including the receiver or pinger threads).
        When called from the receiver or pinger thread, it does not attempt to
        join itself.
        """
        with self._reconnect_lock:
            current_thread = threading.current_thread()
            is_receiver = self._receiver_thread is current_thread
            is_pinger = self._pinger_thread is current_thread

            self._state = ConnectionState.RECONNECTING

            # Stop the pinger if running (unless we ARE the pinger — it
            # will exit naturally after this method returns)
            if (
                not is_pinger
                and self._pinger_thread is not None
                and self._pinger_thread.is_alive()
            ):
                self._pinger_thread.join(timeout=3)
            self._pinger_thread = None

            # Close the old socket — this should unblock the receiver's recv()
            if self._ws is not None:
                with contextlib.suppress(Exception):
                    self._ws.close()
                self._ws = None

            # Wait for the receiver thread to finish (unless we ARE the
            # receiver thread — it will exit naturally after this method
            # returns)
            if (
                not is_receiver
                and self._receiver_thread is not None
                and self._receiver_thread.is_alive()
            ):
                self._receiver_thread.join(timeout=5)
            self._receiver_thread = None

            # Retry with backoff
            last_exc: Exception | None = None
            for attempt in range(self._max_reconnect_attempts):
                delay = self._reconnect_backoff * (2 ** attempt)
                if attempt > 0:
                    logger.info(
                        "Reconnect attempt %d/%d, waiting %.1fs",
                        attempt + 1, self._max_reconnect_attempts, delay,
                    )
                    time.sleep(delay)

                try:
                    self._state = ConnectionState.CONNECTING
                    ws_mod = _import_websocket()
                    self._ws = ws_mod.create_connection(
                        self._url,
                        timeout=self._ws_timeout,
                    )
                    self._closed = False

                    # Recreate protocol with new send function
                    self._protocol = CDPProtocol(
                        send_fn=self._send_raw,
                        event_buffer=self.events,
                        default_timeout=self._ws_timeout,
                    )

                    self._state = ConnectionState.CONNECTED

                    # Restart receiver thread
                    self._receiver_thread = threading.Thread(
                        target=self._receiver_loop,
                        name="cdp-receiver",
                        daemon=True,
                    )
                    self._receiver_thread.start()

                    # Restart pinger if configured
                    if self._ping_interval > 0:
                        self._start_pinger()

                    logger.info(
                        "CDP reconnected to %s after %d attempt(s)",
                        self._url, attempt + 1,
                    )
                    return
                except Exception as exc:
                    last_exc = exc
                    logger.debug(
                        "Reconnect attempt %d failed: %s", attempt + 1, exc,
                    )

            # All attempts exhausted
            self._state = ConnectionState.DISCONNECTED
            raise BackendUnavailableError(
                f"Failed to reconnect to CDP endpoint at {self._url} "
                f"after {self._max_reconnect_attempts} attempts: {last_exc}"
            ) from last_exc

    # -- Keepalive pinger (GW-127) --------------------------------------------

    def _start_pinger(self) -> None:
        """Start the keepalive pinger thread."""
        with self._pinger_lock:
            self._last_pong_time = time.monotonic()
            self._pong_event.set()  # Start with pong "received"

        self._pinger_thread = threading.Thread(
            target=self._pinger_loop,
            name="cdp-pinger",
            daemon=True,
        )
        self._pinger_thread.start()

    def _pinger_loop(self) -> None:
        """Background thread that sends WebSocket pings for keepalive.

        Sends a ping frame every ``ping_interval`` seconds.  If a pong is
        not received within ``pong_timeout`` seconds after the ping, the
        peer is considered dead and an automatic reconnect is triggered.
        """
        while not self._closed and self._state == ConnectionState.CONNECTED:
            # Wait for the ping interval
            if self._wait_for_interval(self._ping_interval):
                break  # _closed was set

            if self._closed or self._state != ConnectionState.CONNECTED:
                break

            # Send ping
            self._pong_event.clear()
            try:
                self._send_ping()
            except Exception:
                logger.debug("Ping send failed, peer may be dead")
                if not self._closed and self._max_reconnect_attempts > 0:
                    self._trigger_dead_peer_reconnect("ping send failed")
                break

            # Wait for pong with timeout
            pong_received = self._pong_event.wait(timeout=self._pong_timeout)
            if self._closed:
                break

            if not pong_received:
                logger.warning(
                    "Dead peer detected: no pong received within %.1fs",
                    self._pong_timeout,
                )
                if self._max_reconnect_attempts > 0:
                    self._trigger_dead_peer_reconnect("pong timeout")
                break

            with self._pinger_lock:
                self._last_pong_time = time.monotonic()

    def _wait_for_interval(self, interval: float) -> bool:
        """Sleep for *interval* seconds, returning early if ``_closed`` is set.

        Returns:
            ``True`` if ``_closed`` was set during the wait.
        """
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            if self._closed:
                return True
            time.sleep(min(0.5, deadline - time.monotonic()))
        return False

    def _send_ping(self) -> None:
        """Send a WebSocket ping frame.

        Uses the ``ping()`` method on the underlying ``websocket.WebSocket``
        if available, otherwise sends a no-op CDP command as a liveness check.
        """
        ws = self._ws
        if ws is None:
            raise BackendUnavailableError("No WebSocket connection")

        # Try native WebSocket ping first (websocket-client supports it)
        if hasattr(ws, "ping"):
            ws.ping()
        else:
            # Fallback: send a lightweight CDP command as liveness probe
            payload = json.dumps({"id": 0, "method": "Target.getTargetProperties"})
            ws.send(payload)

    def _on_pong_received(self) -> None:
        """Called when a WebSocket pong frame is received.

        Signals the pinger thread that the peer is still alive.
        """
        with self._pinger_lock:
            self._last_pong_time = time.monotonic()
        self._pong_event.set()

    def _trigger_dead_peer_reconnect(self, reason: str) -> None:
        """Trigger an automatic reconnect due to a dead peer.

        Args:
            reason: Short description of why the peer was declared dead.
        """
        logger.warning(
            "Triggering auto-reconnect (dead peer): %s — url=%s",
            reason, self._url,
        )
        try:
            self._auto_reconnect()
        except Exception as exc:
            logger.error(
                "Auto-reconnect failed after dead peer detection: %s", exc,
            )
            # Cancel pending commands — they will get a BackendUnavailableError
            if self._protocol is not None:
                self._protocol.cancel_pending()

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
        """Background thread that reads WebSocket messages and dispatches them.

        Handles both regular CDP messages and WebSocket control frames (pong).
        On unexpected exit (transport error), triggers auto-reconnect if enabled.
        """
        while not self._closed:
            try:
                raw = self._ws.recv()
                if not raw:
                    continue
                # Check for pong control frame (some websocket-client versions
                # return non-string types for control frames)
                if not isinstance(raw, str):
                    self._on_pong_received()
                    continue
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Received non-JSON CDP message: %s", raw[:200])
                continue
            except Exception:
                if not self._closed:
                    logger.debug("CDP receiver loop ending unexpectedly")
                    # Trigger auto-reconnect for unexpected receiver exit
                    if (
                        self._state == ConnectionState.CONNECTED
                        and self._max_reconnect_attempts > 0
                    ):
                        self._trigger_dead_peer_reconnect("receiver loop exited")
                break

            if self._protocol is not None:
                self._protocol.dispatch(data)

    def __enter__(self) -> "CDPConnection":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
