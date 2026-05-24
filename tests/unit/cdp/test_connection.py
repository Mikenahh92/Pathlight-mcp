"""Tests for CDP WebSocket connection (connection.py).

Validates :class:`CDPConnection` with a mocked WebSocket transport.
The tests exercise command sending, response waiting, event buffering,
error handling, timeout behavior, connection lifecycle, state transitions,
and Guidewire error mapping.
"""

import json
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from guidewire.cdp._types import ConnectionState
from guidewire.cdp.connection import CDPConnection
from guidewire.cdp.events import EventBuffer
from guidewire.cdp.protocol import CDPError
from guidewire.errors import BackendUnavailableError, ElementNotFoundError, GuidewireError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """Minimal fake WebSocket for testing.

    Simulates ``websocket.create_connection`` behavior.  Supports queuing
    responses and events that ``recv()`` will return in order.  ``recv()``
    blocks until a message is available or the socket is closed.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._queue: list[str] = []
        self._sent: list[str] = []
        self._closed = False
        self._lock = threading.Lock()
        self._has_data = threading.Condition(self._lock)

    def enqueue(self, data: dict[str, Any]) -> None:
        """Queue a JSON message to be returned by ``recv()``."""
        with self._lock:
            self._queue.append(json.dumps(data))
            self._has_data.notify()

    def recv(self) -> str:
        """Return the next queued message (blocking if empty)."""
        with self._lock:
            while not self._queue and not self._closed:
                if not self._has_data.wait(timeout=0.5):
                    continue
            if self._closed and not self._queue:
                raise ConnectionError("WebSocket is closed")
            return self._queue.pop(0)

    def send(self, payload: str) -> None:
        """Record an outgoing message."""
        if self._closed:
            raise ConnectionError("WebSocket is closed")
        self._sent.append(payload)

    def close(self) -> None:
        """Mark as closed."""
        self._closed = True

    @property
    def sent_messages(self) -> list[dict[str, Any]]:
        """Return all sent messages as parsed dicts."""
        return [json.loads(s) for s in self._sent]


def _fake_ws_module(fake_ws: FakeWebSocket) -> MagicMock:
    """Create a fake ``websocket`` module that returns *fake_ws* from ``create_connection``."""
    mod = MagicMock()
    mod.create_connection.return_value = fake_ws
    return mod


def _create_connected_connection(
    url: str = "ws://localhost:9222/devtools/page/ABC",
) -> tuple[CDPConnection, FakeWebSocket]:
    """Create a CDPConnection with a FakeWebSocket, already connected."""
    fake_ws = FakeWebSocket()
    ws_mod = _fake_ws_module(fake_ws)

    with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
        conn = CDPConnection(url=url)
        conn.connect()

    return conn, fake_ws


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestCDPConnectionInit:
    """Tests for CDPConnection construction."""

    def test_default_attributes(self) -> None:
        conn = CDPConnection(host="localhost", port=9222)
        assert "localhost" in conn.url
        assert conn.host == "localhost"
        assert conn.port == 9222
        assert conn.state == ConnectionState.DISCONNECTED
        assert not conn.is_connected
        assert isinstance(conn.events, EventBuffer)

    def test_url_overrides_host_port(self) -> None:
        conn = CDPConnection(url="ws://custom:1234/test")
        assert conn.url == "ws://custom:1234/test"

    def test_custom_event_buffer(self) -> None:
        buf = EventBuffer(maxsize_per_method=50)
        conn = CDPConnection(host="localhost", port=9222, event_buffer=buf)
        assert conn.events.maxsize_per_method == 50

    def test_custom_timeout(self) -> None:
        conn = CDPConnection(host="localhost", port=9222, ws_timeout=10)
        assert conn._ws_timeout == 10


# ---------------------------------------------------------------------------
# Connection lifecycle and state
# ---------------------------------------------------------------------------


class TestCDPConnectionLifecycle:
    """Tests for connect/close lifecycle and state transitions."""

    def test_connect_sets_connected_state(self) -> None:
        conn, _ = _create_connected_connection()
        assert conn.state == ConnectionState.CONNECTED
        assert conn.is_connected

    def test_close_sets_closed_state(self) -> None:
        conn, _ = _create_connected_connection()
        conn.close()
        assert conn.state == ConnectionState.CLOSED
        assert not conn.is_connected

    def test_close_is_idempotent(self) -> None:
        conn, _ = _create_connected_connection()
        conn.close()
        conn.close()  # should not raise
        assert conn.state == ConnectionState.CLOSED

    def test_connect_failure_raises_backend_unavailable(self) -> None:
        ws_mod = MagicMock()
        ws_mod.create_connection.side_effect = OSError("refused")
        with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
            conn = CDPConnection(url="ws://localhost:9222/test")
            with pytest.raises(BackendUnavailableError, match="Failed to connect"):
                conn.connect()
        assert conn.state == ConnectionState.DISCONNECTED

    def test_context_manager(self) -> None:
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)
        with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
            with CDPConnection(url="ws://localhost:9222/test") as conn:
                assert conn.state == ConnectionState.CONNECTED
            assert conn.state == ConnectionState.CLOSED

    def test_initial_state_is_disconnected(self) -> None:
        conn = CDPConnection(host="localhost", port=9222)
        assert conn.state == ConnectionState.DISCONNECTED


# ---------------------------------------------------------------------------
# Send command
# ---------------------------------------------------------------------------


class TestCDPConnectionSendCommand:
    """Tests for send_command."""

    def test_send_command_success(self) -> None:
        conn, fake_ws = _create_connected_connection()

        # Queue the response
        fake_ws.enqueue({"id": 1, "result": {"frameId": "main"}})

        result = conn.send_command("Page.navigate", {"url": "https://example.com"})
        assert result == {"frameId": "main"}

        # Verify the sent message
        sent = fake_ws.sent_messages
        assert len(sent) == 1
        assert sent[0]["method"] == "Page.navigate"
        assert sent[0]["params"] == {"url": "https://example.com"}

    def test_send_command_error_maps_to_guidewire_error(self) -> None:
        conn, fake_ws = _create_connected_connection()

        fake_ws.enqueue({"id": 1, "error": {"code": -32000, "message": "Not found"}})

        with pytest.raises(ElementNotFoundError, match="Not found"):
            conn.send_command("Page.navigate", {"url": "bad"})

    def test_send_command_generic_error_maps_to_guidewire_error(self) -> None:
        conn, fake_ws = _create_connected_connection()

        fake_ws.enqueue({"id": 1, "error": {"code": -32600, "message": "Invalid request"}})

        with pytest.raises(GuidewireError, match="CDP error"):
            conn.send_command("Page.navigate", {"url": "bad"})

    def test_send_command_no_params(self) -> None:
        conn, fake_ws = _create_connected_connection()

        fake_ws.enqueue({"id": 1, "result": {}})

        result = conn.send_command("Page.enable")
        assert result == {}

        sent = fake_ws.sent_messages
        assert sent[0]["method"] == "Page.enable"

    def test_send_command_increments_ids(self) -> None:
        conn, fake_ws = _create_connected_connection()

        # Use timers to enqueue responses after the commands are sent
        def enqueue_after(delay: float, data: dict[str, Any]) -> None:
            threading.Timer(delay, lambda: fake_ws.enqueue(data)).start()

        enqueue_after(0.05, {"id": 1, "result": {}})
        enqueue_after(0.15, {"id": 2, "result": {}})

        conn.send_command("Page.enable", timeout=2.0)
        conn.send_command("Runtime.enable", timeout=2.0)

        sent = fake_ws.sent_messages
        assert sent[0]["id"] == 1
        assert sent[1]["id"] == 2

    def test_send_command_not_connected_raises_backend_unavailable(self) -> None:
        conn = CDPConnection(host="localhost", port=9222)
        with pytest.raises(BackendUnavailableError, match="not open"):
            conn.send_command("Page.enable")

    def test_send_command_timeout(self) -> None:
        conn, _fake_ws = _create_connected_connection()
        # Don't enqueue a response — should timeout

        with pytest.raises(TimeoutError, match="timed out"):
            conn.send_command("Page.enable", timeout=0.1)

    def test_send_command_custom_timeout(self) -> None:
        conn, fake_ws = _create_connected_connection()

        # Enqueue response with a slight delay
        timer = threading.Timer(0.05, lambda: fake_ws.enqueue({"id": 1, "result": {}}))
        timer.start()

        result = conn.send_command("Page.enable", timeout=1.0)
        assert result == {}
        timer.join()


# ---------------------------------------------------------------------------
# Event buffering
# ---------------------------------------------------------------------------


class TestCDPConnectionEventBuffering:
    """Tests for event buffering via receiver thread."""

    def test_events_buffered(self) -> None:
        conn, fake_ws = _create_connected_connection()

        # Queue an event and a response (so receiver thread has work)
        fake_ws.enqueue({"method": "Page.loadEventFired", "params": {"timestamp": 1.0}})
        fake_ws.enqueue({"id": 1, "result": {}})

        # Send a command to trigger receiver activity
        conn.send_command("Page.enable", timeout=2.0)

        # Give the receiver thread time to process the event
        time.sleep(0.1)

        events = conn.events.get_by_method("Page.loadEventFired")
        assert len(events) == 1
        assert events[0].params == {"timestamp": 1.0}

    def test_multiple_events_buffered(self) -> None:
        conn, fake_ws = _create_connected_connection()

        fake_ws.enqueue({"method": "Runtime.consoleAPICalled", "params": {"n": 1}})
        fake_ws.enqueue({"method": "Runtime.consoleAPICalled", "params": {"n": 2}})
        fake_ws.enqueue({"id": 1, "result": {}})

        conn.send_command("Runtime.enable", timeout=2.0)

        time.sleep(0.1)

        events = conn.events.get_by_method("Runtime.consoleAPICalled")
        assert len(events) == 2


# ---------------------------------------------------------------------------
# Close wakes pending
# ---------------------------------------------------------------------------


class TestCDPConnectionClosePending:
    """Tests that close() wakes pending command futures."""

    def test_close_wakes_pending_command(self) -> None:
        conn, _fake_ws = _create_connected_connection()

        # Start a command in a thread (will block waiting for response)
        result_holder: dict[str, Any] = {"error": None}

        def send_cmd() -> None:
            try:
                conn.send_command("Page.enable", timeout=10)
            except Exception as exc:
                result_holder["error"] = exc

        t = threading.Thread(target=send_cmd)
        t.start()

        # Give it time to block
        time.sleep(0.1)

        # Close the connection — should wake the pending command
        conn.close()
        t.join(timeout=5)

        # The pending command should have received an error
        assert result_holder["error"] is not None


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestCDPErrorMapping:
    """Tests for CDP error to Guidewire error mapping."""

    def test_not_found_maps_to_element_not_found(self) -> None:
        error = CDPError(code=-32000, message="Node not found")
        mapped = CDPConnection._map_cdp_error(error)
        assert isinstance(mapped, ElementNotFoundError)

    def test_generic_cdp_error_maps_to_guidewire_error(self) -> None:
        error = CDPError(code=-32600, message="Invalid request")
        mapped = CDPConnection._map_cdp_error(error)
        assert isinstance(mapped, GuidewireError)
        assert not isinstance(mapped, ElementNotFoundError)

    def test_non_cdp_error_maps_to_guidewire_error(self) -> None:
        error = RuntimeError("something broke")
        mapped = CDPConnection._map_cdp_error(error)
        assert isinstance(mapped, GuidewireError)


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


class TestImportGuard:
    """Tests for websocket-client import guard."""

    def test_missing_websocket_raises_backend_unavailable(self) -> None:
        with patch("guidewire.cdp.connection._import_websocket") as mock_import:
            mock_import.side_effect = BackendUnavailableError(
                "websocket-client is required"
            )
            conn = CDPConnection(host="localhost", port=9222)
            with pytest.raises(BackendUnavailableError):
                conn.connect()
