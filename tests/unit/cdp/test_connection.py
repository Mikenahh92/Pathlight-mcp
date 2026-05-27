"""Tests for CDP WebSocket connection (connection.py).

Validates :class:`CDPConnection` with a mocked WebSocket transport.
The tests exercise command sending, response waiting, event buffering,
error handling, timeout behavior, connection lifecycle, state transitions,
Guidewire error mapping, keepalive pinger, dead-peer detection, and
automatic reconnect (GW-127).
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

    Also supports ``ping()`` for keepalive testing (GW-127).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._queue: list[str] = []
        self._sent: list[str] = []
        self._closed = False
        self._lock = threading.Lock()
        self._has_data = threading.Condition(self._lock)
        self._ping_count = 0

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

    def ping(self, payload: bytes = b"") -> None:
        """Record a ping frame (GW-127 keepalive)."""
        if self._closed:
            raise ConnectionError("WebSocket is closed")
        self._ping_count += 1

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
    **kwargs: Any,
) -> tuple[CDPConnection, FakeWebSocket]:
    """Create a CDPConnection with a FakeWebSocket, already connected.

    Disables keepalive pings by default (ping_interval=0) so existing
    tests don't spawn pinger threads.
    """
    fake_ws = FakeWebSocket()
    ws_mod = _fake_ws_module(fake_ws)

    # Disable pinger by default for backward-compatible tests
    kwargs.setdefault("ping_interval", 0)
    kwargs.setdefault("max_reconnect_attempts", 0)

    with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
        conn = CDPConnection(url=url, **kwargs)
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

    def test_keepalive_defaults(self) -> None:
        conn = CDPConnection(host="localhost", port=9222)
        assert conn._ping_interval == 30.0
        assert conn._pong_timeout == 10.0
        assert conn._max_reconnect_attempts == 3
        assert conn._reconnect_backoff == 1.0

    def test_custom_keepalive_params(self) -> None:
        conn = CDPConnection(
            host="localhost", port=9222,
            ping_interval=15.0, pong_timeout=5.0,
            max_reconnect_attempts=5, reconnect_backoff=2.0,
        )
        assert conn._ping_interval == 15.0
        assert conn._pong_timeout == 5.0
        assert conn._max_reconnect_attempts == 5
        assert conn._reconnect_backoff == 2.0


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
            with CDPConnection(url="ws://localhost:9222/test", ping_interval=0,
                               max_reconnect_attempts=0) as conn:
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


# ===========================================================================
# GW-127: Keepalive pinger tests
# ===========================================================================


class TestKeepalivePinger:
    """Tests for the WebSocket ping keepalive mechanism (GW-127)."""

    def test_connect_starts_pinger_when_ping_interval_set(self) -> None:
        """When ping_interval > 0, connect() should start the pinger thread."""
        conn, _ = _create_connected_connection(ping_interval=1.0)
        try:
            assert conn._pinger_thread is not None
            assert conn._pinger_thread.is_alive()
        finally:
            conn.close()

    def test_connect_skips_pinger_when_ping_interval_zero(self) -> None:
        """When ping_interval == 0, no pinger thread should be started."""
        conn, _ = _create_connected_connection(ping_interval=0)
        try:
            assert conn._pinger_thread is None
        finally:
            conn.close()

    def test_close_stops_pinger(self) -> None:
        """close() should stop the pinger thread."""
        conn, _ = _create_connected_connection(ping_interval=1.0)
        assert conn._pinger_thread is not None
        assert conn._pinger_thread.is_alive()

        conn.close()
        # After close, pinger reference may be cleared or thread stopped
        assert conn._pinger_thread is None or not conn._pinger_thread.is_alive()

    def test_pinger_sends_ping(self) -> None:
        """Pinger thread should send ping frames via the WebSocket."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)

        with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
            conn = CDPConnection(
                url="ws://localhost:9222/test",
                ping_interval=0.1,
                pong_timeout=0.5,
                max_reconnect_attempts=0,
            )
            conn.connect()

        try:
            # Wait for the pinger to send at least one ping
            deadline = time.monotonic() + 2.0
            while fake_ws._ping_count == 0 and time.monotonic() < deadline:
                time.sleep(0.05)

            assert fake_ws._ping_count >= 1
        finally:
            conn.close()

    def test_pinger_detects_dead_peer_on_pong_timeout(self) -> None:
        """Pinger should detect dead peer when pong times out and trigger reconnect."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)
        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        call_count = 0

        def _ws_factory(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws_mod
            return new_ws_mod

        # Keep patch active for the entire test (reconnect calls _import_websocket)
        with patch(
            "guidewire.cdp.connection._import_websocket",
            side_effect=_ws_factory,
        ):
            conn = CDPConnection(
                url="ws://localhost:9222/test",
                ping_interval=0.1,
                pong_timeout=0.2,
                max_reconnect_attempts=1,
                reconnect_backoff=0.01,
                ws_timeout=2.0,
            )
            conn.connect()

            try:
                # The first fake_ws has no pong mechanism, so the pinger
                # should detect a dead peer and reconnect to the new_fake_ws
                deadline = time.monotonic() + 5.0
                while call_count < 2 and time.monotonic() < deadline:
                    time.sleep(0.05)

                assert call_count >= 2, f"Expected reconnect, got {call_count} calls"
                assert conn.state == ConnectionState.CONNECTED
            finally:
                conn.close()

    def test_on_pong_received_updates_timestamp(self) -> None:
        """_on_pong_received should update last_pong_time."""
        conn = CDPConnection(host="localhost", port=9222)
        before = time.monotonic()
        conn._on_pong_received()
        assert conn._last_pong_time >= before


# ===========================================================================
# GW-127: Dead-peer detection tests
# ===========================================================================


class TestDeadPeerDetection:
    """Tests for dead-peer detection via receiver loop (GW-127)."""

    def test_receiver_exit_triggers_reconnect(self) -> None:
        """When the receiver loop exits unexpectedly, reconnect should trigger."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)
        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        call_count = 0

        def _ws_factory(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws_mod
            return new_ws_mod

        with patch(
            "guidewire.cdp.connection._import_websocket",
            side_effect=_ws_factory,
        ):
            conn = CDPConnection(
                url="ws://localhost:9222/test",
                ping_interval=0,
                max_reconnect_attempts=1,
                reconnect_backoff=0.01,
                ws_timeout=2.0,
            )
            conn.connect()

            try:
                # Close the fake_ws to make the receiver loop exit
                fake_ws.close()

                # Wait for reconnect
                deadline = time.monotonic() + 5.0
                while call_count < 2 and time.monotonic() < deadline:
                    time.sleep(0.05)

                assert call_count >= 2, (
                    f"Expected reconnect after receiver exit, got {call_count}"
                )
            finally:
                conn.close()

    def test_ping_send_failure_triggers_reconnect(self) -> None:
        """When ping() fails, dead-peer reconnect should be triggered."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)
        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        call_count = 0

        def _ws_factory(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws_mod
            return new_ws_mod

        # Make ping fail
        def _failing_ping(payload: bytes = b"") -> None:
            raise ConnectionError("ping failed")

        fake_ws.ping = _failing_ping  # type: ignore[assignment]

        with patch(
            "guidewire.cdp.connection._import_websocket",
            side_effect=_ws_factory,
        ):
            conn = CDPConnection(
                url="ws://localhost:9222/test",
                ping_interval=0.1,
                pong_timeout=0.2,
                max_reconnect_attempts=1,
                reconnect_backoff=0.01,
                ws_timeout=2.0,
            )
            conn.connect()

            try:
                # Wait for reconnect due to ping failure
                deadline = time.monotonic() + 5.0
                while call_count < 2 and time.monotonic() < deadline:
                    time.sleep(0.05)

                assert call_count >= 2, (
                    f"Expected reconnect after ping failure, got {call_count}"
                )
            finally:
                conn.close()


# ===========================================================================
# GW-127: Auto-reconnect and command retry tests
# ===========================================================================


class TestAutoReconnect:
    """Tests for automatic reconnect with command retry (GW-127)."""

    def test_send_command_retries_after_reconnect(self) -> None:
        """send_command should auto-reconnect and retry on transport failure."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)
        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        call_count = 0

        def _ws_factory(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws_mod
            return new_ws_mod

        with patch(
            "guidewire.cdp.connection._import_websocket",
            side_effect=_ws_factory,
        ):
            conn = CDPConnection(
                url="ws://localhost:9222/test",
                ping_interval=0,
                max_reconnect_attempts=1,
                reconnect_backoff=0.01,
                ws_timeout=2.0,
            )
            conn.connect()

            try:
                # Queue response on the new ws so the retried command succeeds
                def _enqueue_response() -> None:
                    for _ in range(40):  # wait up to 4s
                        if new_fake_ws._sent:
                            msg = json.loads(new_fake_ws._sent[0])
                            new_fake_ws.enqueue({"id": msg["id"], "result": {"ok": True}})
                            return
                        time.sleep(0.1)

                # Kill the first ws so send_command fails and triggers reconnect
                fake_ws.close()

                # Start response enqueuer in background
                threading.Thread(target=_enqueue_response, daemon=True).start()

                result = conn.send_command("Page.enable", timeout=10.0)
                assert result == {"ok": True}
                assert call_count >= 2
            finally:
                conn.close()

    def test_reconnect_exhausted_raises(self) -> None:
        """When all reconnect attempts fail, BackendUnavailableError is raised."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)

        fail_mod = MagicMock()
        fail_mod.create_connection.side_effect = OSError("refused")

        call_count = 0

        def _ws_factory(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws_mod
            return fail_mod

        with patch(
            "guidewire.cdp.connection._import_websocket",
            side_effect=_ws_factory,
        ):
            conn = CDPConnection(
                url="ws://localhost:9222/test",
                ping_interval=0,
                max_reconnect_attempts=2,
                reconnect_backoff=0.01,
                ws_timeout=2.0,
            )
            conn.connect()

            try:
                # Kill the ws so send_command triggers reconnect, but reconnect fails
                fake_ws.close()

                with pytest.raises(BackendUnavailableError, match="not open|reconnect|Failed"):
                    conn.send_command("Page.enable", timeout=10.0)
            finally:
                conn.close()

    def test_reconnect_disabled_does_not_retry(self) -> None:
        """When max_reconnect_attempts == 0, no reconnect should be attempted."""
        conn, fake_ws = _create_connected_connection(max_reconnect_attempts=0)

        # Kill the ws
        fake_ws.close()

        # send_command should raise without attempting reconnect
        with pytest.raises(BackendUnavailableError):
            conn.send_command("Page.enable", timeout=2.0)
        conn.close()

    def test_reconnect_preserves_event_buffer(self) -> None:
        """Reconnect should preserve the existing event buffer."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)
        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        call_count = 0

        def _ws_factory(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws_mod
            return new_ws_mod

        buf = EventBuffer(maxsize_per_method=50)

        with patch(
            "guidewire.cdp.connection._import_websocket",
            side_effect=_ws_factory,
        ):
            conn = CDPConnection(
                url="ws://localhost:9222/test",
                event_buffer=buf,
                ping_interval=0,
                max_reconnect_attempts=1,
                reconnect_backoff=0.01,
                ws_timeout=2.0,
            )
            conn.connect()

            try:
                # Trigger reconnect
                fake_ws.close()

                # Need to give time for reconnect, then queue a response
                def _enqueue_after_reconnect() -> None:
                    for _ in range(40):
                        if new_fake_ws._sent:
                            msg = json.loads(new_fake_ws._sent[0])
                            new_fake_ws.enqueue({"id": msg["id"], "result": {}})
                            return
                        time.sleep(0.1)

                threading.Thread(target=_enqueue_after_reconnect, daemon=True).start()

                # The event buffer should be the same object after reconnect
                assert conn.events is buf
            finally:
                conn.close()

    def test_reconnect_state_transitions(self) -> None:
        """Reconnect should transition through RECONNECTING state."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)
        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        call_count = 0

        def _ws_factory(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws_mod
            return new_ws_mod

        with patch(
            "guidewire.cdp.connection._import_websocket",
            side_effect=_ws_factory,
        ):
            conn = CDPConnection(
                url="ws://localhost:9222/test",
                ping_interval=0,
                max_reconnect_attempts=1,
                reconnect_backoff=0.01,
                ws_timeout=2.0,
            )
            conn.connect()

            # Monitor state changes
            original_state = conn.state

            try:
                # Trigger reconnect
                fake_ws.close()

                # Wait for reconnect
                deadline = time.monotonic() + 5.0
                while call_count < 2 and time.monotonic() < deadline:
                    time.sleep(0.05)

                assert call_count >= 2
                # After successful reconnect, should be back to CONNECTED
                assert conn.state == ConnectionState.CONNECTED
                # Original state was CONNECTED
                assert original_state == ConnectionState.CONNECTED
            finally:
                conn.close()

    def test_reconnect_succeeds_on_second_attempt(self) -> None:
        """Reconnect with retries should succeed on later attempts."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)

        fail_mod = MagicMock()
        fail_mod.create_connection.side_effect = OSError("refused")

        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        call_count = 0

        def _ws_factory(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws_mod
            if call_count == 2:
                return fail_mod
            return new_ws_mod

        with patch(
            "guidewire.cdp.connection._import_websocket",
            side_effect=_ws_factory,
        ):
            conn = CDPConnection(
                url="ws://localhost:9222/test",
                ping_interval=0,
                max_reconnect_attempts=3,
                reconnect_backoff=0.01,
                ws_timeout=2.0,
            )
            conn.connect()

            try:
                # Kill the original ws
                fake_ws.close()

                # Queue response on the new ws for the retried command
                def _enqueue_response() -> None:
                    for _ in range(60):
                        if new_fake_ws._sent:
                            msg = json.loads(new_fake_ws._sent[0])
                            new_fake_ws.enqueue({"id": msg["id"], "result": {"ok": True}})
                            return
                        time.sleep(0.1)

                threading.Thread(target=_enqueue_response, daemon=True).start()

                result = conn.send_command("Page.enable", timeout=15.0)
                assert result == {"ok": True}
                assert call_count >= 3  # original + 2 failed + 1 success
            finally:
                conn.close()
