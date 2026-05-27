"""CDP browser connection manager — target discovery, session management, reconnection.

Provides :class:`CDPBrowser` — the high-level entry point for connecting to a
Chromium-based browser's debug port.  It discovers browser targets via the
HTTP ``/json/list`` endpoint, creates :class:`~guidewire.cdp.session.CDPSession`
instances for targets, and manages the connection lifecycle including
reconnection.

Usage::

    browser = CDPBrowser(host="localhost", port=9222)
    browser.connect()

    targets = browser.list_targets()
    page = next(t for t in targets if t.type == "page")

    with browser.attach(page) as session:
        result = session.send_command("Page.navigate", {"url": "https://example.com"})

    browser.close()
"""

import json
import logging
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from guidewire.cdp._types import CDPTarget, ConnectionState
from guidewire.cdp.connection import CDPConnection
from guidewire.cdp.session import CDPSession
from guidewire.errors import BackendUnavailableError, GuidewireError

__all__ = ["CDPBrowser"]

logger = logging.getLogger(__name__)

_DEFAULT_HTTP_TIMEOUT = 10  # seconds
_DEFAULT_WS_TIMEOUT = 30  # seconds


def _import_urllib_json(data: bytes) -> list[dict[str, Any]]:
    """Parse JSON bytes from an HTTP response."""
    return json.loads(data)


class CDPBrowser:
    """High-level CDP browser connection manager.

    Connects to a Chromium debug port, discovers targets, and manages
    sessions.  Wraps a root :class:`~guidewire.cdp.connection.CDPConnection`
    and provides target discovery via the HTTP ``/json/list`` endpoint.

    Args:
        host: Hostname or IP of the Chromium debug target.
        port: Debug port number.
        ws_timeout: Timeout in seconds for WebSocket operations.
        http_timeout: Timeout in seconds for HTTP target discovery.

    Attributes:
        host: The hostname of the browser.
        port: The debug port number.
        state: Current :class:`~guidewire.cdp._types.ConnectionState`.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9222,
        *,
        ws_timeout: float = _DEFAULT_WS_TIMEOUT,
        http_timeout: float = _DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._ws_timeout = ws_timeout
        self._http_timeout = http_timeout
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._connection: CDPConnection | None = None
        self._sessions: dict[str, CDPSession] = {}  # keyed by target.id
        self._session_by_id: dict[str, CDPSession] = {}  # keyed by session_id
        self._lock = threading.Lock()
        self._event_thread: threading.Thread | None = None
        self._closed = False

    # -- Public API -----------------------------------------------------------

    @property
    def host(self) -> str:
        """The hostname of the browser."""
        return self._host

    @property
    def port(self) -> int:
        """The debug port number."""
        return self._port

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Return ``True`` if the browser connection is active."""
        return self._state == ConnectionState.CONNECTED

    @property
    def connection(self) -> CDPConnection | None:
        """The underlying root :class:`~guidewire.cdp.connection.CDPConnection`."""
        return self._connection

    def connect(self) -> None:
        """Open the browser connection.

        Creates a root :class:`~guidewire.cdp.connection.CDPConnection` to
        the browser's debug port.  This connection is used for session
        management commands (``Target.attachToTarget``, etc.).

        The WebSocket URL is resolved dynamically by querying the browser's
        ``/json/version`` endpoint.  This avoids hardcoding the WS URL path
        which changed in newer Chromium versions (the browser target ID is
        now required, e.g. ``/devtools/browser/{id}``).

        Falls back to the legacy ``/devtools/browser`` path if ``/json/version``
        is unavailable.

        Raises:
            BackendUnavailableError: If the connection cannot be established.
        """
        if self._state == ConnectionState.CONNECTED:
            return

        self._state = ConnectionState.CONNECTING

        try:
            ws_url = self._resolve_browser_ws_url()
            self._connection = CDPConnection(
                url=ws_url,
                ws_timeout=self._ws_timeout,
            )
            self._connection.connect()
            self._closed = False
            self._start_event_listener()
            self._state = ConnectionState.CONNECTED
            logger.info("CDP browser connected to %s:%s", self._host, self._port)
        except Exception:
            self._state = ConnectionState.DISCONNECTED
            raise

    def _resolve_browser_ws_url(self) -> str:
        """Resolve the browser-level WebSocket URL from ``/json/version``.

        Queries the browser's HTTP ``/json/version`` endpoint which returns
        a JSON dict containing ``webSocketDebuggerUrl`` — the canonical WS
        URL for the browser-level CDP connection.

        Falls back to the legacy path ``ws://{host}:{port}/devtools/browser``
        when the endpoint is unavailable (e.g. older Chromium or restricted
        environments).

        Returns:
            A WebSocket URL string.
        """
        url = f"http://{self._host}:{self._port}/json/version"
        try:
            response = urlopen(url, timeout=self._http_timeout)
            data = json.loads(response.read())
            ws_url = data.get("webSocketDebuggerUrl", "")
            if ws_url:
                return ws_url
        except Exception:
            logger.debug(
                "Failed to query /json/version, falling back to legacy WS URL",
                exc_info=True,
            )
        return f"ws://{self._host}:{self._port}/devtools/browser"

    def close(self) -> None:
        """Close the browser connection and detach all sessions.

        Safe to call multiple times.
        """
        self._state = ConnectionState.CLOSING
        self._closed = True

        # Stop the event listener thread
        if (
            self._event_thread is not None
            and self._event_thread.is_alive()
        ):
            self._event_thread.join(timeout=5)
            self._event_thread = None

        # Detach all active sessions
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._session_by_id.clear()

        for session in sessions:
            try:
                session.close()
            except Exception:
                logger.debug("Error closing session during browser shutdown")

        if self._connection is not None:
            self._connection.close()
            self._connection = None

        self._state = ConnectionState.CLOSED
        logger.info("CDP browser connection closed")

    def list_targets(self, *, target_type: str | None = None) -> list[CDPTarget]:
        """Discover browser targets via the HTTP ``/json/list`` endpoint.

        Args:
            target_type: Optional filter to return only targets of a given
                type (e.g. ``"page"``, ``"iframe"``, ``"worker"``,
                ``"service_worker"``).  When ``None`` (default), all targets
                are returned.

        Returns:
            List of discovered :class:`~guidewire.cdp._types.CDPTarget`
            instances, optionally filtered by *target_type*.

        Raises:
            BackendUnavailableError: If the HTTP request fails.
        """
        url = f"http://{self._host}:{self._port}/json/list"
        try:
            response = urlopen(url, timeout=self._http_timeout)
            data = _import_urllib_json(response.read())
            targets = [CDPTarget.from_dict(item) for item in data]
            if target_type is not None:
                targets = [t for t in targets if t.type == target_type]
            return targets
        except (URLError, OSError) as exc:
            raise BackendUnavailableError(f"Failed to discover targets at {url}: {exc}") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise BackendUnavailableError(f"Invalid response from {url}: {exc}") from exc

    def get_target(self, target_id: str) -> CDPTarget | None:
        """Find a specific target by ID.

        Args:
            target_id: The target identifier to look for.

        Returns:
            The matching :class:`~guidewire.cdp._types.CDPTarget`, or ``None``.
        """
        targets = self.list_targets()
        for target in targets:
            if target.id == target_id:
                return target
        return None

    def attach(self, target: CDPTarget) -> CDPSession:
        """Create and attach a session to the given target.

        Args:
            target: The :class:`~guidewire.cdp._types.CDPTarget` to attach to.

        Returns:
            An attached :class:`~guidewire.cdp.session.CDPSession`.

        Raises:
            GuidewireError: If the browser connection is not open.
        """
        if self._state != ConnectionState.CONNECTED or self._connection is None:
            raise GuidewireError("Browser connection is not open")

        session = CDPSession(connection=self._connection, target=target)
        session.attach()

        with self._lock:
            self._sessions[target.id] = session
            if session.session_id is not None:
                self._session_by_id[session.session_id] = session

        return session

    def detach(self, target_id: str) -> None:
        """Detach and remove the session for the given target.

        Args:
            target_id: The target identifier whose session to detach.

        Raises:
            GuidewireError: If no session exists for the target.
        """
        with self._lock:
            session = self._sessions.pop(target_id, None)
            if session is not None and session.session_id is not None:
                self._session_by_id.pop(session.session_id, None)

        if session is None:
            raise GuidewireError(f"No active session for target {target_id}")

        session.detach()

    def get_session(self, target_id: str) -> CDPSession | None:
        """Return the active session for a target, if any.

        Args:
            target_id: The target identifier.

        Returns:
            The :class:`~guidewire.cdp.session.CDPSession`, or ``None``.
        """
        with self._lock:
            return self._sessions.get(target_id)

    @property
    def active_sessions(self) -> list[CDPSession]:
        """Return all active sessions."""
        with self._lock:
            return list(self._sessions.values())

    def reconnect(
        self,
        *,
        max_retries: int = 0,
        backoff_delay: float = 1.0,
    ) -> None:
        """Close and re-establish the browser connection.

        Closes the existing connection, reconnects, and re-attaches all
        sessions that were active before the reconnection.

        Args:
            max_retries: Maximum number of retry attempts when reconnection
                fails.  ``0`` (default) means a single attempt with no
                retries.
            backoff_delay: Delay in seconds between retry attempts.
                Applied as a fixed delay between each attempt.

        Raises:
            BackendUnavailableError: If reconnection fails after all retries.
        """
        # Save session targets for re-attach
        with self._lock:
            targets = [s.target for s in self._sessions.values() if s.is_attached]

        # Close existing connection
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                logger.debug("Error closing connection during reconnect")

        self._state = ConnectionState.DISCONNECTED
        self._sessions.clear()
        self._session_by_id.clear()

        # Reconnect with optional retry / backoff
        last_exc: Exception | None = None
        for attempt in range(1 + max_retries):
            try:
                self.connect()
                break
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    logger.info(
                        "Reconnect attempt %d/%d failed, retrying in %.1fs",
                        attempt + 1,
                        1 + max_retries,
                        backoff_delay,
                    )
                    time.sleep(backoff_delay)
        else:
            raise last_exc  # type: ignore[misc]

        # Re-attach sessions
        for target in targets:
            try:
                self.attach(target)
                logger.info("Re-attached session for target %s", target.id)
            except Exception as exc:
                logger.warning(
                    "Failed to re-attach session for target %s: %s",
                    target.id,
                    exc,
                )

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command on the root browser connection.

        Use this for browser-level commands (e.g. ``Target.setDiscoverTargets``).
        For target-scoped commands, use a :class:`~guidewire.cdp.session.CDPSession`.

        Args:
            method: CDP domain method.
            params: Method parameters (optional).
            timeout: Per-command timeout in seconds.

        Returns:
            The ``result`` dict from the CDP response.

        Raises:
            GuidewireError: If the browser connection is not open.
        """
        if self._connection is None or not self._connection.is_connected:
            raise GuidewireError("Browser connection is not open")
        return self._connection.send_command(method, params, timeout=timeout)

    def __enter__(self) -> "CDPBrowser":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- Internal -------------------------------------------------------------

    def _start_event_listener(self) -> None:
        """Start a background thread that monitors for session-detach events.

        Watches the connection's :class:`~guidewire.cdp.events.EventBuffer`
        for ``Target.detachedFromTarget`` events and calls
        :meth:`CDPSession.mark_detached` on the affected session so that
        subsequent commands trigger a transparent re-attach (GW-117).
        """
        if self._connection is None:
            return

        # Clear any stale events from a previous connection
        self._connection.events.clear()

        self._event_thread = threading.Thread(
            target=self._event_loop,
            name="cdp-session-events",
            daemon=True,
        )
        self._event_thread.start()

    def _event_loop(self) -> None:
        """Background loop that drains events from the buffer."""
        import time as _time

        while not self._closed:
            try:
                self._drain_detach_events()
                self._drain_popup_events()
            except Exception:
                logger.debug(
                    "Error in session event listener",
                    exc_info=True,
                )
            _time.sleep(0.1)

    def _drain_detach_events(self) -> None:
        """Process all pending ``Target.detachedFromTarget`` events."""
        if self._connection is None:
            return

        events = self._connection.events.get_by_method("Target.detachedFromTarget")
        for event in events:
            session_id = event.params.get("sessionId", "")
            if not session_id:
                continue

            with self._lock:
                session = self._session_by_id.pop(session_id, None)

            if session is not None:
                session.mark_detached()
                logger.info(
                    "Target.detachedFromTarget received: session_id=%s, "
                    "session marked detached for target_id=%s",
                    session_id,
                    session.target.id,
                )
            else:
                logger.debug(
                    "Target.detachedFromTarget for unknown session_id=%s",
                    session_id,
                )

    def _drain_popup_events(self) -> None:
        """Process ``Target.targetCreated`` events and log popup detection."""
        if self._connection is None:
            return

        events = self._connection.events.get_by_method("Target.targetCreated")
        for event in events:
            target_info = event.params.get("targetInfo", {})
            if not target_info:
                continue

            target_type = target_info.get("type", "")
            target_id = target_info.get("targetId", "")
            url = target_info.get("url", "")
            opener_id = event.params.get("openerId", "") or target_info.get("openerId", "")

            if target_type == "page":
                if opener_id:
                    logger.info(
                        "Popup detected: target_id=%s url=%s opener_id=%s",
                        target_id,
                        url,
                        opener_id,
                    )
                else:
                    logger.info(
                        "New tab detected: target_id=%s url=%s",
                        target_id,
                        url,
                    )
