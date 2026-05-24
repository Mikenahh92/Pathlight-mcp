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
        self._lock = threading.Lock()

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

        Raises:
            BackendUnavailableError: If the connection cannot be established.
        """
        if self._state == ConnectionState.CONNECTED:
            return

        self._state = ConnectionState.CONNECTING

        try:
            ws_url = f"ws://{self._host}:{self._port}/devtools/browser"
            self._connection = CDPConnection(
                url=ws_url,
                ws_timeout=self._ws_timeout,
            )
            self._connection.connect()
            self._state = ConnectionState.CONNECTED
            logger.info("CDP browser connected to %s:%s", self._host, self._port)
        except Exception:
            self._state = ConnectionState.DISCONNECTED
            raise

    def close(self) -> None:
        """Close the browser connection and detach all sessions.

        Safe to call multiple times.
        """
        self._state = ConnectionState.CLOSING

        # Detach all active sessions
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

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
