"""Tests for CDP session management (session.py).

Validates :class:`CDPSession` — attach/detach lifecycle, session-scoped
command sending, state transitions, and error handling.
"""

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from guidewire.cdp._types import CDPTarget, SessionState
from guidewire.cdp.connection import CDPConnection
from guidewire.cdp.session import CDPSession
from guidewire.errors import GuidewireError

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


def _create_connected_connection() -> tuple[CDPConnection, FakeWebSocket]:
    """Create a connected CDPConnection with a FakeWebSocket."""
    fake_ws = FakeWebSocket()
    ws_mod = _fake_ws_module(fake_ws)

    with patch("guidewire.cdp.connection._import_websocket", return_value=ws_mod):
        conn = CDPConnection(url="ws://localhost:9222/devtools/browser")
        conn.connect()

    return conn, fake_ws


def _create_target(
    target_id: str = "target-1",
    target_type: str = "page",
) -> CDPTarget:
    """Create a test CDPTarget."""
    return CDPTarget(
        id=target_id,
        type=target_type,
        title="Test Page",
        url="https://example.com",
        web_socket_debugger_url=f"ws://localhost:9222/devtools/page/{target_id}",
    )


def _attach_session(
    session: CDPSession,
    fake_ws: FakeWebSocket,
    session_id: str = "session-abc",
) -> None:
    """Attach a session by enqueueing the attach response."""
    # Enqueue response for Target.attachToTarget
    threading.Timer(
        0.05,
        lambda: fake_ws.enqueue(
            {
                "id": 1,
                "result": {"sessionId": session_id},
            }
        ),
    ).start()
    session.attach()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestCDPSessionInit:
    """Tests for CDPSession construction."""

    def test_initial_state(self) -> None:
        conn, _ = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        assert session.state == SessionState.DETACHED
        assert session.session_id is None
        assert not session.is_attached

    def test_target_stored(self) -> None:
        conn, _ = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        assert session.target.id == "target-1"
        assert session.target.type == "page"


# ---------------------------------------------------------------------------
# Attach
# ---------------------------------------------------------------------------


class TestCDPSessionAttach:
    """Tests for attach."""

    def test_attach_success(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Queue the attach response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "result": {"sessionId": "sess-123"},
                }
            ),
        ).start()

        session_id = session.attach()
        assert session_id == "sess-123"
        assert session.state == SessionState.ATTACHED
        assert session.session_id == "sess-123"
        assert session.is_attached

        # Verify the command was sent
        sent = fake_ws.sent_messages
        assert len(sent) >= 1
        attach_cmd = sent[0]
        assert attach_cmd["method"] == "Target.attachToTarget"
        assert attach_cmd["params"]["targetId"] == "target-1"
        assert attach_cmd["params"]["flatten"] is True

    def test_attach_no_flatten(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "result": {"sessionId": "sess-456"},
                }
            ),
        ).start()

        session.attach(flatten=False)

        sent = fake_ws.sent_messages
        attach_cmd = sent[0]
        assert attach_cmd["params"]["flatten"] is False

    def test_attach_already_attached_raises(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        with pytest.raises(GuidewireError, match="already attached"):
            session.attach()

    def test_attach_failure_resets_state(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Queue an error response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "error": {"code": -32000, "message": "Target not found"},
                }
            ),
        ).start()

        with pytest.raises(GuidewireError):
            session.attach()

        assert session.state == SessionState.DETACHED
        assert session.session_id is None

    def test_attach_no_session_id_in_response_raises(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Response without sessionId
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 1, "result": {}}),
        ).start()

        with pytest.raises(GuidewireError, match="no sessionId"):
            session.attach()

        assert session.state == SessionState.DETACHED


# ---------------------------------------------------------------------------
# Detach
# ---------------------------------------------------------------------------


class TestCDPSessionDetach:
    """Tests for detach."""

    def test_detach_success(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        # Queue the detach response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {}}),
        ).start()

        session.detach()

        assert session.state == SessionState.DETACHED
        assert session.session_id is None
        assert not session.is_attached

        # Verify detach command was sent
        sent = fake_ws.sent_messages
        detach_cmd = sent[-1]
        assert detach_cmd["method"] == "Target.detachFromTarget"
        assert detach_cmd["params"]["sessionId"] == "session-abc"

    def test_detach_not_attached_raises(self) -> None:
        conn, _ = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        with pytest.raises(GuidewireError, match="not attached"):
            session.detach()

    def test_detach_failure_still_resets_state(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        # Queue an error for detach
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 2,
                    "error": {"code": -32000, "message": "Session not found"},
                }
            ),
        ).start()

        # detach should still reset state even on error
        session.detach()
        assert session.state == SessionState.DETACHED
        assert session.session_id is None


# ---------------------------------------------------------------------------
# Send command
# ---------------------------------------------------------------------------


class TestCDPSessionSendCommand:
    """Tests for session-scoped send_command."""

    def test_send_command_success(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-send")

        # Queue response for the scoped command
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 2,
                    "result": {"frameId": "main"},
                }
            ),
        ).start()

        result = session.send_command("Page.navigate", {"url": "https://example.com"})
        assert result == {"frameId": "main"}

        sent = fake_ws.sent_messages
        scoped_cmd = sent[-1]
        assert scoped_cmd["method"] == "Page.navigate"
        assert scoped_cmd["params"]["sessionId"] == "sess-send"
        assert scoped_cmd["params"]["url"] == "https://example.com"

    def test_send_command_no_params(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws, session_id="sess-send")

        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {}}),
        ).start()

        result = session.send_command("Page.enable")
        assert result == {}

        sent = fake_ws.sent_messages
        scoped_cmd = sent[-1]
        assert scoped_cmd["params"]["sessionId"] == "sess-send"

    def test_send_command_not_attached_raises(self) -> None:
        conn, _ = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        with pytest.raises(GuidewireError, match="not attached"):
            session.send_command("Page.enable")

    def test_send_command_with_custom_timeout(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {"data": True}}),
        ).start()

        result = session.send_command("Test.method", timeout=5.0)
        assert result == {"data": True}


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


class TestCDPSessionClose:
    """Tests for close."""

    def test_close_detaches_if_attached(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        _attach_session(session, fake_ws)

        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue({"id": 2, "result": {}}),
        ).start()

        session.close()
        assert session.state == SessionState.DETACHED

    def test_close_is_safe_when_detached(self) -> None:
        conn, _ = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        session.close()  # should not raise
        assert session.state == SessionState.DETACHED


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestCDPSessionContextManager:
    """Tests for context manager usage."""

    def test_context_manager_attach_detach(self) -> None:
        conn, fake_ws = _create_connected_connection()
        target = _create_target()
        session = CDPSession(connection=conn, target=target)

        # Enqueue attach response
        threading.Timer(
            0.05,
            lambda: fake_ws.enqueue(
                {
                    "id": 1,
                    "result": {"sessionId": "sess-ctx"},
                }
            ),
        ).start()

        with session:
            assert session.is_attached
            assert session.session_id == "sess-ctx"

            # Enqueue detach response
            threading.Timer(
                0.05,
                lambda: fake_ws.enqueue({"id": 2, "result": {}}),
            ).start()

        assert session.state == SessionState.DETACHED
