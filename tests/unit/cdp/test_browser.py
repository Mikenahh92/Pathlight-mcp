"""Tests for CDP browser connection manager (browser.py).

Validates :class:`CDPBrowser` — target discovery, session management,
connection lifecycle, and reconnection.
"""

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from guidewire.cdp._types import CDPTarget, ConnectionState
from guidewire.cdp.browser import CDPBrowser
from guidewire.cdp.session import CDPSession
from guidewire.errors import BackendUnavailableError, GuidewireError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """Minimal fake WebSocket for testing."""

    def __init__(self) -> None:
        self._queue: list[str] = []
        self._sent: list[str] = []
        self._closed = False
        self._lock = threading.Lock()
        self._has_data = threading.Condition(self._lock)

    def enqueue(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._queue.append(json.dumps(data))
            self._has_data.notify()

    def recv(self) -> str:
        with self._lock:
            while not self._queue and not self._closed:
                if not self._has_data.wait(timeout=0.5):
                    continue
            if self._closed and not self._queue:
                raise ConnectionError("WebSocket is closed")
            return self._queue.pop(0)

    def send(self, payload: str) -> None:
        if self._closed:
            raise ConnectionError("WebSocket is closed")
        self._sent.append(payload)

    def close(self) -> None:
        self._closed = True

    @property
    def sent_messages(self) -> list[dict[str, Any]]:
        return [json.loads(s) for s in self._sent]


def _fake_ws_module(fake_ws: FakeWebSocket) -> MagicMock:
    mod = MagicMock()
    mod.create_connection.return_value = fake_ws
    return mod


def _create_connected_browser() -> tuple[CDPBrowser, FakeWebSocket]:
    """Create a CDPBrowser with a connected FakeWebSocket."""
    fake_ws = FakeWebSocket()
    ws_mod = _fake_ws_module(fake_ws)

    with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
        browser = CDPBrowser(host="localhost", port=9222)
        browser.connect()

    return browser, fake_ws


def _make_target_json(
    targets: list[dict[str, Any]] | None = None,
) -> bytes:
    """Create JSON bytes for /json/list response."""
    if targets is None:
        targets = [
            {
                "id": "page-1",
                "type": "page",
                "title": "Test Page",
                "url": "https://example.com",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/page-1",
            },
            {
                "id": "sw-1",
                "type": "service_worker",
                "title": "SW",
                "url": "https://example.com/sw.js",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/service_worker/sw-1",
            },
        ]
    return json.dumps(targets).encode()


def _create_target(target_id: str = "page-1") -> CDPTarget:
    return CDPTarget(
        id=target_id,
        type="page",
        title="Test Page",
        url="https://example.com",
        web_socket_debugger_url=f"ws://localhost:9222/devtools/page/{target_id}",
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestCDPBrowserInit:
    """Tests for CDPBrowser construction."""

    def test_default_attributes(self) -> None:
        browser = CDPBrowser()
        assert browser.host == "localhost"
        assert browser.port == 9222
        assert browser.state == ConnectionState.DISCONNECTED
        assert not browser.is_connected

    def test_custom_host_port(self) -> None:
        browser = CDPBrowser(host="192.168.1.1", port=9333)
        assert browser.host == "192.168.1.1"
        assert browser.port == 9333


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestCDPBrowserLifecycle:
    """Tests for connect/close lifecycle."""

    def test_connect_sets_connected_state(self) -> None:
        browser, _ = _create_connected_browser()
        assert browser.state == ConnectionState.CONNECTED
        assert browser.is_connected

    def test_close_sets_closed_state(self) -> None:
        browser, _ = _create_connected_browser()
        browser.close()
        assert browser.state == ConnectionState.CLOSED
        assert not browser.is_connected

    def test_close_is_idempotent(self) -> None:
        browser, _ = _create_connected_browser()
        browser.close()
        browser.close()  # should not raise
        assert browser.state == ConnectionState.CLOSED

    def test_connect_failure(self) -> None:
        ws_mod = MagicMock()
        ws_mod.create_connection.side_effect = OSError("refused")

        with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
            browser = CDPBrowser(host="localhost", port=9222)
            with pytest.raises(BackendUnavailableError):
                browser.connect()

        assert browser.state == ConnectionState.DISCONNECTED

    def test_context_manager(self) -> None:
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)

        with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
            with CDPBrowser(host="localhost", port=9222) as browser:
                assert browser.is_connected
            assert browser.state == ConnectionState.CLOSED

    def test_connect_when_already_connected(self) -> None:
        browser, _ = _create_connected_browser()
        browser.connect()  # should not raise or reconnect
        assert browser.is_connected


# ---------------------------------------------------------------------------
# Target discovery
# ---------------------------------------------------------------------------


class TestCDPBrowserTargetDiscovery:
    """Tests for target discovery via /json/list."""

    def test_list_targets(self) -> None:
        browser, _ = _create_connected_browser()
        target_data = _make_target_json()

        with patch("guidewire.cdp.browser.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = target_data
            mock_urlopen.return_value = mock_response

            targets = browser.list_targets()

        assert len(targets) == 2
        assert targets[0].id == "page-1"
        assert targets[0].type == "page"
        assert targets[1].id == "sw-1"
        assert targets[1].type == "service_worker"

    def test_list_targets_empty(self) -> None:
        browser, _ = _create_connected_browser()

        with patch("guidewire.cdp.browser.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"[]"
            mock_urlopen.return_value = mock_response

            targets = browser.list_targets()

        assert targets == []

    def test_list_targets_http_failure(self) -> None:
        browser, _ = _create_connected_browser()

        with patch("guidewire.cdp.browser.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = URLError("connection refused")

            with pytest.raises(BackendUnavailableError, match="Failed to discover"):
                browser.list_targets()

    def test_list_targets_filtered_by_type(self) -> None:
        browser, _ = _create_connected_browser()
        target_data = _make_target_json()

        with patch("guidewire.cdp.browser.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = target_data
            mock_urlopen.return_value = mock_response

            targets = browser.list_targets(target_type="page")

        assert len(targets) == 1
        assert targets[0].id == "page-1"
        assert targets[0].type == "page"

    def test_list_targets_filtered_no_match(self) -> None:
        browser, _ = _create_connected_browser()
        target_data = _make_target_json()

        with patch("guidewire.cdp.browser.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = target_data
            mock_urlopen.return_value = mock_response

            targets = browser.list_targets(target_type="iframe")

        assert targets == []

    def test_list_targets_invalid_json(self) -> None:
        browser, _ = _create_connected_browser()

        with patch("guidewire.cdp.browser.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"not json"
            mock_urlopen.return_value = mock_response

            with pytest.raises(BackendUnavailableError, match="Invalid response"):
                browser.list_targets()

    def test_get_target_found(self) -> None:
        browser, _ = _create_connected_browser()

        with patch("guidewire.cdp.browser.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = _make_target_json()
            mock_urlopen.return_value = mock_response

            target = browser.get_target("page-1")

        assert target is not None
        assert target.id == "page-1"

    def test_get_target_not_found(self) -> None:
        browser, _ = _create_connected_browser()

        with patch("guidewire.cdp.browser.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = _make_target_json()
            mock_urlopen.return_value = mock_response

            target = browser.get_target("nonexistent")

        assert target is None


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class TestCDPBrowserSessionManagement:
    """Tests for attach/detach session management."""

    def _attach_session(
        self,
        browser: CDPBrowser,
        fake_ws: FakeWebSocket,
        target: CDPTarget,
        session_id: str = "session-1",
        cmd_id: int = 1,
    ) -> CDPSession:
        """Attach a session to the browser."""
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": cmd_id,
                    "result": {"sessionId": session_id},
                }
            ),
        ).start()
        return browser.attach(target)

    def test_attach_creates_session(self) -> None:
        browser, fake_ws = _create_connected_browser()
        target = _create_target()

        session = self._attach_session(browser, fake_ws, target)

        assert isinstance(session, CDPSession)
        assert session.is_attached
        assert session.session_id == "session-1"

    def test_attach_tracks_session(self) -> None:
        browser, fake_ws = _create_connected_browser()
        target = _create_target()

        session = self._attach_session(browser, fake_ws, target)

        assert browser.get_session("page-1") is session
        assert len(browser.active_sessions) == 1

    def test_detach_removes_session(self) -> None:
        browser, fake_ws = _create_connected_browser()
        target = _create_target()

        self._attach_session(browser, fake_ws, target)

        # Queue detach response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {}}),
        ).start()
        browser.detach("page-1")

        assert browser.get_session("page-1") is None
        assert len(browser.active_sessions) == 0

    def test_detach_unknown_target_raises(self) -> None:
        browser, _ = _create_connected_browser()

        with pytest.raises(GuidewireError, match="No active session"):
            browser.detach("nonexistent")

    def test_attach_not_connected_raises(self) -> None:
        browser = CDPBrowser()
        target = _create_target()

        with pytest.raises(GuidewireError, match="not open"):
            browser.attach(target)

    def test_multiple_sessions(self) -> None:
        browser, fake_ws = _create_connected_browser()

        target1 = _create_target("page-1")
        target2 = _create_target("page-2")

        # Attach first session
        self._attach_session(browser, fake_ws, target1, "sess-1", cmd_id=1)

        # Attach second session
        self._attach_session(browser, fake_ws, target2, "sess-2", cmd_id=2)

        assert len(browser.active_sessions) == 2
        assert browser.get_session("page-1") is not None
        assert browser.get_session("page-2") is not None

    def test_close_detaches_all_sessions(self) -> None:
        browser, fake_ws = _create_connected_browser()
        target = _create_target()

        self._attach_session(browser, fake_ws, target)

        # Queue detach response for close
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {}}),
        ).start()

        browser.close()
        assert len(browser.active_sessions) == 0


# ---------------------------------------------------------------------------
# Send command
# ---------------------------------------------------------------------------


class TestCDPBrowserSendCommand:
    """Tests for root-level send_command."""

    def test_send_command_success(self) -> None:
        browser, fake_ws = _create_connected_browser()

        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 1, "result": {"discover": True}}),
        ).start()

        result = browser.send_command("Target.setDiscoverTargets", {"discover": True})
        assert result == {"discover": True}

    def test_send_command_not_connected_raises(self) -> None:
        browser = CDPBrowser()

        with pytest.raises(GuidewireError, match="not open"):
            browser.send_command("Target.setDiscoverTargets")


# ---------------------------------------------------------------------------
# Reconnection
# ---------------------------------------------------------------------------


class TestCDPBrowserReconnect:
    """Tests for reconnection."""

    def test_reconnect_reestablishes_connection(self) -> None:
        browser, _fake_ws = _create_connected_browser()
        assert browser.is_connected

        # Set up for reconnection — need a new fake ws for the reconnect
        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        with patch("guidewire.cdp.connection._import_websocket", return_value=new_ws_mod):
            browser.reconnect()

        assert browser.is_connected

    def test_reconnect_reattaches_sessions(self) -> None:
        # Use a short ws_timeout so re-attach doesn't hang for 30s
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)

        with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
            browser = CDPBrowser(host="localhost", port=9222, ws_timeout=2.0)
            browser.connect()

        target = _create_target()

        # Attach a session first
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "result": {"sessionId": "session-old"},
                }
            ),
        ).start()
        browser.attach(target)
        assert len(browser.active_sessions) == 1

        # Set up for reconnection with a response that enqueues after
        # a command is sent on the new ws
        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        def _enqueue_after_command() -> None:
            """Wait until a command is sent on the new ws, then respond."""
            import time

            for _ in range(40):  # wait up to 4s
                if new_fake_ws._sent:
                    break
                time.sleep(0.1)
            new_fake_ws.enqueue(
                {
                    "id": 1,
                    "result": {"sessionId": "session-new"},
                }
            )

        with patch("guidewire.cdp.connection._import_websocket", return_value=new_ws_mod):
            # Start response enqueuer in background
            threading.Thread(target=_enqueue_after_command, daemon=True).start()
            browser.reconnect()

        assert browser.is_connected
        assert len(browser.active_sessions) == 1
        new_session = browser.get_session("page-1")
        assert new_session is not None
        assert new_session.session_id == "session-new"

    def test_reconnect_failure_raises(self) -> None:
        browser, _ = _create_connected_browser()

        ws_mod = MagicMock()
        ws_mod.create_connection.side_effect = OSError("refused")

        with (
            patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod),
            pytest.raises(BackendUnavailableError),
        ):
            browser.reconnect()

    def test_reconnect_with_retry_succeeds_on_second_attempt(self) -> None:
        browser, _ = _create_connected_browser()

        fail_mod = MagicMock()
        fail_mod.create_connection.side_effect = OSError("refused")

        success_ws = FakeWebSocket()
        success_mod = _fake_ws_module(success_ws)

        call_count = 0

        def _ws_factory(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return fail_mod
            return success_mod

        with patch("guidewire.cdp.connection._import_websocket", side_effect=_ws_factory):
            browser.reconnect(max_retries=1, backoff_delay=0.01)

        assert browser.is_connected
        assert call_count == 2

    def test_reconnect_with_retry_exhausted_raises(self) -> None:
        browser, _ = _create_connected_browser()

        ws_mod = MagicMock()
        ws_mod.create_connection.side_effect = OSError("refused")

        with (
            patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod),
            pytest.raises(BackendUnavailableError),
        ):
            browser.reconnect(max_retries=2, backoff_delay=0.01)

    def test_reconnect_retry_succeeds_on_first_attempt(self) -> None:
        browser, _ = _create_connected_browser()

        new_fake_ws = FakeWebSocket()
        new_ws_mod = _fake_ws_module(new_fake_ws)

        with patch("guidewire.cdp.connection._import_websocket", return_value=new_ws_mod):
            browser.reconnect(max_retries=3, backoff_delay=0.01)

        assert browser.is_connected


# ---------------------------------------------------------------------------
# WebSocket URL resolution (GW-112 regression fix)
# ---------------------------------------------------------------------------


class TestWSUrlResolution:
    """Tests for dynamic WebSocket URL resolution from /json/version.

    Validates the fix for the CDP WebSocket handshake 404 regression on
    newer Chromium versions that require a browser target ID in the WS URL.
    """

    def test_connect_uses_version_endpoint_ws_url(self) -> None:
        """connect() should use webSocketDebuggerUrl from /json/version."""
        version_data = json.dumps(
            {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc-123"}
        ).encode()

        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)

        with (
            patch("guidewire.cdp.browser.urlopen") as mock_urlopen,
            patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod),
        ):
            mock_response = MagicMock()
            mock_response.read.return_value = version_data
            mock_urlopen.return_value = mock_response

            browser = CDPBrowser(host="localhost", port=9222)
            browser.connect()

        # Verify the connection URL came from /json/version
        assert browser.connection is not None
        assert browser.connection.url == "ws://localhost:9222/devtools/browser/abc-123"

    def test_connect_fallback_on_version_endpoint_failure(self) -> None:
        """connect() should fall back to legacy URL when /json/version fails."""
        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)

        with (
            patch("guidewire.cdp.browser.urlopen") as mock_urlopen,
            patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod),
        ):
            # /json/version returns an error
            mock_urlopen.side_effect = URLError("not found")

            browser = CDPBrowser(host="localhost", port=9222)
            browser.connect()

        # Verify fallback to legacy URL
        assert browser.connection is not None
        assert browser.connection.url == "ws://localhost:9222/devtools/browser"

    def test_connect_fallback_on_empty_ws_url(self) -> None:
        """connect() should fall back when /json/version returns empty WS URL."""
        version_data = json.dumps({"webSocketDebuggerUrl": ""}).encode()

        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)

        with (
            patch("guidewire.cdp.browser.urlopen") as mock_urlopen,
            patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod),
        ):
            mock_response = MagicMock()
            mock_response.read.return_value = version_data
            mock_urlopen.return_value = mock_response

            browser = CDPBrowser(host="localhost", port=9222)
            browser.connect()

        assert browser.connection is not None
        assert browser.connection.url == "ws://localhost:9222/devtools/browser"

    def test_connect_uses_custom_host_and_port(self) -> None:
        """connect() should respect custom host/port in /json/version request."""
        version_data = json.dumps(
            {"webSocketDebuggerUrl": "ws://192.168.1.50:9333/devtools/browser/xyz"}
        ).encode()

        fake_ws = FakeWebSocket()
        ws_mod = _fake_ws_module(fake_ws)

        with (
            patch("guidewire.cdp.browser.urlopen") as mock_urlopen,
            patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod),
        ):
            mock_response = MagicMock()
            mock_response.read.return_value = version_data
            mock_urlopen.return_value = mock_response

            browser = CDPBrowser(host="192.168.1.50", port=9333)
            browser.connect()

        # Verify the URL requested was for the correct host:port
        call_args = mock_urlopen.call_args[0][0]
        assert "192.168.1.50:9333" in call_args
        assert browser.connection.url == "ws://192.168.1.50:9333/devtools/browser/xyz"
